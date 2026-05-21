# NutriMaster

NutriMaster 是一个面向植物营养与代谢基因知识库的抽取、检索和智能问答系统。它把论文 Markdown 转换成经过验证的结构化基因 JSON，基于语料构建 RAG 检索索引，并提供 Web 问答、Admin 管理、个人文献库、技能工具和 CRISPR 实验 SOP 生成功能。

## 核心能力

- 从论文 Markdown 中抽取结构化植物营养/代谢基因信息。
- 使用 LLM 对抽取字段进行原文依据验证和纠错。
- 将已验证论文保存为 `*_nutri_plant_verified.json` 主语料。
- 对主语料进行分块、向量化和增量索引。
- 提供 FastAPI Web 应用，用于 RAG 问答、技能调用、SOP 生成和个人知识库。
- 提供 Admin 管理界面，用于上传论文、运行抽取流水线、编辑 prompt/schema 和重建索引。
- 自动化测试直接覆盖主 Web 应用接口，避免维护第二套 API 入口。

## 仓库结构

```text
.
├── data/
│   ├── corpus/             # NutriMaster 主语料 JSON
│   ├── index/              # 全局 RAG 索引产物
│   ├── personal_lib/       # 用户上传 PDF 和个人索引
│   └── user_skills/        # 用户自定义技能
├── src/nutrimaster/
│   ├── extraction/         # Markdown → 抽取 JSON → verified JSON 流水线
│   │   ├── input/          # 待处理 Markdown
│   │   ├── output/         # 临时抽取输出
│   │   ├── prompts/        # 抽取 prompt 和 JSON schema（v5）
│   │   ├── reports/        # 抽取、验证和 token 报告
│   │   ├── config.py
│   │   ├── extract.py
│   │   ├── verify.py
│   │   ├── pipeline.py
│   │   └── run.sh
│   ├── rag/                # RAG 分块、向量化和检索
│   │   ├── chunking.py
│   │   ├── incremental_indexer.py
│   │   ├── index_service.py
│   │   └── jina_retriever.py
│   ├── agent/              # Agent runtime、工具和技能
│   │   ├── tools/
│   │   └── skills/
│   ├── web/                # FastAPI Web 应用
│   │   ├── app.py
│   │   ├── routes/
│   │   ├── admin/          # Flask Admin 面板（挂载于 /admin）
│   │   └── static/
│   ├── config/             # 集中配置（settings.py）
│   ├── auth/               # Supabase 认证集成
│   └── experiment/         # 实验功能（CRISPR、基因转化）
├── eval/                   # NutriBench 评测框架（task × model 正交设计，支持 Notion 集成）
├── graphing/               # 基因通路图谱构建与可视化工具
├── tests/                  # 单元、集成和 e2e 测试
├── logs/
├── DEVELOPMENT.md          # 协作开发与维护指南
├── pyproject.toml
└── .env.example
```

## 数据目录

主语料位于 `data/corpus/`。每篇已验证论文是一个 JSON 文件，文件名以 `_nutri_plant_verified.json` 结尾。

当前主语料核心 schema 包含：

- `Title`
- `Journal`
- `DOI`
- `Common_Genes`
- `Pathway_Genes`
- `Regulation_Genes`

全局 RAG 索引由主语料生成，位于 `data/index/`。索引属于运行时产物，可重建。

用户上传 PDF 与个人索引位于 `data/personal_lib/<user_id>/`，与全局主语料分开管理。

## 环境要求

- Python `>=3.11`
- OpenAI-compatible LLM endpoint，用于抽取、验证和对话
- Jina API key，用于 embedding 和检索
- Supabase 项目，用于认证和用户资料
- 可选：PyMuPDF，用于个人 PDF 文献库
- 可选：scikit-learn，用于部分 TF-IDF/索引相关流程

## 安装

推荐使用 `uv` 管理环境。在仓库根目录执行：

```bash
uv sync --dev
```

如果只想一键启动，`uv run` 会自动创建环境并同步依赖：

```bash
uv run nutrimaster web
```

## 环境变量

复制配置模板：

```bash
cp .env.example .env
```

按需填写下面变量。

LLM 配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
MAIN_MODEL=deepseek-v4-flash
EXTRACTOR_MODEL=gpt-5.5
```

检索配置：

```bash
JINA_API_KEY=...
RAG_DATA_DIR=./data/corpus
RAG_INDEX_DIR=./data/index
RAG_PERSONAL_LIB_DIR=./data/personal_lib
```

认证与站点配置：

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
ADMIN_EMAIL=admin@example.com
SITE_URL=http://localhost:5000
```

运行参数：

```bash
WEB_HOST=0.0.0.0
WEB_PORT=5000
ADMIN_PORT=5501
DEBUG=false
TOP_K_RETRIEVAL=20
TOP_K_RERANK=10
DEEP_TOP_K_RETRIEVAL=20
DEEP_TOP_K_RERANK=10
CHUNK_SIZE=1500
CHUNK_OVERLAP=200
MAX_PDF_SIZE_MB=50
MAX_FILES_PER_USER=20
```

Extractor 配置：

```bash
MD_DIR=./src/nutrimaster/extraction/input
JSON_DIR=./data/corpus
REPORTS_DIR=./src/nutrimaster/extraction/reports
TOKEN_USAGE_DIR=./src/nutrimaster/extraction/reports/token-usage
PROCESSED_DIR=./src/nutrimaster/extraction/input/processed
PROMPT_PATH=./src/nutrimaster/extraction/prompts/nutri_gene_prompt_v5.txt
SCHEMA_PATH=./src/nutrimaster/extraction/prompts/nutri_gene_schema_v5.json
EXTRACTOR_MODEL=gpt-5.5
TEMPERATURE=0.7
MAX_WORKERS=20
```

评测框架配置（仅使用 `eval/` 时需要）：

```bash
NOTION_API_KEY=secret_xxx
QUESTION_DB_ID=...
RESULT_DB_ID=...
JUDGE_MODEL=Vendor2/Gemini-3.1-pro
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://...
```

检查真实服务所需配置：

```bash
uv run nutrimaster check-config
```

该命令会检查 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MAIN_MODEL`、`JINA_API_KEY`、`SUPABASE_URL` 和 `SUPABASE_SERVICE_ROLE_KEY`。

## 启动 Web 应用

完整 Web 应用包含聊天界面、认证、个人知识库、CRISPR/SOP 路由，并将 Admin 面板挂载到 `/admin`。

```bash
uv run nutrimaster web
```

默认地址：

- Web 应用：`http://localhost:5000`
- Admin 面板：`http://localhost:5000/admin`

生产或部署环境可直接使用 Uvicorn：

```bash
uv run uvicorn nutrimaster.web.app:app --host 0.0.0.0 --port 5000
```

## Admin

Admin 挂载在主 Web 应用的 `/admin` 路径下，不再维护独立 Flask 启动命令。功能包括上传论文 ZIP、去重、单篇预览、批量抽取与验证、SSE 进度、prompt/schema 编辑、索引重建和已处理论文列表。Admin 访问由 `ADMIN_EMAIL` 白名单控制。

如果希望启动时自动构建索引，设置：

```bash
NUTRIMASTER_API_BUILD_INDEX=true
```

注意：启动时建索引会增加启动时间和外部 API 调用成本。

## 运行 Extractor 流水线

Extractor 从 `src/nutrimaster/extraction/input/` 读取 Markdown，输出 verified JSON 到 `data/corpus/`。

推荐入口：

```bash
uv run nutrimaster extract
```

常用模式：

```bash
uv run nutrimaster extract
uv run nutrimaster extract --test 1
uv run nutrimaster extract --test Butelli
uv run nutrimaster extract --workers 4
uv run nutrimaster extract --rerun
```

处理阶段：

1. `extractor.extract` 调用 LLM 抽取论文元信息和基因数组。
2. `extractor.verify` 根据原文验证并修正字段。
3. verified JSON 写入 `JSON_DIR`。
4. 源 Markdown 移动到 `PROCESSED_DIR`。
5. 验证报告和 token 报告写入 `src/nutrimaster/extraction/reports/`。

注意：`src/nutrimaster/extraction/config.py` 在 import 时读取环境变量。Admin 因此会在 import nutrimaster.extraction 前先设置 `JSON_DIR`、`PROMPT_PATH` 和 `SCHEMA_PATH`。

## 检索和索引

全局检索主要由以下模块组成：

- `src/nutrimaster/rag/chunking.py`
- `src/nutrimaster/rag/incremental_indexer.py`
- `src/nutrimaster/rag/index_service.py`
- `src/nutrimaster/rag/jina_retriever.py`

索引器扫描 `RAG_DATA_DIR` 下的 `*_nutri_plant_verified.json`，生成结构化 gene chunks，调用 Jina embedding，并写入：

```text
data/index/manifest.json
data/index/chunks.pkl
data/index/embeddings.npy
```

`manifest.json` 记录文件 hash 和 chunk range，用于增量重建时复用未变化文件。

## 个人文献库

个人文献库支持用户上传 PDF，并在对话中与全局语料一起检索。

目录结构：

```text
data/personal_lib/<user_id>/
├── pdfs/
└── index/
    ├── manifest.json
    ├── chunks.pkl
    └── embeddings.npy
```

PDF 解析需要可选依赖：

```bash
uv sync --extra pdf
```

开发依赖已包含 PDF 支持：

```bash
uv sync --dev
```

## CRISPR 和 SOP 生成

CRISPR 相关逻辑位于 `src/nutrimaster/experiment/crispr/`，通过 Agent 工具和 Web 路由暴露。它支持 gene/accession 查询、靶点设计、实验方案规划和物种特异 SOP 格式化。

模板位于：

```text
src/nutrimaster/experiment/crispr/templates/
```

转基因 相关逻辑位于 `src/nutrimaster/experiment/gene_transfer/`，通过 Agent 工具和 Web 路由暴露。它支持 gene/accession 查询、上下游序列截取、实验方案规划和物种特异 SOP 格式化。

模板位于：

```text
src/nutrimaster/experiment/gene_transfer/templates/
```

## 基因图谱可视化

`graphing/` 目录提供从主语料构建基因通路图谱并导出多种格式的工具脚本。

额外依赖：

```bash
pip install networkx matplotlib pyvis
```

常用命令（在仓库根目录执行）：

```bash
# 构建通路图，导出 GraphML / GML / JSON / 采样 PNG
python graphing/build_pathway_graph.py

# 快速生成完整通路图 PNG 预览（随机布局）
python graphing/build_fast_graph.py

# 生成完整静态 PNG（spring layout，较慢）
python graphing/build_full_static_graph.py

# 生成完整交互式 HTML
python graphing/build_full_graph_html.py

# 从 CSV 节点/边数据生成交互式 HTML
python graphing/visualize_gene_graph.py
```

输出产物位于 `graphing/output/`，包括 `.graphml`、`.gml`、`.json`、`.png` 和 `.html`。

输入数据约定：通路图脚本默认读取 `data/corpus/*.json` 中每篇文献的 `Pathway_Genes` 字段（`Gene_Name`、`Primary_Substrate`、`Primary_Product`）。

完整图规模较大，`build_full_static_graph.py` 和 `build_full_graph_html.py` 在远程机器上建议用 `tmux` 运行。

## 测试

运行测试：

```bash
uv run pytest
```

跳过需要真实外部服务的测试：

```bash
uv run pytest -m "not integration"
```

跳过浏览器 e2e 测试：

```bash
uv run pytest -m "not e2e"
```

常用局部测试：

```bash
uv run pytest tests/unit -q
uv run pytest src/nutrimaster/extraction/tests -q
```

集成测试需要真实 LLM、Jina 和 Supabase 配置。

E2E 测试需要先启动 Web 服务，并配置：

```bash
NUTRIMASTER_E2E_BASE_URL=...
NUTRIMASTER_E2E_EMAIL=...
NUTRIMASTER_E2E_PASSWORD=...
```

## NutriBench 评测框架

`eval/` 目录实现了基于 **task × model 正交分离** 设计的评测框架，用于对 NutriMaster 及对比 Agent 进行标准化问答评测。

支持的 Agent：`nutrimaster`（RAG 问答）、`llm`（通用 LLM 基线）、`evomaster`（工具使用）。

### 推荐流程

```bash
# 1. 从 Notion 下载题目到本地
python -m eval.main pull --after 2026-05-06

# 2. 本地评测（--resume 支持断点续传）
python -m eval.main nutrimaster v3 --resume

# 3. 预览后上传结果到 Notion
python -m eval.main push --dry-run
python -m eval.main push
```

### 常用选项

```bash
python -m eval.main nutrimaster v3                          # 单 Agent 评测
python -m eval.main nutrimaster v3 --max-questions 5        # 限制题目数（调试）
python -m eval.main nutrimaster v3 --resume --retry-failed  # 重跑失败题目
python -m eval.metrics.filter_stats --file eval/data/results/latest.jsonl --agent NutriMaster
```

断点续传默认每 5 题保存一次检查点；检查点管理详见 `python -m eval.checkpoint_cli`。完整用法见 `eval/README.md`。

## 协作开发

后续开发和维护请先阅读：

```text
DEVELOPMENT.md
```

其中包含模块边界、分支策略、数据目录约定、常见修改流程、测试策略和上线前检查清单。

## 开发注意事项

- `pyproject.toml` 中安装包名为 `nutrimaster`，命令行入口为 `nutrimaster`。
- 所有 Python 模块均位于 `src/nutrimaster/` 包下，通过 `from nutrimaster.xxx import ...` 导入。
- `data/corpus/` 是主语料；`data/index/` 是可再生成的索引状态。
- `data/personal_lib/` 和 `data/user_skills/` 是用户运行时数据。
- `src/nutrimaster/extraction/input/processed/`、`src/nutrimaster/extraction/output/`、token 报告、个人库、用户技能、日志和索引二进制产物默认不应提交。
- Admin 和 `src/nutrimaster/extraction/run.sh` 默认使用 v5 prompt/schema；直接 import `nutrimaster.extraction.config` 时会使用该文件自身默认值，必要时用环境变量显式覆盖。
- 生成新 verified JSON 时务必保持 `JSON_DIR=./data/corpus`。

## 典型本地流程

1. 安装依赖：

   ```bash
   uv sync --dev
   ```

2. 创建并填写 `.env`：

   ```bash
   cp .env.example .env
   ```

3. 检查服务配置：

   ```bash
   uv run nutrimaster check-config
   ```

4. 将 Markdown 论文放入 `src/nutrimaster/extraction/input/`。

5. 运行抽取：

   ```bash
   uv run nutrimaster extract
   ```

6. 启动 Web：

   ```bash
   uv run nutrimaster web
   ```

7. 打开：

   ```text
   http://localhost:5000
   http://localhost:5000/admin
   ```

## License

当前仓库尚未包含 license 文件。公开发布前请补充 license。
