"""
NutriBench 评测框架 - 正交化设计

核心思想：task × model 正交分离
- datamanager/: 数据加载（Notion、本地文件）
- agents/: 各种 Agent（LLM、NutriMaster、EvoMaster）
- judge/: LLM Judge 评分
- metrics/: 评分和结果分析
- run_manager.py: 运行管理器（并行、重试、断点续传）
- main.py: 主评测脚本
"""

from eval.run_manager import RunManager

__all__ = ["RunManager"]
