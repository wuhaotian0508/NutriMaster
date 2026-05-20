# CitationRegistry 工作流详解

## 核心问题

在 NutriMaster Agent 的一次对话中，用户可能问多个问题，Agent 会多次调用 `rag_search` 工具检索文献。每次 RAG 搜索都会返回一个 `EvidencePacket`，其中包含若干 `EvidenceItem`（文献证据）。

**问题**：每次搜索的编号都是独立的（[1], [2], [3]...），如果不统一管理，会导致：
- 同一篇文献在不同搜索中被分配不同编号
- 用户看到的引用编号混乱（如 "[2] Paper A" 和 "[5] Paper A" 实际是同一篇文献）
- 无法追踪文献的真实来源

## 解决方案：CitationRegistry

`CitationRegistry` 是一个**全局编号注册表**，在一次 `Agent.run()` 生命周期内维护文献到编号的映射。

### 核心数据结构

```python
self._ids_by_key: dict[tuple[str, str], str] = {}
```

- **Key**: `(标识类型, 标识值)`
  - 例如：`("doi", "10.1038/nature12345")`
  - 例如：`("pmid", "12345678")`
  - 例如：`("title", "normalized title text")`
  
- **Value**: 全局编号字符串
  - 例如：`"1"`, `"2"`, `"3"`...

### 文献唯一标识优先级

通过 `evidence_key()` 函数生成，优先级从高到低：

1. **DOI** - 最可靠的唯一标识符
2. **PMID** - PubMed ID
3. **URL** - 文献链接
4. **Title** - 标题（归一化后：小写、去标点、去空格）

## 完整工作流

### 1. Agent 初始化阶段

```python
# src/nutrimaster/agent/agent.py:135
citation_registry = CitationRegistry()  # 创建全局注册表
evidence_packets: list[EvidencePacket] = []  # 存储所有证据包
```

### 2. 第一次 RAG 搜索

**用户问题**："维生素C合成相关基因有哪些？"

**RAG 返回**（局部编号）：
```
[1] Paper A: "Ascorbic acid biosynthesis in plants"
    DOI: 10.1038/nature001
[2] Paper B: "Vitamin C metabolism"
    DOI: 10.1016/cell002
[3] Paper C: "L-galactose pathway"
    DOI: 10.1093/pcp003
```

**Registry 处理**：
```python
# agent.py:177
global_packet = citation_registry.assign_packet(result)
```

**内部映射表状态**：
```python
_ids_by_key = {
    ("doi", "10.1038/nature001"): "1",
    ("doi", "10.1016/cell002"): "2",
    ("doi", "10.1093/pcp003"): "3",
}
```

**输出给 LLM**（全局编号）：
```
[1] Paper A: "Ascorbic acid biosynthesis in plants"
[2] Paper B: "Vitamin C metabolism"
[3] Paper C: "L-galactose pathway"
```

### 3. 第二次 RAG 搜索

**用户追问**："Paper B 中提到的 GDP-mannose 途径是什么？"

**RAG 返回**（新的局部编号）：
```
[1] Paper B: "Vitamin C metabolism"  # 重复文献！
    DOI: 10.1016/cell002
[2] Paper D: "GDP-mannose biosynthesis"
    DOI: 10.1105/tpc004
[3] Paper E: "Mannose metabolism in Arabidopsis"
    DOI: 10.1111/tpj005
```

**Registry 处理**：

对于 Paper B：
```python
key = ("doi", "10.1016/cell002")
# key 已存在于 _ids_by_key，复用旧编号 "2"
source_id = self._ids_by_key.setdefault(key, ...)  # 返回 "2"
```

对于 Paper D：
```python
key = ("doi", "10.1105/tpc004")
# key 不存在，分配新编号 len(_ids_by_key) + 1 = 4
source_id = self._ids_by_key.setdefault(key, "4")  # 返回 "4"
```

对于 Paper E：
```python
key = ("doi", "10.1111/tpj005")
# key 不存在，分配新编号 5
source_id = self._ids_by_key.setdefault(key, "5")  # 返回 "5"
```

**内部映射表状态**：
```python
_ids_by_key = {
    ("doi", "10.1038/nature001"): "1",
    ("doi", "10.1016/cell002"): "2",  # 复用
    ("doi", "10.1093/pcp003"): "3",
    ("doi", "10.1105/tpc004"): "4",  # 新增
    ("doi", "10.1111/tpj005"): "5",  # 新增
}
```

**输出给 LLM**（全局编号）：
```
[2] Paper B: "Vitamin C metabolism"  # 编号保持一致！
[4] Paper D: "GDP-mannose biosynthesis"
[5] Paper E: "Mannose metabolism in Arabidopsis"
```

### 4. 最终引用输出

```python
# agent.py:200
citations = self._filter_citations(answer_text, evidence_packets)
```

用户看到的引用列表：
```
[1] Paper A: "Ascorbic acid biosynthesis in plants"
[2] Paper B: "Vitamin C metabolism"
[3] Paper C: "L-galactose pathway"
[4] Paper D: "GDP-mannose biosynthesis"
[5] Paper E: "Mannose metabolism in Arabidopsis"
```

## 关键代码位置

| 文件 | 行号 | 功能 |
|------|------|------|
| `src/nutrimaster/rag/evidence.py` | 240-278 | `CitationRegistry` 类定义 |
| `src/nutrimaster/rag/evidence.py` | 58-68 | `evidence_key()` 生成唯一标识 |
| `src/nutrimaster/agent/agent.py` | 135 | 创建 Registry 实例 |
| `src/nutrimaster/agent/agent.py` | 177 | 调用 `assign_packet()` |
| `src/nutrimaster/agent/agent.py` | 200 | 过滤和输出最终引用 |

## 边界情况处理

### 1. 无法识别的文献（无 DOI/PMID/URL/标题）
```python
if key == ("title", ""):
    source_id = item.source_id  # 保留原始编号
```

### 2. 标题归一化
```python
# evidence.py:51-55
def title_key(value: object) -> str:
    title = clean_text(value).lower()
    title = re.sub(r"<[^>]+>", " ", title)  # 去除 HTML 标签
    title = re.sub(r"[\W_]+", " ", title)   # 去除标点符号
    return re.sub(r"\s+", " ", title).strip()  # 合并空格
```

### 3. DOI 归一化
```python
# evidence.py:27-35
def normalize_doi(value: object) -> str:
    doi = clean_text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)  # 去除前缀
    doi = doi.removeprefix("doi:").strip()
    return doi
```

## 测试用例

参考 `tests/unit/test_new_rag_harness_contract.py:test_citation_registry_assigns_global_ids_across_packets_and_reuses_duplicates()`

## 总结

`CitationRegistry` 通过维护一个**文献唯一标识 → 全局编号**的映射表，确保：

1. ✅ 同一文献在多次搜索中使用相同编号
2. ✅ 新文献按出现顺序递增编号
3. ✅ 用户看到的引用编号连贯一致
4. ✅ 可追溯每个编号对应的真实文献来源
