#!/usr/bin/env python3
import json
from pathlib import Path
from pyvis.network import Network

GRAPHING_DIR = Path(__file__).resolve().parent
REPO_ROOT = GRAPHING_DIR.parent

corpus_dir = REPO_ROOT / "data" / "corpus"
output_file = GRAPHING_DIR / "output" / "pathway_graph_full.html"
output_file.parent.mkdir(parents=True, exist_ok=True)

net = Network(height="100vh", width="100%", directed=True, bgcolor="#1a1a2e", font_color="white")
net.barnes_hut(gravity=-5000, central_gravity=0.3, spring_length=100)

added_nodes = set()

def add_node(name, node_type):
    if name in added_nodes:
        return
    added_nodes.add(name)
    if node_type == "gene":
        net.add_node(name, label=name, color="#4fc3f7", shape="ellipse", size=15)
    elif node_type == "substrate":
        net.add_node(name, label=name, color="#81c784", shape="box", size=10)
    else:
        net.add_node(name, label=name, color="#e57373", shape="box", size=10)

print("Loading corpus...")
for json_file in corpus_dir.glob("*.json"):
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        for gene in data.get("Pathway_Genes", []):
            gene_name = gene.get("Gene_Name", "")
            if not gene_name or gene_name == "NA":
                continue

            add_node(gene_name, "gene")

            for s in (gene.get("Primary_Substrate") or "").split(";"):
                s = s.strip()
                if s and s != "NA":
                    add_node(s, "substrate")
                    net.add_edge(s, gene_name, color="#aaaaaa", width=1)

            for p in (gene.get("Primary_Product") or "").split(";"):
                p = p.strip()
                if p and p != "NA":
                    add_node(p, "product")
                    net.add_edge(gene_name, p, color="#aaaaaa", width=1)
    except Exception:
        pass

print(f"Nodes: {len(net.nodes)}, Edges: {len(net.edges)}")
print("Generating HTML (this may take a while)...")
net.show_buttons(filter_=["physics"])
net.save_graph(output_file)
print(f"Saved: {output_file}")
