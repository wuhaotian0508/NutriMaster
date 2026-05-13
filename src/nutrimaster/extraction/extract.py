"""
extract.py — 基于内联函数调用（Function Calling）的基因提取模块。

单步 API 调用：
    extract_all_genes → 提取 Title/Journal/DOI + Common_Genes/Pathway_Genes/Regulation_Genes

函数调用 schema 直接从 schema JSON 文件动态构建。
"""

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from .config import (
    EXTRACTOR_MODEL, TEMPERATURE,
    PROMPT_PATH, SCHEMA_PATH, REPORTS_DIR,
    get_openai_client,
)
from .text_utils import preprocess_md_for_llm
from .token_tracker import TokenTracker
from .utils import (
    GENE_ARRAY_KEYS, ensure_list, get_gene_name,
    safe_parse_json, stem_to_dirname, prepare_deepseek_params,
)


# ─── Load prompt (cached) ───────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_prompt():
    """读取 prompt 模板文件（带缓存）。

    使用 @lru_cache 缓存，整个进程只读一次文件，后续调用直接返回缓存内容。

    Returns:
        str: prompt 模板的完整文本内容
    """
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ─── Build FC schemas from schema JSON ──────────────────────────────────────

def _schema_props_to_fc(gene_def: dict) -> dict:
    """把 schema JSON 中基因类型的 properties 转换为 OpenAI Function Calling 格式。

    将 schema 中各字段的 description 提取出来，构造 FC 所需的
    {"type": "string", "description": "..."} 格式。

    Args:
        gene_def: schema 中某个基因类型（如 CommonGene）的定义 dict

    Returns:
        dict: FC 格式的 properties 字典
    """
    fc_props = {}
    for field_name, field_schema in gene_def.get("properties", {}).items():
        desc = field_schema.get("description", "")
        fc_props[field_name] = {"type": "string", "description": desc}
    return fc_props


def _build_extract_all_schema(schema_data: dict) -> dict:
    """从 schema JSON 构建 extract_all_genes 的 Function Calling schema。

    读取 MultipleGeneExtraction 下的 CommonGene/PathwayGene/RegulationGene 定义，
    组装成一个包含 Title/Journal/DOI + 三个基因数组的 FC tool schema。

    Args:
        schema_data: 从 schema JSON 文件加载的完整数据

    Returns:
        dict: 符合 OpenAI tools 格式的 function calling schema
    """
    multi = schema_data.get("MultipleGeneExtraction", {})
    defs = multi.get("$defs", {})

    common_props = _schema_props_to_fc(defs.get("CommonGene", {}))
    pathway_props = _schema_props_to_fc(defs.get("PathwayGene", {}))
    regulation_props = _schema_props_to_fc(defs.get("RegulationGene", {}))

    return {
        "type": "function",
        "function": {
            "name": "extract_all_genes",
            "description": (
                "Extract paper metadata and ALL core genes at once, classified into "
                "three arrays: Common_Genes, Pathway_Genes, Regulation_Genes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "Title": {"type": "string", "description": "Full paper title."},
                    "Journal": {"type": "string", "description": "Journal name."},
                    "DOI": {"type": "string", "description": "Pure DOI string, no URL prefix."},
                    "Common_Genes": {
                        "type": "array",
                        "description": "Common genes (general function in nutrient metabolism).",
                        "items": {"type": "object", "properties": common_props},
                    },
                    "Pathway_Genes": {
                        "type": "array",
                        "description": "Pathway genes (biosynthetic/metabolic enzyme genes).",
                        "items": {"type": "object", "properties": pathway_props},
                    },
                    "Regulation_Genes": {
                        "type": "array",
                        "description": "Regulation genes (TFs, signaling, regulators).",
                        "items": {"type": "object", "properties": regulation_props},
                    },
                },
                "required": ["Title", "Journal", "DOI", "Common_Genes", "Pathway_Genes", "Regulation_Genes"],
            },
        },
    }


@lru_cache(maxsize=1)
def _load_extract_all_schema() -> dict:
    """加载并构建 extract_all_genes schema（带缓存）。

    从 SCHEMA_PATH 读取 JSON schema 文件，调用 _build_extract_all_schema 构建
    FC schema。使用 @lru_cache 缓存，schema 文件只读一次。

    Returns:
        dict: 构建好的 extract_all_genes FC schema
    """
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_data = json.load(f)
    return _build_extract_all_schema(schema_data)


def _handle_extract_all(arguments: dict) -> Tuple[dict, dict]:
    """处理 extract_all_genes 的 API 返回结果。

    把 LLM 返回的 JSON arguments 解析成两个结构：
    - extraction_dict: 包含 Title/Journal/DOI + 三个基因数组的完整提取结果
    - gene_dict: {基因名: 分类} 的映射，如 {"SlMYB12": "Regulation", "CHS": "Pathway"}

    Args:
        arguments: LLM 函数调用返回的 JSON 参数字典

    Returns:
        tuple[dict, dict]: (extraction_dict, gene_dict)
    """
    extraction = {
        "Title": arguments.get("Title", "NA"),
        "Journal": arguments.get("Journal", "NA"),
        "DOI": arguments.get("DOI", "NA"),
        "Common_Genes": [],
        "Pathway_Genes": [],
        "Regulation_Genes": [],
    }

    gene_dict = {}
    for arr_key, cat in GENE_ARRAY_KEYS:
        genes_arr = ensure_list(arguments.get(arr_key, []))
        extraction[arr_key] = genes_arr
        for g in genes_arr:
            if isinstance(g, dict):
                gname = get_gene_name(g)
                if gname:
                    gene_dict[gname] = cat

    return extraction, gene_dict


# ═══════════════════════════════════════════════════════════════════════════════
#  Core extraction: single-step API call
# ═══════════════════════════════════════════════════════════════════════════════

def _call_extract_api(
    api_client,
    model: str,
    content: str,
    name: str,
    extract_all_schema: dict,
    tracker: TokenTracker,
):
    """调用一次 LLM API 提取论文中的所有基因信息（单步提取）。

    流程：构建 messages → 调用 API（带 FC tool）→ 解析返回的 JSON → 统计基因数量。
    对 DeepSeek V4 模型会自动调整参数（移除不兼容参数，添加 thinking 配置）。

    Args:
        api_client: OpenAI 客户端实例
        model: 模型名称
        content: 预处理后的论文 Markdown 文本
        name: 文件名（用于日志和 token 追踪）
        extract_all_schema: extract_all_genes 的 FC schema
        tracker: token 用量追踪器

    Returns:
        tuple: (extraction_dict, gene_dict, success)
            - extraction_dict: 包含论文元数据和基因数组的提取结果
            - gene_dict: {基因名: 分类} 映射
            - success: 是否成功提取
    """
    api_kwargs = dict(temperature=TEMPERATURE, max_tokens=16384)
    base_prompt = _load_prompt()

    user_parts = [
        "Analyze this literature and extract all nutrient metabolism gene information.\n",
        "Extract the paper metadata (Title, Journal, DOI) and ALL core genes at once,",
        "classified into Common_Genes, Pathway_Genes, and Regulation_Genes arrays.\n",
        f"Paper content:\n\n{content}",
    ]

    messages = [
        {"role": "system", "content": base_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]

    print(f"    🔵 API Call: extract_all_genes ({model})...")

    try:
        # 为 DeepSeek V4 准备参数（移除不兼容参数，添加 thinking 配置）
        api_kwargs = prepare_deepseek_params(model, api_kwargs)
        response = api_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[extract_all_schema],
            tool_choice={"type": "function", "function": {"name": "extract_all_genes"}},
            **api_kwargs,
        )
    except Exception as e:
        print(f"    ❌ [{model}] API error (extract_all): {e}")
        return None, None, False

    tracker.add(response, stage="extract", file=name)
    msg = response.choices[0].message

    if not msg.tool_calls:
        print(f"    ⚠️  [{model}] extract_all did not trigger tool call")
        return None, None, False

    tc = msg.tool_calls[0]
    parsed = safe_parse_json(tc.function.arguments, "extract_all")
    if parsed is None:
        print(f"    ❌ extract_all_genes JSON parse failed")
        return None, None, False

    extraction, gene_dict = _handle_extract_all(parsed)

    c = len(extraction.get("Common_Genes", []))
    p = len(extraction.get("Pathway_Genes", []))
    r = len(extraction.get("Regulation_Genes", []))
    total = c + p + r

    print(f"    📊 Genes: Common={c}, Pathway={p}, Regulation={r}, Total={total}")
    print(f"    📋 gene_dict: {gene_dict}")

    if total == 0:
        print(f"    ⚠️  All gene arrays empty")
        return extraction, {}, False

    return extraction, gene_dict, True


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def extract_paper(
    md_path,
    tracker: TokenTracker,
) -> Tuple[Optional[dict], Optional[dict]]:
    """从单篇论文中提取基因信息（对外主入口）。

    完整流程：
    1. 增量跳过：如果 extraction.json 已存在，直接读取返回（可设 FORCE_RERUN=1 强制重跑）
    2. 读取 Markdown → preprocess_md_for_llm() 预处理（去图片/URL + LLM 过滤 section）
    3. 调用 extraction API 提取所有基因
    4. 保存 extraction.json + gene_dict.json 到 reports 目录

    Args:
        md_path: 论文 Markdown 文件路径
        tracker: token 用量追踪器

    Returns:
        tuple[dict | None, dict | None]: (extraction_dict, gene_dict)
            - extraction_dict: 提取结果，包含论文元数据和基因数组
            - gene_dict: 基因名到分类的映射
            - 失败时返回 (None, None)
    """
    md_path = Path(md_path)
    name = md_path.name
    filename = md_path.stem

    # Incremental: skip if already extracted
    paper_dir = REPORTS_DIR / stem_to_dirname(filename)
    output_path = paper_dir / "extraction.json"
    if output_path.exists() and os.getenv("FORCE_RERUN") != "1":
        print(f"  ⏭️  Already exists, skip: {output_path}  (set FORCE_RERUN=1 to re-run)")
        with open(output_path, "r", encoding="utf-8") as f:
            extraction = json.load(f)
        gene_dict_path = paper_dir / "gene_dict.json"
        if gene_dict_path.exists():
            with open(gene_dict_path, "r", encoding="utf-8") as f:
                gene_dict = json.load(f)
        else:
            gene_dict = {}
            for arr_key, cat in GENE_ARRAY_KEYS:
                for g in extraction.get(arr_key, []):
                    if isinstance(g, dict) and g.get("Gene_Name"):
                        gene_dict[g["Gene_Name"]] = cat
        return extraction, gene_dict

    # Read and preprocess
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    original_len = len(content)
    content = preprocess_md_for_llm(content, tracker=tracker)
    print(f"  📏 Preprocessing: {original_len:,} → {len(content):,} chars (saved {original_len - len(content):,})")

    # Load schema
    extract_all_schema = _load_extract_all_schema()

    client = get_openai_client()

    print(f"  🔵 Using extraction API ({EXTRACTOR_MODEL})...")
    extraction, gene_dict, success = _call_extract_api(
        client, EXTRACTOR_MODEL, content, name, extract_all_schema, tracker,
    )

    if not success or extraction is None:
        print(f"  ⚠️  Extraction API failed: {name}")
        paper_dir.mkdir(parents=True, exist_ok=True)
        error_report = {
            "file": name,
            "error": "Extraction API failed",
            "timestamp": datetime.now().isoformat(),
        }
        with open(paper_dir / "extraction-error.json", "w", encoding="utf-8") as f:
            json.dump(error_report, f, indent=2, ensure_ascii=False)
        return None, None

    # Save results
    paper_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extraction, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Extraction saved: {output_path}")

    if gene_dict:
        with open(paper_dir / "gene_dict.json", "w", encoding="utf-8") as f:
            json.dump(gene_dict, f, indent=2, ensure_ascii=False)

    return extraction, gene_dict
