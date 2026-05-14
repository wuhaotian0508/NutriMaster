#!/usr/bin/env python3
import json
import networkx as nx
import matplotlib.pyplot as plt
from pathlib import Path

GRAPHING_DIR = Path(__file__).resolve().parent
REPO_ROOT = GRAPHING_DIR.parent

corpus_dir = REPO_ROOT / "data" / "corpus"
output_file = GRAPHING_DIR / "output" / "pathway_graph_fast.png"
output_file.parent.mkdir(parents=True, exist_ok=True)

print("Building graph...")
G = nx.DiGraph()

for json_file in corpus_dir.glob("*.json"):
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        for gene in data.get("Pathway_Genes", []):
            gene_name = gene.get("Gene_Name", "")
            if not gene_name or gene_name == "NA":
                continue

            G.add_node(gene_name, node_type="gene")

            for s in (gene.get("Primary_Substrate") or "").split(";"):
                s = s.strip()
                if s and s != "NA":
                    G.add_node(s, node_type="substrate")
                    G.add_edge(s, gene_name)

            for p in (gene.get("Primary_Product") or "").split(";"):
                p = p.strip()
                if p and p != "NA":
                    G.add_node(p, node_type="product")
                    G.add_edge(gene_name, p)
    except Exception:
        pass

print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
print("Computing layout (using fast random layout)...")

# 使用随机布局，速度快
pos = nx.random_layout(G, seed=42)

print("Drawing...")
plt.figure(figsize=(50, 50), dpi=100)

# 按类型分组节点
gene_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "gene"]
substrate_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "substrate"]
product_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "product"]

# 绘制边
nx.draw_networkx_edges(G, pos, edge_color="#dddddd", arrows=False,
                       width=0.2, alpha=0.2)

# 绘制节点（分类型）
nx.draw_networkx_nodes(G, pos, nodelist=gene_nodes,
                       node_color="#4fc3f7", node_shape="o",
                       node_size=10, alpha=0.7, label="Gene (蓝色圆)")
nx.draw_networkx_nodes(G, pos, nodelist=substrate_nodes,
                       node_color="#81c784", node_shape="s",
                       node_size=8, alpha=0.7, label="Substrate (绿色方)")
nx.draw_networkx_nodes(G, pos, nodelist=product_nodes,
                       node_color="#e57373", node_shape="s",
                       node_size=8, alpha=0.7, label="Product (红色方)")

plt.title(f"Pathway Gene Network\n{G.number_of_nodes()} nodes, {G.number_of_edges()} edges",
          fontsize=80, pad=30)
plt.legend(fontsize=50, markerscale=8, loc='upper right')
plt.axis("off")
plt.tight_layout()

print(f"Saving to {output_file}...")
plt.savefig(output_file, dpi=100, bbox_inches="tight", facecolor="white")
print("Done!")
