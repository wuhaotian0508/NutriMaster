# Graphing 工具说明

这里集中放置项目里的画图、图谱构建和图谱导出相关文件。

## 目录结构

- `build_pathway_graph.py`：从 `data/corpus/*.json` 读取 `Pathway_Genes`，构建底物、基因、产物三类节点的通路图，并导出多种格式。
- `build_fast_graph.py`：生成完整通路图的快速 PNG 预览，使用随机布局，适合快速看整体规模。
- `build_full_static_graph.py`：生成完整通路图的静态 PNG，使用 spring layout，耗时更久但结构更清楚。
- `build_full_graph_html.py`：生成完整通路图的交互式 HTML。
- `visualize_gene_graph.py`：从 `graph_export/nodes.csv` 和 `graph_export/edges.csv` 生成 `gene_graph.html`。
- `graph_export/`：CSV 图数据和由 `visualize_gene_graph.py` 生成的交互式 HTML。
- `output/`：通路图生成结果，包括 PNG、HTML、GraphML、GML、JSON、edge list。
- `vendor/vis-9.1.2/`：本地保留的 vis-network 前端资源。

## 依赖

这些脚本主要依赖：

```bash
pip install networkx matplotlib pyvis
```

如果使用项目自己的环境，通常可以先进入仓库根目录并激活现有虚拟环境：

```bash
source .venv/bin/activate
```

## 常用命令

在仓库根目录运行：

```bash
python graphing/build_pathway_graph.py
```

生成：

- `graphing/output/pathway_graph.graphml`
- `graphing/output/pathway_graph.gml`
- `graphing/output/pathway_graph_edges.txt`
- `graphing/output/pathway_graph.json`
- `graphing/output/pathway_graph_sample.png`

快速生成完整静态预览：

```bash
python graphing/build_fast_graph.py
```

生成：

- `graphing/output/pathway_graph_fast.png`

生成更清晰但更慢的完整静态图：

```bash
python graphing/build_full_static_graph.py
```

生成：

- `graphing/output/pathway_graph_full_static.png`

生成完整交互式 HTML：

```bash
python graphing/build_full_graph_html.py
```

生成：

- `graphing/output/pathway_graph_full.html`

从 CSV 节点/边数据生成基因图 HTML：

```bash
python graphing/visualize_gene_graph.py
```

生成：

- `graphing/graph_export/gene_graph.html`

## 输入数据约定

通路图脚本默认读取：

```text
data/corpus/*.json
```

每个 JSON 文件中会读取 `Pathway_Genes` 字段，并使用：

- `Gene_Name`
- `Primary_Substrate`
- `Primary_Product`

`visualize_gene_graph.py` 默认读取：

```text
graphing/graph_export/nodes.csv
graphing/graph_export/edges.csv
```

## 运行提醒

完整图比较大，`build_full_static_graph.py` 和 `build_full_graph_html.py` 可能耗时较长。远程机器上建议用 `tmux` 跑：

```bash
tmux new -s graphing
python graphing/build_full_static_graph.py
```
