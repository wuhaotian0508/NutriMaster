"""
utils.py — 提取管线的共享工具函数模块。

整合了 extract.py、verify.py 和 pipeline.py 中重复出现的模式，
包括基因数组常量、防御性解析、JSON 修复和 DeepSeek 兼容性处理。
"""

import json
from typing import Optional

# ─── Gene array key constants ────────────────────────────────────────────────

GENE_ARRAY_KEYS = (
    ("Common_Genes", "Common"),
    ("Pathway_Genes", "Pathway"),
    ("Regulation_Genes", "Regulation"),
)

GENE_ARRAY_KEY_NAMES = tuple(k for k, _ in GENE_ARRAY_KEYS)


# ─── Defensive parsing helpers ───────────────────────────────────────────────

def ensure_list(val) -> list:
    """防御性地把值转成 list。

    处理 LLM 返回的各种异常情况：
    - 已经是 list → 直接返回
    - 是 JSON 字符串 → json.loads 解析
    - 其他类型 → 返回空列表

    例: ensure_list('[{"Gene_Name":"CHS"}]') → [{"Gene_Name":"CHS"}]
        ensure_list(None) → []
    """
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return []
    return []


def get_gene_name(gene_dict: dict) -> str:
    """从基因 dict 中提取基因名，兼容多种 key 命名。

    依次尝试 Gene_Name → gene → gene_name → name，
    兼容不同版本 schema 和 LLM 返回格式。

    例: get_gene_name({"Gene_Name": "CHS"}) → "CHS"
        get_gene_name({"gene": "SlMYB12"}) → "SlMYB12"
    """
    return (
        gene_dict.get("Gene_Name")
        or gene_dict.get("gene")
        or gene_dict.get("gene_name")
        or gene_dict.get("name")
        or ""
    )


def safe_parse_json(json_str: str, label: str = "") -> Optional[dict]:
    """解析 JSON，支持截断修复。

    LLM 有时返回被截断的 JSON（超过 max_tokens），这个函数会：
    1. 先尝试直接 json.loads
    2. 失败则找最后一个 }，截断并补齐未闭合的 [] 和 {}
    3. 再次尝试解析

    例: '{"a":1,"b":[2,3' → 修复为 '{"a":1,"b":[2,3]}' → 成功解析
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    last_brace = json_str.rfind('}')
    if last_brace == -1:
        return None

    truncated = json_str[:last_brace + 1]
    ob = truncated.count('{') - truncated.count('}')
    obr = truncated.count('[') - truncated.count(']')
    repair = truncated + ']' * max(0, obr) + '}' * max(0, ob)
    try:
        result = json.loads(repair)
        if label:
            print(f"    🔧 [{label}] Truncated JSON auto-repaired")
        return result
    except json.JSONDecodeError:
        return None


def stem_to_dirname(stem: str) -> str:
    """把文件名 stem 转成目录名。

    去掉 MinerU 前缀、去掉重复后缀 _(1)、下划线转连字符。
    例: "MinerU_markdown_Nat_Biotechnol_2008_Butelli_(1)"
    → "Nat-Biotechnol-2008-Butelli"
    """
    if stem.startswith("MinerU_markdown_"):
        stem = stem[len("MinerU_markdown_"):]
    stem = stem.replace("_(1)", "")
    stem = stem.replace("_", "-")
    return stem


# ─── DeepSeek V4 compatibility ───────────────────────────────────────────────

def is_deepseek_model(model_name: str) -> bool:
    """判断是否为 DeepSeek 系列模型（V3.2/V4/R1）。"""
    name = model_name.lower()
    return any(m in name for m in ("deepseek-reasoner", "deepseek-r1", "deepseek-v4"))


def prepare_deepseek_params(model_name: str, params: dict) -> dict:
    """为 DeepSeek V4 准备 API 参数。

    DeepSeek V4 thinking 模式下：
    1. 移除 temperature/top_p/presence_penalty/frequency_penalty（会被忽略）
    2. 移除 logprobs/top_logprobs（会报 400 错误）
    3. 通过 extra_body 传递 thinking 和 reasoning_effort 配置

    Args:
        model_name: 模型名称
        params: 原始 API 参数字典

    Returns:
        处理后的参数字典（原地修改 + 返回）
    """
    if not is_deepseek_model(model_name):
        return params

    # 移除不兼容参数
    ignored = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
    forbidden = {"logprobs", "top_logprobs"}
    for key in ignored | forbidden:
        params.pop(key, None)

    # 配置 thinking 模式（通过 extra_body）
    extra_body = params.get("extra_body", {})
    if "thinking" not in extra_body:
        extra_body["thinking"] = {"type": "enabled"}
    if "reasoning_effort" not in extra_body:
        # extractor 是单次提取，使用 high 即可（不需要 max）
        extra_body["reasoning_effort"] = "high"

    if extra_body:
        params["extra_body"] = extra_body

    return params

