#!/usr/bin/env python3
"""
Build pathway gene graph from corpus data.

Graph structure:
- Nodes: Gene_Name (blue ellipse), Primary_Substrate, Primary_Product
- Edges: substrate -> gene -> product
"""

import json
from pathlib import Path
import networkx as nx
import matplotlib.pyplot as plt
from typing import Dict, List


GRAPHING_DIR = Path(__file__).resolve().parent
REPO_ROOT = GRAPHING_DIR.parent
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "corpus"
DEFAULT_OUTPUT_DIR = GRAPHING_DIR / "output"


def load_corpus_data(corpus_dir: str) -> List[Dict]:
    """Load all JSON files from corpus directory."""
    corpus_path = Path(corpus_dir)
    all_genes = []

    for json_file in corpus_path.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                pathway_genes = data.get("Pathway_Genes", [])
                all_genes.extend(pathway_genes)
        except Exception as e:
            print(f"Error loading {json_file}: {e}")

    return all_genes


def sanitize_string(s: str) -> str:
    """Remove control characters and NULL bytes for XML compatibility."""
    if not isinstance(s, str):
        return str(s)
    # Remove NULL bytes and control characters except newline, tab, carriage return
    return ''.join(c for c in s if c == '\n' or c == '\t' or c == '\r' or (ord(c) >= 32 and ord(c) != 127))


def build_graph(genes: List[Dict]) -> nx.DiGraph:
    """Build directed graph from pathway genes.

    Each gene creates:
    - substrate -> gene edge
    - gene -> product edge
    """
    G = nx.DiGraph()

    for gene in genes:
        gene_name = gene.get("Gene_Name")
        if not gene_name or gene_name == "NA":
            continue

        # Sanitize gene attributes for XML export
        sanitized_gene = {k: sanitize_string(v) if isinstance(v, str) else v
                         for k, v in gene.items()}

        # Add gene node
        G.add_node(gene_name, node_type="gene", **sanitized_gene)

        # Add substrate edges
        substrates = gene.get("Primary_Substrate", "")
        if substrates and substrates != "NA":
            # Split multiple substrates by semicolon
            for substrate in substrates.split(";"):
                substrate = substrate.strip()
                if substrate:
                    G.add_node(substrate, node_type="substrate")
                    G.add_edge(substrate, gene_name, edge_type="substrate_to_gene")

        # Add product edges
        products = gene.get("Primary_Product", "")
        if products and products != "NA":
            # Split multiple products by semicolon
            for product in products.split(";"):
                product = product.strip()
                if product:
                    G.add_node(product, node_type="product")
                    G.add_edge(gene_name, product, edge_type="gene_to_product")

    return G


def analyze_graph(G: nx.DiGraph) -> Dict:
    """Analyze graph statistics."""
    gene_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "gene"]
    substrate_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "substrate"]
    product_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "product"]

    stats = {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "gene_nodes": len(gene_nodes),
        "substrate_nodes": len(substrate_nodes),
        "product_nodes": len(product_nodes),
        "connected_components": nx.number_weakly_connected_components(G),
        "avg_degree": sum(dict(G.degree()).values()) / G.number_of_nodes() if G.number_of_nodes() > 0 else 0,
    }

    return stats


def export_graph(G: nx.DiGraph, output_dir: str):
    """Export graph in multiple formats."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Create a clean graph for XML export (node IDs only, no attributes with control chars)
    G_clean = nx.DiGraph()
    for node in G.nodes():
        clean_node_id = sanitize_string(str(node))
        G_clean.add_node(clean_node_id, node_type=G.nodes[node].get("node_type", "unknown"))

    for u, v in G.edges():
        clean_u = sanitize_string(str(u))
        clean_v = sanitize_string(str(v))
        G_clean.add_edge(clean_u, clean_v, edge_type=G.edges[u, v].get("edge_type", "unknown"))

    # Export as GraphML (simplified version without full attributes)
    graphml_file = output_path / "pathway_graph.graphml"
    nx.write_graphml(G_clean, graphml_file)
    print(f"Exported GraphML: {graphml_file}")

    # Export as GML (simpler format)
    gml_file = output_path / "pathway_graph.gml"
    try:
        nx.write_gml(G_clean, gml_file)
        print(f"Exported GML: {gml_file}")
    except Exception as e:
        print(f"Warning: Could not export GML: {e}")

    # Export edge list
    edgelist_file = output_path / "pathway_graph_edges.txt"
    nx.write_edgelist(G, edgelist_file, data=True)
    print(f"Exported edge list: {edgelist_file}")

    # Export as JSON (for web visualization)
    json_file = output_path / "pathway_graph.json"
    from networkx.readwrite import json_graph
    graph_data = json_graph.node_link_data(G)
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(graph_data, f, indent=2, ensure_ascii=False)
    print(f"Exported JSON: {json_file}")


def visualize_sample(G: nx.DiGraph, output_dir: str, sample_size: int = 50):
    """Visualize a sample subgraph."""
    # Sample genes
    gene_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "gene"]
    if len(gene_nodes) > sample_size:
        import random
        sampled_genes = random.sample(gene_nodes, sample_size)
    else:
        sampled_genes = gene_nodes

    # Get subgraph with neighbors
    nodes_to_include = set(sampled_genes)
    for gene in sampled_genes:
        nodes_to_include.update(G.predecessors(gene))
        nodes_to_include.update(G.successors(gene))

    subgraph = G.subgraph(nodes_to_include)

    # Create visualization
    plt.figure(figsize=(20, 15))
    pos = nx.spring_layout(subgraph, k=2, iterations=50)

    # Color nodes by type
    node_colors = []
    for node in subgraph.nodes():
        node_type = subgraph.nodes[node].get("node_type", "unknown")
        if node_type == "gene":
            node_colors.append("lightblue")
        elif node_type == "substrate":
            node_colors.append("lightgreen")
        elif node_type == "product":
            node_colors.append("lightcoral")
        else:
            node_colors.append("gray")

    # Draw
    nx.draw_networkx_nodes(subgraph, pos, node_color=node_colors, node_size=500, alpha=0.8)
    nx.draw_networkx_edges(subgraph, pos, edge_color="gray", arrows=True, arrowsize=10, alpha=0.5)
    nx.draw_networkx_labels(subgraph, pos, font_size=6)

    plt.title(f"Pathway Gene Network (Sample of {len(sampled_genes)} genes)")
    plt.axis("off")
    plt.tight_layout()

    output_path = Path(output_dir)
    viz_file = output_path / "pathway_graph_sample.png"
    plt.savefig(viz_file, dpi=150, bbox_inches="tight")
    print(f"Saved visualization: {viz_file}")
    plt.close()


def main():
    corpus_dir = DEFAULT_CORPUS_DIR
    output_dir = DEFAULT_OUTPUT_DIR

    print("Loading corpus data...")
    genes = load_corpus_data(corpus_dir)
    print(f"Loaded {len(genes)} pathway genes")

    print("\nBuilding graph...")
    G = build_graph(genes)

    print("\nGraph statistics:")
    stats = analyze_graph(G)
    for key, value in stats.items():
        print(f"  {key}: {value}")

    print("\nExporting graph...")
    export_graph(G, output_dir)

    print("\nCreating sample visualization...")
    visualize_sample(G, output_dir, sample_size=50)

    print("\nDone!")


if __name__ == "__main__":
    main()
