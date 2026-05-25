from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INVALID = {"", "-", "na", "n/a", "none", "null", "nan", "unknown", "not available"}


def clean(value: Any) -> str:
    """清洗抽取字段；NA/unknown 等占位值当作空值。"""
    text = str(value or "").strip()
    return "" if text.lower() in INVALID else text


def norm(value: Any) -> str:
    """标准化名称，用于搜索和稳定 ID。"""
    return re.sub(r"\s+", " ", clean(value)).lower()


def split_items(value: Any) -> list[str]:
    """把 'CHS; DFR; ANS' 这类字段拆成多个实体。"""
    if not clean(value):
        return []
    if isinstance(value, list):
        return [clean(x) for x in value if clean(x)]
    return [clean(x) for x in re.split(r"[;,\n]", str(value)) if clean(x)]


def species_of(gene: dict[str, Any]) -> str:
    """Gene 节点必须尽量带 species，避免 PAL/CHS/DFR 跨物种误合并。"""
    return (
        clean(gene.get("Species_Latin_Name"))
        or clean(gene.get("Source_Species_Latin_Name"))
        or clean(gene.get("Applied_Species"))
        or clean(gene.get("Species"))
    )


def node_id(node_type: str, name: str, species: str = "") -> str:
    """节点去重 ID：Gene 使用 type+name+species；非 Gene 用 type+name。"""
    species_key = norm(species) if node_type == "Gene" else ""
    raw = f"{node_type}|{norm(name)}|{species_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def edge_id(src: str, dst: str, relation: str, evidence: dict[str, Any]) -> str:
    """边去重 ID：同一篇论文同一条记录不会重复插入。"""
    raw = {
        "src": src,
        "dst": dst,
        "relation": relation,
        "paper": evidence.get("doi") or evidence.get("source_file") or "",
        "section": evidence.get("section") or "",
        "record_index": evidence.get("record_index"),
    }
    return hashlib.sha1(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GraphSearchResult:
    seeds: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class LocalGraphIndex:
    """本地 SQLite 图索引：负责建图、去重、解析主体、取局部邻域。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self) -> None:
        """重建图索引；图可以从 corpus 再生成，所以直接清空更简单。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            DROP TABLE IF EXISTS edges;
            DROP TABLE IF EXISTS nodes;

            CREATE TABLE nodes(
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              name TEXT NOT NULL,
              norm_name TEXT NOT NULL,
              species TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE edges(
              id TEXT PRIMARY KEY,
              src TEXT NOT NULL,
              dst TEXT NOT NULL,
              relation TEXT NOT NULL,
              evidence_json TEXT NOT NULL
            );

            CREATE INDEX idx_graph_nodes_norm ON nodes(norm_name);
            CREATE INDEX idx_graph_nodes_type_norm ON nodes(type, norm_name);
            CREATE INDEX idx_graph_edges_src ON edges(src);
            CREATE INDEX idx_graph_edges_dst ON edges(dst);
            """)

    def build_from_corpus(self, corpus_dir: Path | str) -> None:
        """从 data/corpus/*.json 建图。"""
        self.initialize()
        with self.connect() as db:
            for path in sorted(Path(corpus_dir).glob("*.json")):
                try:
                    paper = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue

                base = {
                    "source_file": str(path),
                    "title": clean(paper.get("Title")),
                    "journal": clean(paper.get("Journal")),
                    "doi": clean(paper.get("DOI")),
                }

                for i, gene in enumerate(paper.get("Pathway_Genes") or []):
                    self._add_pathway_gene(db, gene, base | {"section": "Pathway_Genes", "record_index": i})
                for i, gene in enumerate(paper.get("Regulation_Genes") or []):
                    self._add_regulation_gene(db, gene, base | {"section": "Regulation_Genes", "record_index": i})
                for i, gene in enumerate(paper.get("Common_Genes") or []):
                    self._add_common_gene(db, gene, base | {"section": "Common_Genes", "record_index": i})

    def _add_node(self, db: sqlite3.Connection, node_type: str, name: str, species: str = "") -> str:
        """插入节点；INSERT OR IGNORE 实现节点去重。"""
        name = clean(name)
        if not name:
            return ""
        nid = node_id(node_type, name, species)
        db.execute(
            "INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?, ?)",
            (nid, node_type, name, norm(name), clean(species)),
        )
        return nid

    def _add_edge(self, db: sqlite3.Connection, src: str, dst: str, relation: str, evidence: dict[str, Any]) -> None:
        """插入边；每条边都带 evidence_json，方便回答时引用来源。"""
        if not src or not dst:
            return
        db.execute(
            "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
            (edge_id(src, dst, relation, evidence), src, dst, relation, json.dumps(evidence, ensure_ascii=False)),
        )

    def _gene_node(self, db: sqlite3.Connection, gene: dict[str, Any]) -> str:
        return self._add_node(db, "Gene", clean(gene.get("Gene_Name")), species_of(gene))

    def _add_pathway_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Pathway_Genes: 底物 -> Reaction <- Gene -> Reaction -> 产物。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        evidence = evidence | {
            "species": species_of(gene),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        substrate = clean(gene.get("Primary_Substrate"))
        product = clean(gene.get("Primary_Product"))
        if substrate and product:
            reaction = self._add_node(db, "Reaction", f"{clean(gene.get('Gene_Name'))}: {substrate} -> {product}")
            self._add_edge(db, self._add_node(db, "Metabolite", substrate), reaction, "input_of", evidence)
            self._add_edge(db, g, reaction, "catalyzes", evidence)
            self._add_edge(db, reaction, self._add_node(db, "Metabolite", product), "produces", evidence)

        for field, relation, node_type in (
            ("Terminal_Metabolite", "contributes_to", "Metabolite"),
            ("Biosynthetic_Pathway", "participates_in", "Pathway"),
            ("Applied_Species", "tested_in", "Species"),
        ):
            value = clean(gene.get(field))
            if value:
                self._add_edge(db, g, self._add_node(db, node_type, value), relation, evidence)

    def _add_regulation_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Regulation_Genes: regulator -> target / signal -> regulator。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        species = species_of(gene)
        evidence = evidence | {
            "species": species,
            "mode": clean(gene.get("Regulation_Mode")),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        for target in split_items(gene.get("Primary_Regulatory_Targets")):
            self._add_edge(db, g, self._add_node(db, "Gene", target, species), "regulates", evidence)
        for signal in split_items(gene.get("Upstream_Signals_or_Inputs")):
            self._add_edge(db, self._add_node(db, "Signal", signal), g, "upstream_signal_of", evidence)

        process = clean(gene.get("Metabolic_Process_Controlled"))
        if process:
            self._add_edge(db, g, self._add_node(db, "Process", process), "controls", evidence)
        for terminal in split_items(gene.get("Terminal_Metabolite")):
            self._add_edge(db, g, self._add_node(db, "Metabolite", terminal), "affects", evidence)

    def _add_common_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Common_Genes: 更宽泛的 gene-metabolite/phenotype/species 关系。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        evidence = evidence | {
            "species": species_of(gene),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        for field, relation, node_type in (
            ("Terminal_Metabolite", "associated_with", "Metabolite"),
            ("Core_Phenotypic_Effect", "has_effect", "Phenotype"),
            ("Applied_Species", "tested_in", "Species"),
        ):
            value = clean(gene.get(field))
            if value:
                self._add_edge(db, g, self._add_node(db, node_type, value), relation, evidence)

    def resolve_seeds(self, query: str, *, species: str = "", limit: int = 12) -> list[dict[str, Any]]:
        """从用户问题里确定图搜索主体；先 exact match，再模糊 match。"""
        tokens = [query, *re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,32}", query)]
        values = []
        for token in tokens:
            value = norm(token)
            if value and value not in values:
                values.append(value)

        if not values or not self.db_path.exists():
            return []

        with self.connect() as db:
            marks = ",".join("?" for _ in values)
            rows = db.execute(
                f"SELECT * FROM nodes WHERE norm_name IN ({marks}) ORDER BY type='Gene' DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
            if not rows:
                rows = db.execute(
                    "SELECT * FROM nodes WHERE norm_name LIKE ? ORDER BY type='Gene' DESC LIMIT ?",
                    (f"%{values[0]}%", limit),
                ).fetchall()

        result = [dict(row) for row in rows]
        if species:
            s = norm(species)
            result = [row for row in result if not row.get("species") or s in norm(row.get("species"))]
        return result

    def neighborhood(
        self,
        query: str,
        *,
        species: str = "",
        hops: int = 1,
        direction: str = "both",
        limit: int = 50,
    ) -> GraphSearchResult:
        """只取主体附近 1-2 跳，不把整张图塞给 LLM。"""
        seeds = self.resolve_seeds(query, species=species)
        if not seeds:
            return GraphSearchResult([], [], [])

        seen = {seed["id"] for seed in seeds}
        frontier = set(seen)
        edges: list[dict[str, Any]] = []

        with self.connect() as db:
            for _ in range(max(1, hops)):
                if not frontier or len(edges) >= limit:
                    break

                clauses, params = [], []
                marks = ",".join("?" for _ in frontier)
                if direction in {"both", "downstream"}:
                    clauses.append(f"src IN ({marks})")
                    params.extend(frontier)
                if direction in {"both", "upstream"}:
                    clauses.append(f"dst IN ({marks})")
                    params.extend(frontier)

                rows = db.execute(
                    f"SELECT * FROM edges WHERE {' OR '.join(clauses)} LIMIT ?",
                    (*params, limit - len(edges)),
                ).fetchall()

                nxt = set()
                for row in rows:
                    edge = dict(row)
                    edge["evidence"] = json.loads(edge.pop("evidence_json"))
                    edges.append(edge)
                    for nid in (edge["src"], edge["dst"]):
                        if nid not in seen:
                            seen.add(nid)
                            nxt.add(nid)
                frontier = nxt

            marks = ",".join("?" for _ in seen)
            nodes = [dict(row) for row in db.execute(f"SELECT * FROM nodes WHERE id IN ({marks})", tuple(seen))]

        return GraphSearchResult(seeds, nodes, edges)
