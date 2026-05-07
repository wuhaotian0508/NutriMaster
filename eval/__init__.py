"""
NutriBench 评测框架 - 正交化设计

核心思想：task × model 正交分离
- datasets/: 数据加载（Notion、本地文件）
- agents/: 各种 Agent（LLM、NutriMaster、EvoMaster）
- metrics/: 评分和结果分析
- run_eval.py: 主评测脚本（类似 eval_pipeline.py）
"""
