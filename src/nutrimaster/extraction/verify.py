"""
verify.py — 基因验证模块：内联函数调用 + 动态分批。

动态分批策略（每批默认 12 个基因）：
    < 12 个基因  → 1 批
    12-23 个基因 → 2 批
    24-35 个基因 → 3 批
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

from .config import (
    EXTRACTOR_MODEL, OUTPUT_DIR, REPORTS_DIR, PROCESSED_DIR,
    get_openai_client, read_markdown_bounded,
)
from .text_utils import preprocess_md_for_llm
from .token_tracker import TokenTracker
from .utils import (
    GENE_ARRAY_KEYS, ensure_list, safe_parse_json, stem_to_dirname,
    prepare_deepseek_params,
)

# ─── 可配置参数 ────────────────────────────────────────────────────────────
# 每批验证的基因数阈值，Admin Panel 可在运行时修改此值
GENES_PER_BATCH = int(os.getenv("VERIFY_GENES_PER_BATCH", "12"))


# ─── Verify FC schema (inline) ──────────────────────────────────────────────

VERIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "verify_all_genes",
        "description": "Submit verification results for ALL genes at once. For each gene, provide verdicts for each non-NA field.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_verdicts": {
                    "type": "array",
                    "description": "Verification results grouped by gene.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Gene_Name": {"type": "string", "description": "The gene name being verified."},
                            "field_verdicts": {
                                "type": "array",
                                "description": "Verification results for each field of this gene.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field_name": {"type": "string", "description": "The field name being verified."},
                                        "verdict": {
                                            "type": "string",
                                            "enum": ["SUPPORTED", "UNSUPPORTED", "UNCERTAIN"],
                                            "description": "Verification verdict.",
                                        },
                                        "reason": {"type": "string", "description": "Brief explanation for the verdict."},
                                    },
                                    "required": ["field_name", "verdict", "reason"],
                                },
                            },
                        },
                        "required": ["Gene_Name", "field_verdicts"],
                    },
                },
            },
            "required": ["gene_verdicts"],
        },
    },
}

VERIFY_SYSTEM_PROMPT = """You are a rigorous academic verification assistant specializing in plant molecular biology and metabolic biochemistry.

Your task: Given a scientific paper (Markdown) and extracted JSON fields for ALL genes, verify whether EACH field value is faithfully supported by the original paper.

### Verification criteria:
1. Core gene validity - Was this gene directly tested in the Results section?
2. Trait validity - Is the gene linked to a final nutrient product, not just a generic trait?
3. Directionality consistency - Do intermediate metabolite changes match the final product change?
4. Evidence alignment - Are claims backed by figures/tables in Results, not just Discussion?
5. Hallucination check - Do specific values (numbers, gene names, accession IDs, EC numbers, species names, etc.) actually appear in the paper?

### For EACH field, determine:
- SUPPORTED: The value is explicitly stated or directly inferable from the paper.
- UNSUPPORTED: The value CANNOT be found in or inferred from the paper (likely hallucination).
- UNCERTAIN: Partially related content exists, but the exact value is not clearly supported.

Be strict: if a specific number/ID is not in the paper, mark UNSUPPORTED.
Call the verify_all_genes function with your verification results for ALL genes at once."""


# ─── Helper functions ────────────────────────────────────────────────────────

def _extract_non_na_fields(gene_dict: dict) -> dict:
    """提取基因 dict 中所有字段，用于验证。

    返回基因字典的完整副本，不过滤任何值（包括 None、"NA" 字符串
    和列表中的 "NA" 元素），确保所有字段都被验证。

    Args:
        gene_dict: 单个基因的数据字典

    Returns:
        dict: 包含所有字段的字典
    """
    fields = {}
    for key, value in gene_dict.items():
        fields[key] = value
    return fields


def _build_genes_text(genes_with_info: list) -> str:
    """为一批基因构建验证用的文本描述。

    把每个基因的字段格式化为 Markdown 标题 + JSON 代码块，
    供 LLM 逐字段验证使用。

    Args:
        genes_with_info: [(gene_data, category), ...] 基因数据和分类的元组列表

    Returns:
        str: 格式化后的多基因文本描述
    """
    parts = []
    for i, (gene_data, category) in enumerate(genes_with_info, 1):
        gene_name = gene_data.get("Gene_Name", f"Gene_{i}")
        non_na = _extract_non_na_fields(gene_data)
        if not non_na:
            continue
        fields_json = json.dumps(non_na, indent=2, ensure_ascii=False)
        parts.append(
            f"### Gene #{i}: {gene_name} (Category: {category})\n"
            f"```json\n{fields_json}\n```"
        )
    return "\n\n".join(parts)


def _apply_corrections(gene_data: dict, field_verdicts_list: list) -> Tuple[dict, list]:
    """对 UNSUPPORTED 的字段自动修正为 "NA"。

    遍历 LLM 返回的 field_verdicts，找到 verdict=="UNSUPPORTED" 的字段，
    将其原始值替换为 "NA"，并记录修正历史（包含字段名、旧值、新值和原因）。

    Args:
        gene_data: 单个基因的原始数据字典
        field_verdicts_list: LLM 返回的字段验证结果列表，每项包含
                            field_name、verdict 和 reason

    Returns:
        tuple[dict, list]: (corrected_gene, corrections)
            - corrected_gene: 修正后的基因数据字典
            - corrections: 修正记录列表，每项包含 field、old_value、new_value、reason
    """
    corrections = []
    corrected_gene = dict(gene_data)

    verdict_map = {}
    for fv in field_verdicts_list:
        fname = fv.get("field_name", "")
        if fname:
            verdict_map[fname] = fv

    for field_name, verdict_info in verdict_map.items():
        verdict = verdict_info.get("verdict", "").upper()
        reason = verdict_info.get("reason", "")

        if verdict == "UNSUPPORTED":
            old_value = corrected_gene.get(field_name)
            corrected_gene[field_name] = "NA"
            corrections.append({
                "field": field_name,
                "old_value": old_value,
                "new_value": "NA",
                "reason": reason,
            })

    return corrected_gene, corrections


def _compute_batches(total_genes: int) -> List[Tuple[int, int]]:
    """动态分批策略：根据基因数量决定批次。

    阈值由模块级变量 GENES_PER_BATCH 控制（默认 12），可通过环境变量
    VERIFY_GENES_PER_BATCH 或在运行时修改。

    Args:
        total_genes: 待验证的基因总数

    Returns:
        list[tuple[int, int]]: [(start, end), ...] 索引范围列表，
                              每个元组表示一个批次的起止索引
    """
    threshold = GENES_PER_BATCH
    if total_genes < threshold:
        return [(0, total_genes)]

    num_batches = (total_genes // threshold) + 1
    batch_size = (total_genes + num_batches - 1) // num_batches
    batches = []
    for start in range(0, total_genes, batch_size):
        end = min(start + batch_size, total_genes)
        batches.append((start, end))
    return batches


def _handle_verify_all(arguments: dict) -> list:
    """处理 verify_all_genes 的 API 返回结果。

    从函数调用返回的参数中提取 gene_verdicts 列表。
    使用 ensure_list 进行防御性解析。

    Args:
        arguments: LLM 函数调用返回的 JSON 参数字典

    Returns:
        list: gene_verdicts 列表，每项包含 Gene_Name 和 field_verdicts
    """
    return ensure_list(arguments.get("gene_verdicts", []))


# ─── Core verify API call ────────────────────────────────────────────────────

def _call_verify_api(api_client, model_name: str, user_prompt: str,
                     file_name: str, tracker: TokenTracker):
    """单批次验证 API 调用。

    构建 messages → 调用 verify_all_genes FC tool → 解析返回的 gene_verdicts。
    对 DeepSeek V4 模型会自动调整参数。

    Args:
        api_client: OpenAI 客户端实例
        model_name: 模型名称
        user_prompt: 包含论文文本和待验证基因的用户提示
        file_name: 文件名标识（用于 token 追踪）
        tracker: token 用量追踪器

    Returns:
        tuple[list, bool]: (gene_verdicts_list, success)
            - gene_verdicts_list: 验证结果列表
            - success: 是否成功完成验证
    """
    messages = [
        {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    print(f"    🔵 API Call: verify_all_genes...")
    try:
        # 为 DeepSeek V4 准备参数
        verify_params = {
            "model": model_name,
            "messages": messages,
            "tools": [VERIFY_SCHEMA],
            "tool_choice": {"type": "function", "function": {"name": "verify_all_genes"}},
            "temperature": 0,
            "max_tokens": 16384,
        }
        verify_params = prepare_deepseek_params(model_name, verify_params)
        response = api_client.chat.completions.create(**verify_params)
    except MemoryError:
        raise
    except Exception as e:
        print(f"    ❌ [{model_name}] API error (verify): {e}")
        return [], False

    tracker.add(response, stage="verify", file=file_name)
    msg = response.choices[0].message

    if not msg.tool_calls:
        print(f"    ⚠️  [{model_name}] verify did not trigger tool call")
        return [], False

    tc = msg.tool_calls[0]
    parsed = safe_parse_json(tc.function.arguments, "verify")
    if parsed is None:
        return [], False

    gene_verdicts = _handle_verify_all(parsed)
    total_fields = sum(len(gv.get("field_verdicts", [])) for gv in gene_verdicts)

    if not gene_verdicts or total_fields == 0:
        print(f"    ⚠️  [{model_name}] verify result empty (genes={len(gene_verdicts)}, fields=0)")
        return [], False

    print(f"    ✅ [{model_name}] Verified: {len(gene_verdicts)} genes, {total_fields} fields")
    return gene_verdicts, True


# ═════════════════════════════════════════════════════���═════════════════════════
#  verify_paper sub-functions
# ═══════════════════════════════════════════════════════════════════════════════

def _collect_genes_for_verification(extraction_dict: dict) -> list:
    """从提取结果中收集所有基因信息用于验证。

    遍历 Common_Genes/Pathway_Genes/Regulation_Genes 三个数组，
    为每个基因记录其数据、分类、所属数组键和索引。

    Args:
        extraction_dict: extract_paper 返回的完整提取结果

    Returns:
        list: [(gene_data, category, arr_key, index), ...] 元组列表
    """
    all_genes = []
    for arr_key, cat in GENE_ARRAY_KEYS:
        arr = ensure_list(extraction_dict.get(arr_key, []))
        for idx, g in enumerate(arr):
            if isinstance(g, dict):
                all_genes.append((g, cat, arr_key, idx))
    return all_genes


def _run_batch_verification(
    md_content: str,
    all_genes_with_info: list,
    stem: str,
    tracker: TokenTracker,
) -> list:
    """执行分批验证 API 调用，返回所有批次的 gene_verdicts 汇总。

    按 _compute_batches() 计算批次划分，每批调用一次 LLM 验证 API，
    最终汇总所有批次的结果。部分批次失败时仍返回已有结果。

    Args:
        md_content: 预处理后的论文 Markdown 文本
        all_genes_with_info: [(gene_data, category, arr_key, index), ...] 全部基因信息
        stem: 论文文件名 stem（用于日志和 token 追踪标识）
        tracker: token 用量追踪器

    Returns:
        list: 所有批次合并后的 gene_verdicts 列表
    """
    total_genes = len(all_genes_with_info)
    batches = _compute_batches(total_genes)
    print(f"  🧬 Genes to verify: {total_genes}, batches: {len(batches)}")

    client = get_openai_client()
    all_gene_verdicts = []
    batch_failed = False

    for batch_idx, (start, end) in enumerate(batches):
        batch_genes = all_genes_with_info[start:end]
        gene_names_in_batch = [g.get("Gene_Name", f"Gene_{start + i + 1}") for i, (g, _, _, _) in enumerate(batch_genes)]
        print(f"\n  📦 Batch {batch_idx + 1}/{len(batches)}: genes {start + 1}-{end} ({', '.join(gene_names_in_batch)})")

        genes_text = _build_genes_text([(g, cat) for g, cat, _, _ in batch_genes])
        user_prompt = (
            f"## Paper Text (Markdown)\n\n{md_content}\n\n"
            f"---\n\n"
            f"## Genes to Verify (batch {batch_idx + 1}/{len(batches)}, {len(batch_genes)} genes)\n\n{genes_text}\n\n"
            f"Please verify each field of each gene. Give SUPPORTED/UNSUPPORTED/UNCERTAIN verdict with reason."
        )

        batch_verdicts, success = _call_verify_api(client, EXTRACTOR_MODEL, user_prompt, f"{stem}_batch{batch_idx + 1}", tracker)

        if not success:
            print(f"    ⚠️  Batch {batch_idx + 1} extraction API failed, skipping")
            batch_failed = True
            continue

        all_gene_verdicts.extend(batch_verdicts)

    if batch_failed and all_gene_verdicts:
        print(f"  ⚠️  Some batches failed, processing available results")

    return all_gene_verdicts


def _build_verification_report(
    extraction_dict: dict,
    all_genes_with_info: list,
    all_gene_verdicts: list,
    stem: str,
    md_path: Path,
) -> Tuple[dict, dict]:
    """构建验证报告并应用修正。

    按基因名将 verdict 匹配到对应基因，对 UNSUPPORTED 字段调用
    _apply_corrections() 修正为 "NA"，统计 SUPPORTED/UNSUPPORTED/UNCERTAIN
    各类数量，生成包含逐基因详情和汇总统计的报告。

    Args:
        extraction_dict: 原始提取结果
        all_genes_with_info: 全部基因信息列表
        all_gene_verdicts: LLM 返回的验证结果列表
        stem: 论文文件名 stem
        md_path: 论文 Markdown 文件路径

    Returns:
        tuple[dict, dict]: (file_report, verified_data)
            - file_report: 验证报告，包含逐基因验证详情和汇总统计
            - verified_data: 修正后的完整提取数据
    """
    # Match verdicts to genes by name
    verdict_by_name = {}
    for gv in all_gene_verdicts:
        gname = gv.get("Gene_Name", "")
        if gname:
            verdict_by_name[gname] = gv.get("field_verdicts", [])

    file_report = {
        "file": stem,
        "md_path": str(md_path),
        "timestamp": datetime.now().isoformat(),
        "genes": [],
        "summary": {
            "total_fields": 0,
            "supported": 0,
            "unsupported": 0,
            "uncertain": 0,
            "total_corrections": 0,
        },
    }

    corrected_arrays = {}
    for arr_key, _ in GENE_ARRAY_KEYS:
        corrected_arrays[arr_key] = list(ensure_list(extraction_dict.get(arr_key, [])))

    for gene_data, cat, arr_key, idx_in_arr in all_genes_with_info:
        gene_name = gene_data.get("Gene_Name", "Unknown")
        field_verdicts = verdict_by_name.get(gene_name, [])

        corrected_gene, corrections = _apply_corrections(gene_data, field_verdicts)

        if idx_in_arr < len(corrected_arrays[arr_key]):
            corrected_arrays[arr_key][idx_in_arr] = corrected_gene

        gene_stats = {"supported": 0, "unsupported": 0, "uncertain": 0}
        for fv in field_verdicts:
            v = fv.get("verdict", "").upper()
            if v == "SUPPORTED":
                gene_stats["supported"] += 1
            elif v == "UNSUPPORTED":
                gene_stats["unsupported"] += 1
            elif v == "UNCERTAIN":
                gene_stats["uncertain"] += 1

        total = gene_stats["supported"] + gene_stats["unsupported"] + gene_stats["uncertain"]

        print(f"\n  🧬 {gene_name} ({cat}):")
        print(f"     ✅ SUPPORTED:   {gene_stats['supported']}")
        print(f"     ❓ UNCERTAIN:   {gene_stats['uncertain']}")
        print(f"     ❌ UNSUPPORTED: {gene_stats['unsupported']}")
        if corrections:
            print(f"     🔧 Corrected {len(corrections)} fields")
            for c in corrections[:3]:
                old_val = str(c["old_value"])[:60]
                print(f"        - {c['field']}: \"{old_val}\" → \"NA\"")

        verification_dict = {}
        for fv in field_verdicts:
            fname = fv.get("field_name", "")
            if fname:
                verification_dict[fname] = {
                    "verdict": fv.get("verdict", "UNCERTAIN"),
                    "reason": fv.get("reason", ""),
                }

        file_report["genes"].append({
            "gene_name": gene_name,
            "category": cat,
            "verification": verification_dict,
            "corrections": corrections,
            "stats": gene_stats,
        })

        file_report["summary"]["total_fields"] += total
        file_report["summary"]["supported"] += gene_stats["supported"]
        file_report["summary"]["unsupported"] += gene_stats["unsupported"]
        file_report["summary"]["uncertain"] += gene_stats["uncertain"]
        file_report["summary"]["total_corrections"] += len(corrections)

    # Build verified data
    verified_data = dict(extraction_dict)
    for arr_key, _ in GENE_ARRAY_KEYS:
        verified_data[arr_key] = corrected_arrays[arr_key]

    return file_report, verified_data


def _save_verification_results(
    verified_data: dict,
    file_report: dict,
    stem: str,
    md_path: Path,
):
    """保存验证结果文件。

    执行三步操作：
    1. 将修正后的提取结果写入 output/ 目录（*_nutri_plant_verified.json）
    2. 将验证报告写入 reports/<paper>/ 目录（verification.json）
    3. 将已处理的原始 Markdown 文件移动到 processed/ 目录

    Args:
        verified_data: 修正后的完整提取数据
        file_report: 验证报告字典
        stem: 论文文件名 stem
        md_path: 论文原始 Markdown 文件路径
    """
    # Write corrected verified JSON
    verified_json_path = OUTPUT_DIR / f"{stem}_nutri_plant_verified.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    verified_tmp_path = verified_json_path.with_name(
        f".{verified_json_path.name}.{os.getpid()}.tmp"
    )
    verified_tmp_path.unlink(missing_ok=True)
    try:
        with verified_tmp_path.open("x", encoding="utf-8") as f:
            json.dump(verified_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(verified_tmp_path, verified_json_path)
        directory_fd = os.open(
            OUTPUT_DIR,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        verified_tmp_path.unlink(missing_ok=True)
    print(f"\n  ✅ Verified JSON saved: {verified_json_path}")

    # Save verification report
    paper_dir = REPORTS_DIR / stem_to_dirname(stem)
    paper_dir.mkdir(parents=True, exist_ok=True)
    report_path = paper_dir / "verification.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(file_report, f, indent=2, ensure_ascii=False)
    print(f"  📋 Report saved: {report_path}")

    # Move processed MD
    if md_path.exists():
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        dest = PROCESSED_DIR / md_path.name
        shutil.move(str(md_path), str(dest))
        print(f"  📦 MD moved to: {dest}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def verify_paper(
    md_path,
    extraction_dict: dict,
    stem: str,
    tracker: TokenTracker,
) -> Optional[dict]:
    """验证论文中所有基因的提取结果（对外主入口）。

    完整流程：
    1. 增量跳过：如果 verified JSON 已存在，直接返回跳过状态（可设 FORCE_RERUN=1 强制重跑）
    2. 读取并预处理论文 Markdown
    3. 收集提取结果中的所有基因信息
    4. 分批调用 LLM 进行逐字段验证
    5. 构建验证报告并自动修正 UNSUPPORTED 字段
    6. 保存验证结果、报告和移动已处理文件

    Args:
        md_path: 论文 Markdown 文件路径
        extraction_dict: extract_paper 返回的提取结果
        stem: 论文文件名 stem（不含扩展名）
        tracker: token 用量追踪器

    Returns:
        dict | None: 验证报告字典；跳过时返回 {"status": "skipped"}；失败返回 None
    """
    md_path = Path(md_path)

    # Incremental: skip if already verified
    verified_json_path = OUTPUT_DIR / f"{stem}_nutri_plant_verified.json"
    if verified_json_path.exists() and os.getenv("FORCE_RERUN") != "1":
        print(f"\n  ⏭️  Already verified, skip: {stem}  (set FORCE_RERUN=1 to re-run)")
        return {"status": "skipped", "report": None}

    print(f"\n{'=' * 60}")
    print(f"📄 Verifying: {stem}")
    print(f"{'=' * 60}")

    # Read and preprocess
    md_content = read_markdown_bounded(md_path)
    original_len = len(md_content)
    md_content = preprocess_md_for_llm(md_content, tracker=tracker)
    print(f"  📏 Preprocessing: {original_len:,} → {len(md_content):,} chars (saved {original_len - len(md_content):,})")

    # 1. Collect genes
    all_genes_with_info = _collect_genes_for_verification(extraction_dict)
    if not all_genes_with_info:
        print("  ⚠️  No genes to verify")
        return None

    # 2. Batch verification
    all_gene_verdicts = _run_batch_verification(md_content, all_genes_with_info, stem, tracker)
    if not all_gene_verdicts:
        print(f"    ⚠️  All batches failed: {stem}")
        return None

    # 3. Build report + apply corrections
    file_report, verified_data = _build_verification_report(
        extraction_dict, all_genes_with_info, all_gene_verdicts, stem, md_path,
    )

    # 4. Save results
    _save_verification_results(verified_data, file_report, stem, md_path)

    return file_report
