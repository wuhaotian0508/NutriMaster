"""
token_tracker.py — 线程安全的 API token 用量追踪器。

精简版本：仅打印总 input/output/total tokens 和调用次数。
支持并行论文处理场景下的线程安全操作。
"""

import json
import os
import threading
from datetime import datetime


class TokenTracker:
    """线程安全的 API token 用量追踪器。

    记录每次 API 调用的 token 消耗（输入/输出/总计），支持按阶段过滤、
    汇总统计、打印报告和导出 JSON。使用 RLock 确保多线程并行处理时的安全性。
    """

    def __init__(self, model="unknown"):
        """初始化 token 追踪器。

        使用 threading.RLock()（可重入锁），防止同一线程在 save_report()
        嵌套调用 get_summary() 时死锁。

        Args:
            model: 模型名称，用于报告中标识使用的 AI 模型，默认 "unknown"
        """
        self.model = model
        self.calls = []
        self._lock = threading.RLock()

    def add(self, response, stage="unknown", file=""):
        """记录一次 API 调用的 token 用量（线程安全）。

        从 response.usage 中提取 prompt_tokens/completion_tokens，
        加入总计并保存到明细列表。

        Args:
            response: OpenAI API 的响应对象
            stage: 调用阶段，如 "extract" / "verify" / "preprocess"
            file: 对应的论文文件名
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        record = {
            "stage": stage,
            "file": file,
            "prompt_tokens": usage.prompt_tokens or 0,
            "completion_tokens": usage.completion_tokens or 0,
            "total_tokens": usage.total_tokens or 0,
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self.calls.append(record)

    def _aggregate(self, stage_filter=None):
        """聚合 token 用量统计，可按阶段过滤。

        在锁内遍历所有调用记录，汇总 prompt/completion/total tokens，
        并计算千 token（kT）值。

        Args:
            stage_filter: 可选的阶段名称过滤器（如 "extract"、"verify"、"preprocess"）。
                         为 None 时聚合所有阶段。

        Returns:
            dict: 包含 prompt_tokens、completion_tokens、total_tokens、
                 对应的 ktokens 值和调用次数 calls
        """
        with self._lock:
            filtered = self.calls if stage_filter is None else [
                c for c in self.calls if c["stage"] == stage_filter
            ]
            prompt = sum(c["prompt_tokens"] for c in filtered)
            completion = sum(c["completion_tokens"] for c in filtered)
            total = sum(c["total_tokens"] for c in filtered)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "prompt_ktokens": round(prompt / 1000, 2),
            "completion_ktokens": round(completion / 1000, 2),
            "total_ktokens": round(total / 1000, 2),
            "calls": len(filtered),
        }

    def get_summary(self):
        """获取按阶段分组的 token 用量汇总。

        遍历所有记录的阶段（如 extract、verify、preprocess），
        分别聚合每个阶段的统计数据，并附加一个 "total" 总计。

        Returns:
            dict: 键为阶段名称（加 "total"），值为各阶段的聚合统计 dict
        """
        with self._lock:
            stages = sorted(set(c["stage"] for c in self.calls))
        summary = {}
        for stage in stages:
            summary[stage] = self._aggregate(stage)
        summary["total"] = self._aggregate()
        return summary

    def print_summary(self):
        """打印 token 用量汇总到终端。

        以表格格式输出总 input/output/total tokens（以千 token 为单位）
        和 API 调用次数。无调用记录时打印提示信息。
        """
        with self._lock:
            if not self.calls:
                print("\n📊 Token usage: no API calls recorded")
                return

        t = self._aggregate()
        print(f"\n{'═' * 50}")
        print(f"📊 Token Usage (model: {self.model})")
        print(f"{'═' * 50}")
        print(f"  Input:  {t['prompt_ktokens']:>10.2f} kT")
        print(f"  Output: {t['completion_ktokens']:>10.2f} kT")
        print(f"  Total:  {t['total_ktokens']:>10.2f} kT")
        print(f"  Calls:  {t['calls']:>10}")
        print(f"{'═' * 50}")

    def save_report(self, path):
        """保存详细的 token 用量报告到 JSON 文件。

        在锁内复制调用记录列表并生成汇总，然后写入 JSON 文件。
        使用 RLock 所以内部调用 get_summary() 不会死锁。

        Args:
            path: 报告文件的保存路径（字符串或 Path 对象）

        Returns:
            str: 报告文件的实际保存路径
        """
        path = str(path)
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)

        with self._lock:
            calls = list(self.calls)
            report = {
                "timestamp": datetime.now().replace(microsecond=0).isoformat(),
                "model": self.model,
                "calls": calls,
                "summary": self.get_summary(),
            }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"  💾 Token report saved: {path}")
        return path

    def merge(self, other):
        """合并另一个 TokenTracker 的记录（线程安全）。

        将 other 中的所有调用记录追加到当前追踪器中。
        同时获取两个追踪器的锁以避免竞态条件。

        Args:
            other: 要合并的另一个 TokenTracker 实例
        """
        if isinstance(other, TokenTracker):
            with self._lock:
                with other._lock:
                    self.calls.extend(other.calls)
