# NutriBench 评测框架

基于 **task × model 正交分离** 设计的评测框架。

## 设计理念

参考图片中的思想：将评测系统拆解为独立的正交模块，避免"每加一个模型就要改一遍评测框架"的问题。

**核心原则：**
- 复用性：新模型不需要修改评测逻辑
- 灵活性：数据、模型、评分器可以自由组合
- 可扩展性：添加新组件不影响现有代码

## 目录结构

```
eval/
├── agents/                  # 被测 Agent（与数据和评分无关）
│   ├── llm_agent.py        # 通用 LLM Agent
│   ├── nutrimaster_agent.py # NutriMaster RAG Agent
│   └── evomaster_agent.py  # EvoMaster 工具使用 Agent
├── judge/                   # 评分 Judge（与数据和模型无关）
│   └── llm_judge.py        # LLM Judge 评分器
├── datamanager/             # 数据读写（与模型和评分无关）
│   ├── notion_storage.py   # Notion 数据库读写
│   └── local_storage.py    # 本地 JSONL 文件读写
├── metrics/                 # 结果分析小工具
│   └── stats.py            # 统计计算（平均分、得分率等）
├── data/                    # 本地数据存放目录（按日期/agent/版本组织）
│   └── .gitkeep            # 例如: data/2026-05-07/NutriMaster/v3.jsonl
├── main.py                  # 主评测脚本
└── README.md                # 本文档

metrics/
└── calc_score.py            # 计算历史结果平均分（独立工具）
```

## 统一接口

### Agent 接口
所有 Agent 都有统一的 `answer()` 方法：

```python
async def answer(self, question: str) -> dict[str, Any]:
    """
    返回: {"ok": True/False, "output": "答案", "error": "错误信息"}
    """
```

### Judge 接口
Judge 有统一的 `judge()` 方法：

```python
async def judge(self, question: str, answer: str, rubrics: list) -> dict[str, Any]:
    """
    返回: {"ok": True/False, "总分": 8.5, "满分": 10.0, "评分详情": "...", "error": "..."}
    """
```

### 数据格式
题目格式：
```python
{
    "编号": 1,
    "正文": "问题内容",
    "采分点": [{"描述": "...", "满分": 5.0}, ...],
    "难度": "中等",
    "标签": ["基因", "代谢"]
}
```

结果格式：
```python
{
    "题目编号": 1,
    "Agent名称": "NutriMaster",
    "版本": "v3",
    "答案": "...",
    "总分": 8.5,
    "满分": 10.0,
    "评分详情": "...",
    "耗时": 3.2
}
```

## 使用示例

### 运行评测

```bash
# 评测所有配置的 Agent（默认：llm + nutrimaster）
python eval/main.py

# 只评测 LLM baseline
python eval/main.py --agents llm

# 只评测 NutriMaster
python eval/main.py --agents nutrimaster

# 评测多个 Agent
python eval/main.py --agents llm,nutrimaster,evomaster

# 限制题目数量（调试用）
python eval/main.py --agents llm --max-questions 5

# 指定版本
python eval/main.py --agents nutrimaster --version v4

# 调整并发数
python eval/main.py --agents llm --agent-concurrency 5 --judge-concurrency 5
```

**结果自动保存到两个地方：**
1. 本地：`eval/data/2026-05-07/NutriMaster/v3.jsonl`
2. Notion：结果数据库

### 计算历史结果平均分

```bash
# 计算指定 agent 和版本的平均分
python eval/metrics/calc_score.py NutriMaster v3

# 显示详细得分
python eval/metrics/calc_score.py "Claude-4.5-Sonnet" v3 --show-details
```

## 添加新 Agent

1. 在 `eval/agents/` 下创建新文件
2. 实现 `answer()` 方法，返回统一格式

```python
class MyAgent:
    def __init__(self, ...):
        self.name = "MyAgent"

    async def answer(self, question: str) -> dict[str, Any]:
        # 调用你的模型
        result = my_model.generate(question)
        return {"ok": True, "output": result, "error": None}
```

3. 在 `main.py` 中添加命令行选项

```python
elif args.agent == "myagent":
    agent = MyAgent(...)
```

## 添加新 Judge

1. 在 `eval/judge/` 下创建新文件
2. 实现 `judge()` 方法，返回统一格式

```python
class MyJudge:
    async def judge(self, question: str, answer: str, rubrics: list) -> dict[str, Any]:
        # 实现你的评分逻辑
        score = calculate_score(answer, rubrics)
        return {"ok": True, "总分": score, "满分": 10.0, "评分详情": "...", "error": None}
```

## 添加新数据源

1. 在 `eval/datamanager/` 下创建新文件
2. 实现 `load_questions()` 和 `save_results()` 方法

```python
class MyStorage:
    def load_questions(self, ...) -> list[dict[str, Any]]:
        # 从你的数据源加载题目
        return [{"编号": 1, "正文": "...", "采分点": [...], ...}, ...]

    def save_results(self, results: list[dict[str, Any]]):
        # 保存结果到你的数据源
        pass
```

## 环境变量

```bash
# Notion API
NOTION_API_KEY=secret_xxx
QUESTION_DB_ID=e755b041d920410fa6dd3aa88c421879
RESULT_DB_ID=c7b1b42c0ac14b5f883725f75860860e

# LLM API
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.gpugeek.com/v1

# Judge 模型
JUDGE_MODEL=Vendor2/Gemini-3.1-pro

# EvoMaster（可选）
EVOMASTER_ROOT=/data/haotianwu/Evomaster_fs
```

## 与旧版本的关系

- **旧版本**: `eval_pipeline.py` - 单体脚本，所有逻辑耦合在一起
- **新版本**: `eval/` 目录 - 模块化设计，组件可独立复用

新版本的优势：
- ✅ 添加新模型不需要修改评测逻辑
- ✅ 可以轻松切换数据源（Notion ↔ 本地文件）
- ✅ 可以组合不同的 Agent、Judge、数据源
- ✅ 代码更清晰，易于测试和维护

旧版本 `eval_pipeline.py` 仍然保留，用于生产环境。新版本可以逐步替代。
