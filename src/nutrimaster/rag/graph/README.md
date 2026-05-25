# NutriMaster Graph RAG

这个目录实现结构化基因语料的图 RAG。图 RAG 不是替代 embedding/BM25，而是补充它：
embedding/BM25 擅长召回长文本解释，图擅长回答“谁调控谁”“这个基因到这个代谢物之间有没有证据链”。

## 一个节点是什么

节点是从 `data/corpus/*.json` 字段中抽出来的实体，不是一篇论文，也不是一个 chunk。

常见节点：

```text
(:Gene {name: "HY5", species: "Solanum lycopersicum"})
(:Gene {name: "PSY1", species: "Solanum lycopersicum"})
(:Metabolite {name: "lycopene"})
(:Pathway {name: "Carotenoid biosynthesis"})
(:Signal {name: "Light"})
(:Process {name: "carotenoid biosynthesis"})
(:Reaction {name: "PSY1: substrate -> product"})
```

`Gene` 节点用 `name + species` 去重，因为 `PAL`、`CHS`、`DFR`、`HY5` 这类短名会跨物种重复。
代谢物和通路默认按名字合并，这样不同论文中的同一个产物可以连到一起。

## 一条边是什么

边是两个节点之间的有方向关系，并带有来源证据。方向表示生物学语义方向，不表示检索只能朝这个方向走。

例如 Regulation：

```text
(:Signal {name: "Light"}) -[:UPSTREAM_SIGNAL_OF]-> (:Gene {name: "HY5"})
(:Gene {name: "HY5"}) -[:REGULATES]-> (:Gene {name: "PSY1"})
(:Gene {name: "HY5"}) -[:CONTROLS]-> (:Process {name: "carotenoid biosynthesis"})
(:Gene {name: "HY5"}) -[:AFFECTS]-> (:Metabolite {name: "lycopene"})
```

例如 Pathway：

```text
(:Metabolite {name: "L-glutamate"}) -[:INPUT_OF]-> (:Reaction {name: "MdGAD1: L-glutamate -> GABA"})
(:Gene {name: "MdGAD1"}) -[:CATALYZES]-> (:Reaction {name: "MdGAD1: L-glutamate -> GABA"})
(:Reaction {name: "MdGAD1: L-glutamate -> GABA"}) -[:PRODUCES]-> (:Metabolite {name: "GABA"})
(:Gene {name: "MdGAD1"}) -[:PARTICIPATES_IN]-> (:Pathway {name: "GABA biosynthesis"})
(:Gene {name: "MdGAD1"}) -[:CONTRIBUTES_TO]-> (:Metabolite {name: "γ-Aminobutyric acid (GABA)"})
```

每条边会保存：

```text
doi, title, journal, source_file, section, record_index,
summary, validation, species, domain
```

所以 agent 不只是看到 `HY5 -> PSY1`，还会看到这条关系来自哪篇论文、验证方法是什么、摘要结论是什么。

## Pathway 和 Regulation 如何建图

`Pathway_Genes` 描述的是酶、反应、底物、产物、通路：

```text
substrate -[:INPUT_OF]-> reaction
gene -[:CATALYZES]-> reaction
reaction -[:PRODUCES]-> product
gene -[:PARTICIPATES_IN]-> pathway
gene -[:CONTRIBUTES_TO]-> terminal_metabolite
```

这里用 `Reaction` 中间节点，而不是直接 `Gene -> Product`，因为“基因催化反应”和“反应产生产物”是两件事。
这样问“这个产物上游有哪些底物/酶”时，路径也更清楚。

`Regulation_Genes` 描述的是调控者、靶基因、信号、过程、终产物：

```text
regulator_gene -[:REGULATES]-> target_gene
signal -[:UPSTREAM_SIGNAL_OF]-> regulator_gene
regulator_gene -[:CONTROLS]-> process
regulator_gene -[:AFFECTS]-> terminal_metabolite
```

所以如果用户问“谁调控 PSY1”，图检索会从 `PSY1` 反向找 `REGULATES` 的起点；
如果用户问“HY5 如何影响 lycopene”，图检索会优先找 `HY5` 到 `lycopene` 的受限路径。

## 建图

SQLite fallback：

```bash
nutrimaster-build-graph-index --backend sqlite --corpus data/corpus --out data/index/graph_index.sqlite
```

Neo4j：

```bash
pip install -e .

export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='your-password'

nutrimaster-build-graph-index --backend neo4j --corpus data/corpus
```

如果只想增量覆盖同一批节点关系、不清空旧图：

```bash
nutrimaster-build-graph-index --backend neo4j --corpus data/corpus --no-reset
```

## 接入 RAG

默认使用 SQLite fallback。如果要让运行时 RAG 用 Neo4j：

```bash
export NUTRIMASTER_GRAPH_BACKEND=neo4j
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='your-password'
```

启动 Web 时自动建图：

```bash
export NUTRIMASTER_WEB_BUILD_GRAPH=1
```

关闭图 RAG：

```bash
export NUTRIMASTER_GRAPH_BACKEND=off
```

Neo4j 不可用时，`rag_search` 会继续返回 PubMed/GeneDB 结果，只是没有 graph evidence。

## Neo4j Browser 常用查询

看某个基因附近 2 跳：

```cypher
MATCH path = (g:Gene {name: "HY5"})-[*1..2]-(n)
RETURN path
LIMIT 50;
```

看谁调控 PSY1：

```cypher
MATCH path = (regulator:Gene)-[:REGULATES]->(target:Gene)
WHERE target.name = "PSY1"
RETURN path
LIMIT 25;
```

看 HY5 到 lycopene 的机制路径：

```cypher
MATCH path = (:Gene {name: "HY5"})
  -[:REGULATES|CONTROLS|AFFECTS|CATALYZES|PRODUCES|PARTICIPATES_IN|CONTRIBUTES_TO*1..4]-
  (:Metabolite {name: "lycopene"})
WHERE NONE(n IN nodes(path)[1..-1] WHERE coalesce(n.type, "") = "Species")
RETURN path
ORDER BY length(path)
LIMIT 10;
```

看某篇论文贡献了哪些边：

```cypher
MATCH path = (a:GraphNode)-[r]->(b:GraphNode)
WHERE r.doi = "10.1002/advs.202500110"
RETURN path
LIMIT 100;
```

## 代码入口

- `schema.py`：图字段白名单、节点/关系类型、清洗和 ID 工具。
- `extract.py`：从用户问题抽取实体、方向、target、物种和字段 hint。
- `neo4j_store.py`：初始化 Neo4j schema，并从 corpus 写入节点和边。
- `resolver.py`：exact/fulltext/fuzzy 节点解析。
- `path_search.py`：受限 Cypher 路径和邻域搜索。
- `source.py`：把路径渲染成 `EvidenceItem`，接入 `RAGSearchService`。
- `index.py`：SQLite fallback 图索引。
