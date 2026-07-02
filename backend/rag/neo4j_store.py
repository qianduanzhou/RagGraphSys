"""Neo4j 图谱库。

知识图谱层：
  * 节点带有 ``:Entity`` 标签和 ``name`` 属性，
  * 关系使用经过清洗、带类型的标签（例如 ``:WORKS_FOR``），否则回退为
    ``:RELATES_TO``，
  * ``search`` 基于给定的实体关键词做模糊一跳遍历，
    与规格中要求的 ``MATCH (a)-[r]->(b) RETURN a, r, b`` 模式对应。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from neo4j import GraphDatabase

from core.config import Settings
from core.logger import get_logger
from core.utils import sanitize_relation_type

logger = get_logger(__name__)


class Neo4jStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def verify(self) -> None:
        """Neo4j 不可达时抛出异常（启动时调用）。"""
        self.driver.verify_connectivity()
        logger.info("Neo4j connectivity verified at %s", self.settings.neo4j_uri)

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def add_knowledge(
        self,
        triples: Sequence[Tuple[str, str, str]],
        source: Optional[str] = None,
    ) -> int:
        """将 (head, rel, tail) 三元组合并写入图。返回合并条数。

        ``source`` 为文档来源（文件名）：写入时给每条关系维护一个 ``sources`` 数组，
        以便后续 :meth:`delete_by_source` 能按文档精确清理。多个文档共享同一关系时，
        数组会累加去重，删除其中一个文档不会影响其他文档。
        """
        triples = [(h, r, t) for h, r, t in triples if h and t]
        if not triples:
            return 0

        with self.driver.session() as session:
            session.execute_write(self._merge_triples, triples, source)
        logger.info("Merged %d triples into Neo4j (source=%s)", len(triples), source)
        return len(triples)

    @staticmethod
    def _merge_triples(
        tx,
        triples: Sequence[Tuple[str, str, str]],
        source: Optional[str] = None,
    ) -> None:
        for head, rel, tail in triples:
            rel_type = sanitize_relation_type(rel)
            # rel_type 经 sanitize_relation_type 限制为 [A-Za-z0-9_]，
            # 因此将其插入 Cypher 模板是安全的。
            if source:
                # 给关系打上来源标记（数组去重），用于按文档删除。
                cypher = (
                    "MERGE (a:Entity {name: $head}) "
                    "MERGE (b:Entity {name: $tail}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    "SET r.sources = CASE "
                    "WHEN r.sources IS NULL THEN [$source] "
                    "WHEN $source IN r.sources THEN r.sources "
                    "ELSE r.sources + $source END"
                )
                tx.run(cypher, head=head, tail=tail, source=source)
            else:
                cypher = (
                    "MERGE (a:Entity {name: $head}) "
                    "MERGE (b:Entity {name: $tail}) "
                    f"MERGE (a)-[:{rel_type}]->(b)"
                )
                tx.run(cypher, head=head, tail=tail)

    # ------------------------------------------------------------------ #
    # 删除（按文档来源）
    # ------------------------------------------------------------------ #
    def delete_by_source(self, source: str) -> int:
        """删除某文档来源在图谱中的关系，并清理由此变为孤立的实体节点。

        关系上的 ``sources`` 数组由 :meth:`add_knowledge` 维护；这里把该 ``source``
        从数组移除，数组为空时删除该关系。**历史数据（无 ``sources`` 属性）不会被
        误删**（``$source IN NULL`` 在 Cypher 中为假）。返回被删除的关系数。
        """
        with self.driver.session() as session:
            deleted = session.execute_write(self._delete_source, source)
        logger.info("Deleted %d relations for source=%s", deleted, source)
        return deleted

    @staticmethod
    def _delete_source(tx, source: str) -> int:
        # 1) 从命中关系的 sources 数组移除该来源；数组为空则删除该关系。
        rec = tx.run(
            "MATCH (a)-[r]->(b) "
            "WHERE $source IN r.sources "
            "WITH r, [s IN r.sources WHERE s <> $source] AS rest "
            "SET r.sources = rest "
            "WITH r WHERE size(r.sources) = 0 "
            "DELETE r "
            "RETURN count(r) AS deleted",
            source=source,
        ).single()
        deleted = rec["deleted"] if rec else 0
        # 2) 清理孤立实体节点（已无任何关系），避免检索时返回悬空实体。
        tx.run("MATCH (n:Entity) WHERE NOT (n)--() DELETE n")
        return deleted

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def search(
        self,
        entities: Sequence[str],
        limit: int = 5,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """返回从给定实体关键词可达的一跳关系。"""
        entities = [e for e in entities if e]
        if not entities:
            return []

        cypher = (
            "MATCH (a:Entity) "
            "WHERE ANY(e IN $entities WHERE "
            "  toLower(a.name) CONTAINS toLower(e) OR toLower(e) CONTAINS toLower(a.name)) "
            "MATCH (a)-[r]-(b) "
            "WITH a, r, b "
            "WHERE a.name <> b.name "
            "  AND ($owner_prefix IS NULL OR ANY(s IN coalesce(r.sources, []) WHERE s STARTS WITH $owner_prefix)) "
            "RETURN a.name AS head, type(r) AS rel, b.name AS tail "
            "LIMIT toInteger($limit)"
        )
        owner_prefix = f"{owner}::" if owner else None
        seen = set()
        results: List[Dict[str, Any]] = []
        with self.driver.session() as session:
            records = session.run(cypher, entities=list(entities), limit=limit, owner_prefix=owner_prefix)
            for record in records:
                key = (record["head"], record["rel"], record["tail"])
                if key in seen:
                    continue
                seen.add(key)
                results.append({"head": record["head"], "rel": record["rel"], "tail": record["tail"]})
        logger.info("Neo4j search returned %d relations for entities=%s", len(results), entities)
        return results

    def count_entities(self, owner: Optional[str] = None) -> int:
        with self.driver.session() as session:
            if owner:
                record = session.run(
                    "MATCH (a)-[r]-(b) "
                    "WHERE ANY(s IN coalesce(r.sources, []) WHERE s STARTS WITH $owner_prefix) "
                    "WITH collect(DISTINCT a) + collect(DISTINCT b) AS nodes "
                    "UNWIND nodes AS n "
                    "RETURN count(DISTINCT n) AS c",
                    owner_prefix=f"{owner}::",
                ).single()
            else:
                record = session.run("MATCH (n:Entity) RETURN count(n) AS c").single()
            return record["c"] if record else 0
