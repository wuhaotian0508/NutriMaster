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
├── configs.py               # 环境变量、默认模型、路径等集中配置
├── contracts.py             # 轻量 Protocol 接口定义
├── agent_factory.py         # Agent 创建和 LLM alias 展开
├── agents/                  # 被测 Agent（与数据和评分无关）
│   ├── llm_agent.py        # 通用 LLM Agent
│   ├── nutrimaster_agent.py # NutriMaster RAG Agent
│   └── evomaster_agent.py  # EvoMaster 工具使用 Agent
├── judge/                   # 评分 Judge（与数据和模型无关）
│   └── llm_judge.py        # LLM Judge 评分器
├── datamanager/             # 数据读写（与模型和评分无关）
│   ├── notion_storage.py   # Notion 数据库读写
│   ├── local_storage.py    # 本地 JSONL 文件读写
│   ├── pull.py             # 从 Notion 下载题目，并保存到本地 latest
│   └── push.py             # 保存/上传结果，上传前支持 dry-run
├── metrics/                 # 结果分析小工具
│   ├── stats.py            # 统计计算（平均分、得分率等）
│   └── filter_stats.py     # 高级统计（按 agent/版本/时间过滤）
├── data/                    # 本地数据存放目录（按日期/agent/版本组织）
├── runner.py                # 单题评测与多 Agent 编排
├── run_manager.py           # 运行管理器（并行、重试、断点续传）
├── checkpoint_cli.py        # 检查点管理工具
├── main.py                  # 主评测脚本
└── README.md                # 本文档
```

## 统一接口

### Agent 接口
所有 Agent 都满足 `EvalAgent` 轻量协议：有 `name` 属性和统一的 `answer()` 方法。

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
    "耗时": 3.2,
    "时间戳": "2026-05-07T10:30:00"
}
```

## 使用示例

### 推荐流程：先下载、再本地评测、最后上传

普通评测只读本地题目、只写本地结果，不会自动上传 Notion。

```bash
# 1. 从 Notion 下载题目到本地；--after 按 Notion created_time 过滤
python -m eval.main pull --after 2026-05-06

# 2. 本地评测，默认读取 eval/data/questions/latest.jsonl
python -m eval.main nutrimaster v3 --resume

# 3. 先预览最近一次本地结果
python -m eval.main push --dry-run

# 4. 确认满意后上传最近一次本地结果到 Notion
python -m eval.main push
```

默认文件：
- 题目 latest：`eval/data/questions/latest.jsonl`
- 结果 latest：`eval/data/results/latest.jsonl`
- 日期结果：`eval/data/YYYY-MM-DD/<Agent名称>/<版本>.jsonl`

数据流职责：
- `eval/datamanager/pull.py`：`python -m eval.main pull` 的实现，从 Notion 拉题并写本地题目文件。
- `eval/datamanager/push.py`：本地结果落盘和 `python -m eval.main push` 的实现。
- `eval/datamanager/notion_storage.py`：只处理 Notion API 和 Notion schema 字段适配。
- `eval/datamanager/local_storage.py`：只处理 JSONL 文件读写。
- `eval/agent_factory.py`：只处理 Agent 创建、LLM 模型 alias 和 `llm` 多模型展开。
- `eval/runner.py`：只处理单题评测、RunManager 调度、统计和本地结果保存。
- `eval/main.py`：只负责 CLI、pull/push 分发、题目加载和启动 runner。

### 基础评测

```bash
# 使用位置参数（推荐，更简洁）
python -m eval.main nutrimaster v3
python -m eval.main evomaster v4
python -m eval.main llm v3

# 使用命名参数
python -m eval.main --agents nutrimaster --version v3

# 评测所有配置的 Agent（默认：llm + nutrimaster）
python -m eval.main

# 评测多个 Agent
python -m eval.main --agents llm,nutrimaster,evomaster

# 限制题目数量（调试用）
python -m eval.main nutrimaster v3 --max-questions 5

# 调整并发数
python -m eval.main nutrimaster v3 --agent-concurrency 5 --judge-concurrency 5

# 指定题目文件
python -m eval.main nutrimaster v3 --questions-file eval/data/questions/questions_after_2026-05-06.jsonl

# EvoMaster 默认读取 OPENAI_API_KEY、OPENAI_BASE_URL 和 MAIN_MODEL
MAIN_MODEL=deepseek-v4-flash python -m eval.main evomaster v3 --resume
```

### 断点续传（Resume）

```bash
# 首次运行
python -m eval.main nutrimaster v3

# 中断后继续（自动跳过已完成的题目）
python -m eval.main nutrimaster v3 --resume

# 重跑失败的题目
python -m eval.main nutrimaster v3 --resume --retry-failed

# 清除检查点，从头开始
python -m eval.main nutrimaster v3 --clean

# 自定义检查点目录
python -m eval.main nutrimaster v3 --checkpoint-dir /path/to/checkpoints
```

### 检查点管理

```bash
# 查看所有检查点
python -m eval.checkpoint_cli list

# 查看特定检查点状态
python -m eval.checkpoint_cli status --agent NutriMaster --version v3

# 清除检查点
python -m eval.checkpoint_cli clear --agent NutriMaster --version v3

# 导出检查点
python -m eval.checkpoint_cli export --agent NutriMaster --version v3 -o results.jsonl
```

### 高级统计分析

```bash
# 从本地文件统计
python -m eval.metrics.filter_stats --file eval/data/2026-05-07/NutriMaster/v3.jsonl

# 按 agent 和版本过滤
python -m eval.metrics.filter_stats --file results.jsonl --agent NutriMaster --version v3

# 按时间过滤（5月5日之后）
python -m eval.metrics.filter_stats --file results.jsonl --after 2026-05-05

# 统计 Evomaster-fs_mv 的 v3 版本在 5月5日之后的均值
python -m eval.metrics.filter_stats --file results.jsonl --agent Evomaster-fs_mv --version v3 --after 2026-05-05

# 从 Notion 数据库统计（需要设置 NOTION_API_KEY 和 RESULT_DB_ID）
python -m eval.metrics.filter_stats --notion --database-id c7b1b42c0ac14b5f883725f75860860e --agent Evomaster-fs_mv --version v3 --after 2026-05-05

# 显示详细结果
python -m eval.metrics.filter_stats --file results.jsonl --agent NutriMaster --details

# 导出统计结果
python -m eval.metrics.filter_stats --file results.jsonl --agent NutriMaster --output stats.json
```

**结果会保存到本地和检查点：**
1. 本地：`eval/data/2026-05-07/NutriMaster/v3.jsonl`
2. Latest：`eval/data/results/latest.jsonl`
3. 检查点：`.eval_checkpoints/NutriMaster_v3.jsonl`（用于断点续传）

Notion 上传需要显式运行 `python -m eval.main push`。

## RunManager 特性

### 并行控制
- 自动管理并发数，避免 API 限流
- 使用 `asyncio.Semaphore` 控制并发

### 重试机制
- 自动重试失败的任务（默认 3 次）
- 指数退避策略（1s, 2s, 4s）
- 可配置重试次数：`--max-retries 5`

### 断点续传
- 自动保存检查点（每 5 题）
- 中断后可继续运行
- 支持重跑失败的题目

### 进度追踪
- 实时显示进度（需要安装 `tqdm`）
- 显示成功/失败状态
- 显示耗时统计

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

3. 在 `eval/agent_factory.py` 的 `create_agent()` 中添加

```python
def create_agent(agent_type: str, agent_config: dict = None):
    if agent_type == "myagent":
        return MyAgent(...)
    # ...
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

1. 在 `eval/datamanager/` 下创建新的 storage 文件，封装外部系统的字段适配。
2. 如果它参与 CLI 流程，在 `pull.py` 或 `push.py` 里接入编排逻辑。
3. 保持 `main.py` 不直接依赖外部数据源 SDK。

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

# OpenAI-compatible API（EvoMaster / NutriMaster 主服务常用）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=http://your-openai-compatible-endpoint/v1
MAIN_MODEL=deepseek-v4-flash

# Judge 模型
JUDGE_MODEL=Vendor2/Gemini-3.1-pro

# EvoMaster（可选）
EVOMASTER_ROOT=/data/haotianwu/Evomaster_fs
EVOMASTER_PLAYGROUND=fs_mv
EVOMASTER_MODEL=deepseek-v4-flash
EVOMASTER_TIMEOUT=3600
```

## 与旧版本的关系

- **旧版本**: `eval_pipeline.py` - 单体脚本，所有逻辑耦合在一起
- **新版本**: `eval/` 目录 - 模块化设计，组件可独立复用

新版本的优势：
- ✅ 添加新模型不需要修改评测逻辑
- ✅ 可以轻松切换数据源（Notion ↔ 本地文件）
- ✅ 可以组合不同的 Agent、Judge、数据源
- ✅ 支持断点续传和自动重试
- ✅ 代码更清晰，易于测试和维护

旧版本已移至 `others/eval_pipeline.py`，新版本是推荐使用的方式。
