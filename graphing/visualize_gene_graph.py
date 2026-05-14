import csv
from pathlib import Path

import networkx as nx
from pyvis.network import Network


GRAPHING_DIR = Path(__file__).resolve().parent
GRAPH_EXPORT_DIR = GRAPHING_DIR / "graph_export"

NODES_CSV = GRAPH_EXPORT_DIR / "nodes.csv"
EDGES_CSV = GRAPH_EXPORT_DIR / "edges.csv"
OUT_HTML = GRAPH_EXPORT_DIR / "gene_graph.html"

MAX_NODES = 800

RELATION_FILTER = None
# RELATION_FILTER = {"AFFECTS"}
# RELATION_FILTER = {"AFFECTS", "APPLIED_IN", "BELONGS_TO_TYPE"}

GROUP_COLORS = {
    "Gene": "#2F80ED",
    "GeneType": "#9B51E0",
    "Paper": "#828282",
    "Species": "#27AE60",
    "Metabolite": "#F2994A",
    "MetaboliteClass": "#EB5757",
    "Process": "#56CCF2",
}


def make_node_key(label: str, node_id: str) -> str:
    return f"{label}::{node_id}"


def load_nodes(path: Path):
    nodes = {}

    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            label = row.get("label", "").strip()
            node_id = row.get("id", "").strip()

            if not label or not node_id:
                continue

            key = make_node_key(label, node_id)

            title_lines = []
            for k, v in row.items():
                if v and v.strip():
                    title_lines.append(f"{k}: {v}")

            nodes[key] = {
                "id": node_id,
                "label": label,
                "title": "<br>".join(title_lines),
                "color": GROUP_COLORS.get(label, "#BDBDBD"),
            }

    return nodes


def load_edges(path: Path):
    edges = []

    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            relation = row.get("relation", "").strip()

            if RELATION_FILTER is not None and relation not in RELATION_FILTER:
                continue

            src_label = row.get("src_label", "").strip()
            src_id = row.get("src_id", "").strip()
            dst_label = row.get("dst_label", "").strip()
            dst_id = row.get("dst_id", "").strip()

            if not src_label or not src_id or not dst_label or not dst_id:
                continue

            src = make_node_key(src_label, src_id)
            dst = make_node_key(dst_label, dst_id)

            edges.append(
                {
                    "src": src,
                    "dst": dst,
                    "relation": relation,
                }
            )

    return edges


def build_graph(nodes, edges):
    graph = nx.MultiDiGraph()

    used_nodes = set()

    for edge in edges:
        used_nodes.add(edge["src"])
        used_nodes.add(edge["dst"])

    if MAX_NODES is not None:
        used_nodes = set(list(used_nodes)[:MAX_NODES])

    for key in used_nodes:
        node = nodes.get(key)

        if node is None:
            continue

        graph.add_node(
            key,
            label=node["id"],
            title=node["title"],
            group=node["label"],
            color=node["color"],
            size=18 if node["label"] == "Gene" else 12,
        )

    for edge in edges:
        if edge["src"] not in graph or edge["dst"] not in graph:
            continue

        graph.add_edge(
            edge["src"],
            edge["dst"],
            label=edge["relation"],
            title=edge["relation"],
            arrows="to",
        )

    return graph


def render_graph(graph, out_html: Path):
    out_html.parent.mkdir(parents=True, exist_ok=True)

    net = Network(
        height="900px",
        width="100%",
        directed=True,
        notebook=False,
        cdn_resources="remote",
        bgcolor="#ffffff",
        font_color="#222222",
    )

    net.from_nx(graph)

    net.set_options(
        """
        {
          "nodes": {
            "shape": "dot",
            "font": {
              "size": 18,
              "face": "Arial"
            }
          },
          "edges": {
            "font": {
              "size": 10,
              "align": "middle"
            },
            "color": {
              "color": "#9E9E9E",
              "highlight": "#2F80ED"
            },
            "smooth": {
              "type": "dynamic"
            }
          },
          "physics": {
            "enabled": true,
            "barnesHut": {
              "gravitationalConstant": -30000,
              "centralGravity": 0.2,
              "springLength": 160,
              "springConstant": 0.04,
              "damping": 0.09,
              "avoidOverlap": 0.4
            },
            "minVelocity": 0.75
          },
          "interaction": {
            "hover": true,
            "tooltipDelay": 150,
            "navigationButtons": true,
            "keyboard": true
          }
        }
        """
    )

    net.write_html(str(out_html))


def main():
    if not NODES_CSV.exists():
        raise FileNotFoundError(f"missing nodes file: {NODES_CSV}")

    if not EDGES_CSV.exists():
        raise FileNotFoundError(f"missing edges file: {EDGES_CSV}")

    nodes = load_nodes(NODES_CSV)
    edges = load_edges(EDGES_CSV)
    graph = build_graph(nodes, edges)

    render_graph(graph, OUT_HTML)

    print(f"nodes loaded: {len(nodes)}")
    print(f"edges loaded: {len(edges)}")
    print(f"nodes rendered: {graph.number_of_nodes()}")
    print(f"edges rendered: {graph.number_of_edges()}")
    print(f"output: {OUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
