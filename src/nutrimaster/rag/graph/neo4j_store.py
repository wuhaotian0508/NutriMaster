from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from nutrimaster.rag.graph.schema import (
    clean_value,
    gene_species,
    node_label,
    normalize_name,
    relation_type,
    relationship_evidence,
    split_items,
    stable_edge_id,
    stable_node_id,
)


@dataclass(frozen=True)
class Neo4jGraphConfig:
    """Neo4j 连接配置。

    Attributes:
        uri: Neo4j Bolt URI，例如 bolt://localhost:7687。
        user: Neo4j 用户名。
        password: Neo4j 密码。
        database: Neo4j database 名；为空时使用服务默认数据库。
    """

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "password"
    database: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Neo4jGraphConfig":
        """从环境变量创建 Neo4j 配置。

        Args:
            env: 环境变量映射；为空时使用 os.environ。

        Returns:
            Neo4jGraphConfig。支持 NEO4J_URI/USER/PASSWORD/DATABASE。
        """
        source = env or os.environ
        return cls(
            uri=source.get("NEO4J_URI", "bolt://localhost:7687"),
            user=source.get("NEO4J_USER", "neo4j"),
            password=source.get("NEO4J_PASSWORD", "password"),
            database=source.get("NEO4J_DATABASE", ""),
        )


class Neo4jGraphStore:
    """Neo4j 图存储后端，负责 schema 初始化和从 corpus 建图。

    这个类只处理“怎么把结构化 JSON 写成图”。自然语言查询、节点消歧和路径排序
    放在 resolver/path_search/source 中，避免建图逻辑和检索逻辑互相缠住。
    """

    def __init__(self, config: Neo4jGraphConfig):
        """创建 Neo4j driver。

        Args:
            config: Neo4j 连接配置。

        Raises:
            RuntimeError: 当前环境没有安装 neo4j Python driver。
        """
        self.config = config
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover - 取决于运行环境是否安装 neo4j
            raise RuntimeError("Neo4j graph backend requires dependency: neo4j>=5.0.0") from exc
        self._driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))

    def close(self) -> None:
        """关闭 Neo4j driver 连接。"""
        self._driver.close()

    def session(self):
        """返回 Neo4j session；database 为空时使用 Neo4j 默认 database。"""
        kwargs = {"database": self.config.database} if self.config.database else {}
        return self._driver.session(**kwargs)

    def is_available(self) -> bool:
        """检查 Neo4j 是否可用。

        Returns:
            True 表示能够执行简单查询；False 表示服务不可达或认证失败。
        """
        try:
            with self.session() as session:
                session.run("RETURN 1 AS ok").single()
            return True
        except MemoryError:
            raise
        except Exception:
            return False

    def initialize_schema(self) -> None:
        """创建 Neo4j 约束和索引。

        GraphNode 是所有业务节点的公共 label，因此唯一约束和全文索引都建在 GraphNode 上。
        具体业务 label，如 Gene/Metabolite/Pathway，只用于可视化和查询过滤。
        """
        statements = [
            "CREATE CONSTRAINT graph_node_id IF NOT EXISTS FOR (n:GraphNode) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX graph_node_norm_name IF NOT EXISTS FOR (n:GraphNode) ON (n.norm_name)",
            "CREATE INDEX graph_node_type IF NOT EXISTS FOR (n:GraphNode) ON (n.type)",
            "CREATE INDEX graph_node_species IF NOT EXISTS FOR (n:GraphNode) ON (n.species)",
            (
                "CREATE FULLTEXT INDEX graph_nodes_text IF NOT EXISTS "
                "FOR (n:GraphNode) ON EACH [n.name, n.norm_name, n.alias_text, n.species]"
            ),
        ]
        with self.session() as session:
            for statement in statements:
                session.run(statement).consume()

    def clear_graph(self) -> None:
        """删除当前数据库中本模块创建的 GraphNode 子图。"""
        with self.session() as session:
            session.run("MATCH (n:GraphNode) DETACH DELETE n").consume()

    def build_from_corpus(self, corpus_dir: Path | str, *, reset: bool = True) -> dict[str, int]:
        """从 data/corpus/*.json 写入 Neo4j。

        Args:
            corpus_dir: verified JSON 语料目录。
            reset: True 时先删除旧 GraphNode 子图，再全量重建。

        Returns:
            写入统计，包含 files/nodes/edges。
        """
        if reset:
            self.clear_graph()
        self.initialize_schema()

        corpus = Path(corpus_dir)
        total_files = 0
        total_nodes = 0
        total_edges = 0
        for path in sorted(corpus.glob("*.json")):
            rows = self._paper_to_rows(path)
            if not rows["nodes"] and not rows["edges"]:
                continue
            with self.session() as session:
                session.execute_write(self._write_rows, rows)
            total_files += 1
            total_nodes += len(rows["nodes"])
            total_edges += len(rows["edges"])
        return {"files": total_files, "nodes": total_nodes, "edges": total_edges}

    @staticmethod
    def _write_rows(tx, rows: dict[str, list[dict[str, Any]]]) -> None:
        for node in rows["nodes"]:
            Neo4jGraphStore._merge_node(tx, node)
        for edge in rows["edges"]:
            Neo4jGraphStore._merge_edge(tx, edge)

    @staticmethod
    def _merge_node(tx, node: dict[str, Any]) -> None:
        label = node_label(node["type"])
        # label 已经过白名单校验，才允许拼进 Cypher；属性全部用参数传入。
        tx.run(
            f"""
            MERGE (n:GraphNode {{id: $id}})
            SET n:{label}
            SET n.type = $type,
                n.name = $name,
                n.norm_name = $norm_name,
                n.species = $species,
                n.alias_text = $alias_text
            """,
            **node,
        ).consume()

    @staticmethod
    def _merge_edge(tx, edge: dict[str, Any]) -> None:
        relation = relation_type(edge["relation"])
        props = {
            key: value
            for key, value in edge.items()
            if key not in {"src", "dst", "relation"} and value is not None
        }
        # relation 已经过白名单校验，才允许拼进 Cypher；这样既能动态关系类型，也避免注入。
        tx.run(
            f"""
            MATCH (src:GraphNode {{id: $src}}), (dst:GraphNode {{id: $dst}})
            MERGE (src)-[r:{relation} {{id: $id}}]->(dst)
            ON CREATE SET r += $props
            ON MATCH SET r.evidence_count = coalesce(r.evidence_count, 1) + $evidence_count
            """,
            src=edge["src"],
            dst=edge["dst"],
            id=edge["id"],
            props=props,
            evidence_count=int(props.get("evidence_count") or 1),
        ).consume()

    def _paper_to_rows(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        try:
            import json

            paper = json.loads(path.read_text(encoding="utf-8"))
        except MemoryError:
            raise
        except Exception:
            return {"nodes": [], "edges": []}

        builder = _GraphRowsBuilder(
            base_evidence={
                "source_file": str(path),
                "title": clean_value(paper.get("Title")),
                "journal": clean_value(paper.get("Journal")),
                "doi": clean_value(paper.get("DOI")),
            }
        )

        for index, gene in enumerate(paper.get("Pathway_Genes") or []):
            builder.add_pathway_gene(gene, record_index=index)
        for index, gene in enumerate(paper.get("Regulation_Genes") or []):
            builder.add_regulation_gene(gene, record_index=index)
        for index, gene in enumerate(paper.get("Common_Genes") or []):
            builder.add_common_gene(gene, record_index=index)
        return builder.rows()


class _GraphRowsBuilder:
    """把一篇论文的 JSON 抽取结果转换成节点/边行。

    这个类不连接 Neo4j，便于单元测试和后续导出到别的图后端。
    """

    def __init__(self, *, base_evidence: dict[str, Any]):
        self.base_evidence = base_evidence
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}

    def rows(self) -> dict[str, list[dict[str, Any]]]:
        """返回 Neo4j 写入所需的节点和边。"""
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}

    def add_pathway_gene(self, gene: dict[str, Any], *, record_index: int) -> None:
        """把 Pathway_Genes 记录转换为底物/反应/产物和通路关系。"""
        gene_node = self._gene_node(gene)
        if not gene_node:
            return
        evidence = self._evidence(gene, section="Pathway_Genes", record_index=record_index, domain="pathway")

        substrates = split_items(gene.get("Primary_Substrate"))
        products = split_items(gene.get("Primary_Product"))
        if substrates or products:
            # 整体图以 Gene 为中心做邻接索引：Pathway 记录里的 a -> b -> c
            # 只落成 a -> b 和 b -> c 两条邻接边，避免再额外索引一条链式关系。
            for index, substrate in enumerate(substrates):
                self._edge(
                    self._node("Metabolite", substrate),
                    gene_node,
                    "INPUT_OF",
                    evidence,
                    field="Primary_Substrate",
                    item=substrate,
                    item_index=index,
                )
            for index, product in enumerate(products):
                self._edge(
                    gene_node,
                    self._node("Metabolite", product),
                    "PRODUCES",
                    evidence,
                    field="Primary_Product",
                    item=product,
                    item_index=index,
                )

        for index, metabolite in enumerate(split_items(gene.get("Terminal_Metabolite"))):
            self._edge(
                gene_node,
                self._node("Metabolite", metabolite),
                "CONTRIBUTES_TO",
                evidence,
                field="Terminal_Metabolite",
                item=metabolite,
                item_index=index,
            )
        pathway_items = (
            ("Biosynthetic_Pathway", split_items(gene.get("Biosynthetic_Pathway"))),
            ("Pathway_Branch_or_Subpathway", split_items(gene.get("Pathway_Branch_or_Subpathway"))),
        )
        for field, pathways in pathway_items:
            for index, pathway in enumerate(pathways):
                self._edge(
                    gene_node,
                    self._node("Pathway", pathway),
                    "PARTICIPATES_IN",
                    evidence,
                    field=field,
                    item=pathway,
                    item_index=index,
                )
        self._add_species_edge(gene_node, gene, evidence)

    def add_regulation_gene(self, gene: dict[str, Any], *, record_index: int) -> None:
        """把 Regulation_Genes 记录转换为调控者、靶基因、上游信号和过程关系。"""
        gene_node = self._gene_node(gene)
        if not gene_node:
            return
        species = gene_species(gene)
        evidence = self._evidence(gene, section="Regulation_Genes", record_index=record_index, domain="regulation")
        evidence["mode"] = clean_value(gene.get("Regulation_Mode"))

        for index, target in enumerate(split_items(gene.get("Primary_Regulatory_Targets"))):
            self._edge(
                gene_node,
                self._node("Gene", target, species=species),
                "REGULATES",
                evidence,
                field="Primary_Regulatory_Targets",
                item=target,
                item_index=index,
            )
        for index, signal in enumerate(split_items(gene.get("Upstream_Signals_or_Inputs"))):
            self._edge(
                self._node("Signal", signal),
                gene_node,
                "UPSTREAM_SIGNAL_OF",
                evidence,
                field="Upstream_Signals_or_Inputs",
                item=signal,
                item_index=index,
            )
        for index, process in enumerate(split_items(gene.get("Metabolic_Process_Controlled"))):
            self._edge(
                gene_node,
                self._node("Process", process),
                "CONTROLS",
                evidence,
                field="Metabolic_Process_Controlled",
                item=process,
                item_index=index,
            )
        for index, metabolite in enumerate(split_items(gene.get("Terminal_Metabolite"))):
            self._edge(
                gene_node,
                self._node("Metabolite", metabolite),
                "AFFECTS",
                evidence,
                field="Terminal_Metabolite",
                item=metabolite,
                item_index=index,
            )
        self._add_species_edge(gene_node, gene, evidence)

    def add_common_gene(self, gene: dict[str, Any], *, record_index: int) -> None:
        """把 Common_Genes 记录转换为更宽泛的影响/表型关系。"""
        gene_node = self._gene_node(gene)
        if not gene_node:
            return
        evidence = self._evidence(gene, section="Common_Genes", record_index=record_index, domain="common")
        for index, metabolite in enumerate(split_items(gene.get("Terminal_Metabolite"))):
            self._edge(
                gene_node,
                self._node("Metabolite", metabolite),
                "AFFECTS",
                evidence,
                field="Terminal_Metabolite",
                item=metabolite,
                item_index=index,
            )
        phenotype = clean_value(gene.get("Core_Phenotypic_Effect"))
        if phenotype:
            self._edge(
                gene_node,
                self._node("Phenotype", phenotype),
                "HAS_EFFECT",
                evidence,
                field="Core_Phenotypic_Effect",
                item=phenotype,
            )
        self._add_species_edge(gene_node, gene, evidence)

    def _gene_node(self, gene: dict[str, Any]) -> str:
        aliases = [clean_value(gene.get("Gene_Accession_Number"))]
        return self._node("Gene", clean_value(gene.get("Gene_Name")), species=gene_species(gene), aliases=aliases)

    def _node(self, node_type: str, name: str, *, species: str = "", aliases: list[str] | None = None) -> str:
        name = clean_value(name)
        if not name:
            return ""
        species = clean_value(species)
        node_id = stable_node_id(node_type, name, species)
        alias_values = [name, species, *(aliases or [])]
        alias_text = " ".join(value for value in alias_values if clean_value(value))
        existing = self.nodes.get(node_id)
        if existing:
            merged_aliases = {*(existing.get("alias_text") or "").split(), *alias_text.split()}
            existing["alias_text"] = " ".join(sorted(merged_aliases))
            return node_id
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "name": name,
            "norm_name": normalize_name(name),
            "species": species,
            "alias_text": alias_text,
        }
        return node_id

    def _edge(
        self,
        src: str,
        dst: str,
        relation: str,
        evidence: dict[str, Any],
        *,
        field: str,
        item: str = "",
        item_index: int = 0,
    ) -> None:
        if not src or not dst:
            return
        rel = relation_type(relation)
        edge_evidence = relationship_evidence(
            evidence,
            relation=rel,
            field=field,
            item=item,
            item_index=item_index,
        )
        edge_id = stable_edge_id(src, dst, rel, edge_evidence)
        existing = self.edges.get(edge_id)
        if existing:
            existing["evidence_count"] = int(existing.get("evidence_count") or 1) + 1
            sections = set(existing.get("source_sections") or [])
            if edge_evidence.get("section"):
                sections.add(edge_evidence["section"])
            existing["source_sections"] = sorted(sections)
            return
        row = {
            "id": edge_id,
            "src": src,
            "dst": dst,
            "relation": rel,
            "evidence_count": 1,
            "source_sections": [edge_evidence["section"]] if edge_evidence.get("section") else [],
            **edge_evidence,
        }
        self.edges[edge_id] = row

    def _add_species_edge(self, gene_node: str, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        species = clean_value(gene.get("Applied_Species_Latin_Name")) or clean_value(gene.get("Applied_Species"))
        if species:
            self._edge(
                gene_node,
                self._node("Species", species),
                "TESTED_IN",
                evidence,
                field="Applied_Species",
                item=species,
            )

    def _evidence(self, gene: dict[str, Any], *, section: str, record_index: int, domain: str) -> dict[str, Any]:
        return {
            **self.base_evidence,
            "section": section,
            "record_index": record_index,
            "domain": domain,
            "species": gene_species(gene),
            "validation": clean_value(gene.get("Core_Validation_Method")),
            "summary": clean_value(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }
