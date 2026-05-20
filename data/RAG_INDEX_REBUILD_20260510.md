# RAG 索引重建总结 - 2026-05-10

## 目标

为当前本地语料目录完整重建 RAG 向量索引：

```bash
/data/haotianwu/biojson/data/corpus/
```

本次需要修复并重新生成的核心索引文件是：

```bash
data/index/manifest.json
data/index/embeddings.npy
data/index/chunks.pkl
```

成功标准：

- corpus 文件数等于 manifest 中记录的文件数。
- chunks 数量等于 embeddings 的行数。
- `JinaRetriever` 可以正常加载索引，且 `load_error` 为 `None`。
- 随机执行一次向量检索，能返回非空结果。

## 最终结果

本次重建已成功完成。

```text
corpus files: 18580
manifest files: 18580
chunks: 102977
embeddings shape: (102977, 1024)
embeddings dtype: float32
```

重建后 `JinaRetriever.index_status()` 关键结果：

```text
chunks_loaded: 102977
embeddings_loaded: 102977
manifest_file_exists: True
load_error: None
```

测试检索问题：

```text
rice grain yield salt stress gene
```

测试检索返回结果：

```text
1 0.7062 OsASR5 10.1016/j.plaphy.2021.04.012 gene
2 0.6694 TaSTRG NA gene
3 0.6659 OsNHX1 10.1093/jxb/erv142 gene
```

## 本次改动或生成的文件

本次没有修改项目源代码。

重新生成或替换的索引文件：

```bash
data/index/manifest.json
data/index/embeddings.npy
data/index/chunks.pkl
```

重建前创建的旧索引备份目录：

```bash
data/index-backup-before-full-rebuild-20260510000511/
```

保留在项目根目录的日志文件：

```bash
rebuild_full_index.log
rebuild_full_index.failed-no-proxy-20260510001051.log
rebuild_full_index.slow-per-file-20260510001246.log
rebuild_full_index.batch32-interrupted-20260510002815.log
```

执行过程中临时写入 `/tmp` 的脚本如下。这些脚本不是项目源码的一部分：

```bash
/tmp/biojson_rebuild_full_index.sh
/tmp/biojson_rebuild_full_index_fast.sh
/tmp/biojson_rebuild_full_index_fast.py
```

## 初始状态

重建前，本地索引是不完整且不一致的：

- `data/index/manifest.json` 缺失。
- `data/index/embeddings.npy` 缺失。
- `data/index/chunks.pkl` 虽然存在，但属于旧状态，和当前 corpus / chunker 不一致。
- `data/index/bm25.pkl` 虽然存在，但记录的 chunk 数也和当前向量索引状态不一致。

重建前 `JinaRetriever.index_status()` 显示：

```text
corpus_files: 18580
manifest_files: None
chunks_loaded: 0
embeddings_loaded: 0
chunks_file_exists: True
embeddings_file_exists: False
manifest_file_exists: False
load_error: None
```

## 实际运行过的命令和代码

### 1. 检查当前状态

检查项目路径、文件列表、corpus 和 index 状态：

```bash
pwd
rg --files | sed -n '1,120p'
ls -lah data/index data/corpus
find data/corpus -maxdepth 1 -name '*_nutri_plant_verified.json' | wc -l
find data/index -maxdepth 1 -type f -printf '%f %s bytes\n' | sort
```

检查 Jina API Key、tmux、Python 环境：

```bash
if [ -f .env ] && grep -q '^JINA_API_KEY=' .env; then echo yes; else echo no; fi
command -v tmux
command -v python3
```

检查旧索引能否被 `JinaRetriever` 加载：

```bash
python3 - <<'PY'
from pathlib import Path
from nutrimaster.rag.jina import JinaRetriever

r = JinaRetriever(index_path=Path('data/index'), data_dir=Path('data/corpus'))
print(r.index_status())
PY
```

### 2. 备份旧索引

重建前先备份整个 `data/index/`：

```bash
stamp=$(date +%Y%m%d%H%M%S)
backup="data/index-backup-before-full-rebuild-$stamp"
mkdir -p "$backup"
rsync -a data/index/ "$backup"/
```

实际备份目录：

```bash
data/index-backup-before-full-rebuild-20260510000511/
```

### 3. 第一次尝试：直接调用现有 force rebuild 逻辑

最开始按照原计划，尝试使用项目中已有的 `JinaRetriever.build_index(force=True)`：

```python
from pathlib import Path
from nutrimaster.rag.jina import JinaRetriever

retriever = JinaRetriever(index_path=Path("data/index"), data_dir=Path("data/corpus"))
print("initial_status:", retriever.index_status(), flush=True)

retriever.build_index(
    data_dir=Path("data/corpus"),
    incremental=True,
    force=True,
)

print("chunks:", len(retriever.chunks), flush=True)
print("embeddings:", None if retriever.embeddings is None else retriever.embeddings.shape, flush=True)
print("status:", retriever.index_status(), flush=True)
```

该代码放在 tmux 中运行。但是第一次卡住，因为 tmux 会话没有继承当前 shell 的代理环境，导致 Jina API 请求直连异常。

后来在 tmux 启动脚本中显式加入代理环境变量：

```bash
export HTTP_PROXY="http://127.0.0.1:7899"
export HTTPS_PROXY="http://127.0.0.1:7899"
export http_proxy="http://127.0.0.1:7899"
export https_proxy="http://127.0.0.1:7899"
export ALL_PROXY="socks5://127.0.0.1:7899"
export all_proxy="socks5://127.0.0.1:7899"
```

代理修复后，这条路径可以继续运行，但速度太慢。原因是现有 force rebuild 流程基本按文件逐个生成 embedding，请求次数太多，不适合当前 18,580 个文件规模。

### 4. 最终成功方案：批量重建索引

最终改用临时批处理脚本完成重建。

脚本做了以下事情：

1. 读取所有 corpus JSON 文件。
2. 使用项目现有 `chunk_paper` 分块逻辑生成所有 chunks。
3. 根据文件 sha、chunk 范围和 chunker 版本生成 manifest。
4. 将所有 chunk 文本按较大的 batch 调用 Jina embedding API。
5. 将 embedding 写入临时 `.npy` memmap 文件。
6. 写入临时 `chunks.pkl` 和 `manifest.json`。
7. 全部成功后，再用原子替换方式替换正式索引文件。
8. 最后执行数量校验、retriever 加载校验和测试检索。

最终通过 tmux 启动：

```bash
tmux new-session -d -s rebuild-rag-index 'bash /tmp/biojson_rebuild_full_index_fast.sh'
```

核心路径和参数：

```python
from pathlib import Path

ROOT = Path('/data/haotianwu/biojson')
DATA_DIR = ROOT / 'data/corpus'
INDEX_DIR = ROOT / 'data/index'
BATCH_SIZE = 128
```

构建 chunks 和 manifest 的核心代码：

```python
from nutrimaster.rag.gene_index import CHUNKER_VERSION, chunk_paper, sha256_of

files = sorted(DATA_DIR.glob('*_nutri_plant_verified.json'))

all_chunks = []
manifest_files = {}
cursor = 0

for path in files:
    with path.open('r', encoding='utf-8') as f:
        paper = json.load(f)

    chunks = chunk_paper(paper) or []
    start = cursor
    all_chunks.extend(chunks)
    cursor += len(chunks)

    manifest_files[path.name] = {
        'sha': sha256_of(path),
        'chunker_version': CHUNKER_VERSION,
        'n_chunks': len(chunks),
        'start': start,
        'end': cursor,
    }
```

调用 Jina embedding API 的核心 payload：

```python
payload = {
    'model': settings.rag.embedding_model,
    'input': texts,
    'task': 'retrieval.passage',
}

response = requests.post(
    settings.rag.jina_embedding_url,
    json=payload,
    headers=headers,
    timeout=120,
)
```

创建 embedding 临时 memmap：

```python
mmap = np.lib.format.open_memmap(
    tmp_embeds,
    mode='w+',
    dtype=np.float32,
    shape=(n_chunks, dim),
)

mmap[embedded:end] = vectors
```

全部成功后，原子替换正式索引文件：

```python
with tmp_chunks.open('wb') as f:
    pickle.dump(all_chunks, f, protocol=pickle.HIGHEST_PROTOCOL)

tmp_manifest.write_text(
    json.dumps(
        {'chunker_version': CHUNKER_VERSION, 'files': manifest_files},
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)

os.replace(tmp_embeds, INDEX_DIR / 'embeddings.npy')
os.replace(tmp_chunks, INDEX_DIR / 'chunks.pkl')
os.replace(tmp_manifest, INDEX_DIR / 'manifest.json')
```

## 验证命令

### 1. 运行索引状态检查脚本

```bash
python3 check_index_status.py
```

输出摘要：

```text
Corpus 文件数: 18,580
Manifest 已索引文件数: 18,580
缺失: 0
同步率: 100.00%
状态: 完全同步
```

### 2. 数量一致性检查

```bash
python3 - <<'PY'
import json
import pickle
import numpy as np
from pathlib import Path

manifest = json.loads(Path('data/index/manifest.json').read_text())
chunks = pickle.loads(Path('data/index/chunks.pkl').read_bytes())
embeddings = np.load('data/index/embeddings.npy', mmap_mode='r')
corpus_files = len(list(Path('data/corpus').glob('*_nutri_plant_verified.json')))

print('corpus files:', corpus_files)
print('manifest files:', len(manifest['files']))
print('chunks:', len(chunks))
print('embeddings shape:', embeddings.shape)

assert len(manifest['files']) == corpus_files
assert len(chunks) == embeddings.shape[0]
PY
```

验证输出：

```text
corpus files: 18580
manifest files: 18580
chunks: 102977
embeddings shape: (102977, 1024)
```

### 3. Retriever 加载和检索检查

```bash
python3 - <<'PY'
from pathlib import Path
from nutrimaster.rag.jina import JinaRetriever

r = JinaRetriever(index_path=Path('data/index'), data_dir=Path('data/corpus'))

print('status:', r.index_status())

results = r.search('rice grain yield salt stress gene', top_k=3)
print('sample results:', len(results))

for i, (chunk, score) in enumerate(results, 1):
    print(i, round(score, 4), chunk.gene_name, chunk.doi, chunk.chunk_type)

assert results
PY
```

验证输出：

```text
status: {
  'data_dir': 'data/corpus',
  'index_dir': 'data/index',
  'corpus_files': 18580,
  'manifest_files': 18580,
  'chunks_loaded': 102977,
  'embeddings_loaded': 102977,
  'chunks_file_exists': True,
  'embeddings_file_exists': True,
  'manifest_file_exists': True,
  'load_error': None
}

sample results: 3
1 0.7062 OsASR5 10.1016/j.plaphy.2021.04.012 gene
2 0.6694 TaSTRG NA gene
3 0.6659 OsNHX1 10.1093/jxb/erv142 gene
```

## 经验教训

- tmux 不一定继承当前 shell 的代理环境。涉及外部 API 的长任务，最好在 tmux 启动脚本里显式导出 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 以及对应小写变量。
- 项目现有的 `JinaRetriever.build_index(force=True)` 逻辑功能上是正确的，但对本次 18,580 个文件规模来说太慢，因为它接近按文件逐个 embedding，请求次数太多。
- 大规模重建时，更合适的方式是先在本地完成全部 chunk，再按较大 batch 统一请求 embedding API。
- 大索引写入应使用临时文件和原子替换。这样即使中途失败，也不会污染正式的 `embeddings.npy`、`chunks.pkl` 和 `manifest.json`。
- 重建前必须完整备份旧索引，即使旧索引已经明显不一致，也应该保留回滚依据。
- 校验不能只看文件是否存在，还要检查数量一致性、retriever 加载状态和真实检索结果。
- 本次没有重建 `bm25.pkl`。当前向量检索路径依赖的是 `manifest.json`、`embeddings.npy`、`chunks.pkl` 三件套。

## 下次重建建议流程

建议后续完整重建时按以下流程执行：

1. 确认没有其他任务正在同步或修改 `data/corpus/`。
2. 备份整个 `data/index/`。
3. 在 tmux 中执行批量重建脚本，并显式设置代理环境变量。
4. 使用临时文件写入，完成后再原子替换正式索引文件。
5. 运行 `python3 check_index_status.py`。
6. 校验 `len(chunks) == embeddings.shape[0]`。
7. 校验 `JinaRetriever.index_status()`。
8. 执行一次真实检索，确认返回非空结果。

本次成功日志：

```bash
rebuild_full_index.log
```
