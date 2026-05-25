from __future__ import annotations

import argparse

from nutrimaster.rag.graph.index import LocalGraphIndex
from nutrimaster.rag.graph.neo4j_store import Neo4jGraphConfig, Neo4jGraphStore


def main() -> None:
    """构建图 RAG 索引的命令行入口。

    默认构建 SQLite fallback；传入 --backend neo4j 时写入 Neo4j，方便在 Neo4j Browser
    里可视化图，也方便运行时执行 Cypher 路径检索。
    """
    parser = argparse.ArgumentParser(description="Build graph index for NutriMaster RAG.")
    parser.add_argument("--backend", choices=["sqlite", "neo4j"], default="sqlite")
    parser.add_argument("--corpus", default="data/corpus", help="Directory containing verified corpus JSON files.")
    parser.add_argument("--out", default="data/index/graph_index.sqlite", help="SQLite output path.")
    parser.add_argument("--uri", default=None, help="Neo4j URI, e.g. bolt://localhost:7687.")
    parser.add_argument("--user", default=None, help="Neo4j user.")
    parser.add_argument("--password", default=None, help="Neo4j password.")
    parser.add_argument("--database", default=None, help="Neo4j database name.")
    parser.add_argument("--no-reset", action="store_true", help="Do not delete existing GraphNode subgraph first.")
    args = parser.parse_args()

    if args.backend == "sqlite":
        LocalGraphIndex(args.out).build_from_corpus(args.corpus)
        print(f"SQLite graph index built: {args.out}")
        return

    env_config = Neo4jGraphConfig.from_env()
    config = Neo4jGraphConfig(
        uri=args.uri or env_config.uri,
        user=args.user or env_config.user,
        password=args.password or env_config.password,
        database=args.database if args.database is not None else env_config.database,
    )
    store = Neo4jGraphStore(config)
    try:
        stats = store.build_from_corpus(args.corpus, reset=not args.no_reset)
    finally:
        store.close()
    print(
        "Neo4j graph built: "
        f"files={stats['files']} nodes={stats['nodes']} edges={stats['edges']} uri={config.uri}"
    )


if __name__ == "__main__":
    main()
