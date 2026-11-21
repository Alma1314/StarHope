"""Embedded Cozo graph index for typed question relations.

``question_relations`` in the main SQLite database stays the source of truth.
This file is a derived, rebuildable graph index under
``.system_generated/question_graph.db``.  When the native Cozo wheel cannot
load, callers fall back to the SQLite recursive CTE in
``starhope.question_relations``.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .paths import QUESTION_GRAPH_FILE

_LINK_DDL = (
    ":create link {src: Int, dst: Int, relation_id: Int => relation_type: String}"
)
_WALK_SQL = """
path[src, dst, rel, depth] := *link[src, dst, rel, rt], depth = 1
path[src, dst, rel, depth] := path[src, mid, r0, d0], *link[mid, dst, rel, rt],
    depth = d0 + 1, depth <= $max
?[dst, rel, min(depth)] := path[$start, dst, rel, depth], dst != $start
"""


class QuestionGraph:
    """Thread-safe handle for one embedded Cozo database file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or QUESTION_GRAPH_FILE)
        self._lock = threading.RLock()
        self._client = None
        self._failed = False
        self._ready = False

    # -- lifecycle ---------------------------------------------------
    def _connection(self):
        if self._failed:
            return None
        if self._client is not None:
            return self._client
        try:
            from pycozo import Client

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._client = Client("sqlite", str(self.path), {}, dataframe=False)
        except Exception as exc:
            self._failed = True
            print(f"[Question Graph] unavailable (type={type(exc).__name__})")
            return None
        return self._client

    def _ensure_schema(self, client) -> bool:
        if self._ready:
            return True
        try:
            client.run(_LINK_DDL)
        except Exception:
            # A persistent file already contains the relation.
            pass
        self._ready = True
        return True

    def _execute(self, script: str, params: dict | None = None):
        """Run one statement; return None when the graph backend is unavailable."""
        with self._lock:
            client = self._connection()
            if client is None or not self._ensure_schema(client):
                return None
            try:
                return client.run(script, params or {})
            except Exception as exc:
                print(f"[Question Graph] query failed (type={type(exc).__name__})")
                return None

    def available(self) -> bool:
        with self._lock:
            return self._connection() is not None

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self._ready = False
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # -- writes ------------------------------------------------------
    def upsert(self, relation_id: int, source_id: int, target_id: int,
               relation_type: str) -> bool:
        result = self._execute(
            "?[src, dst, relation_id, relation_type] <- "
            "[[$src, $dst, $rid, $type], [$dst, $src, $rid, $type]]\n"
            ":put link {src, dst, relation_id => relation_type}",
            {"src": int(source_id), "dst": int(target_id),
             "rid": int(relation_id), "type": str(relation_type)},
        )
        return result is not None

    def remove(self, relation_id: int) -> bool:
        result = self._execute(
            "?[src, dst, relation_id] := *link[src, dst, relation_id, rt], "
            "relation_id == $rid\n:rm link {src, dst, relation_id}",
            {"rid": int(relation_id)},
        )
        return result is not None

    def remove_question(self, question_id: int) -> bool:
        result = self._execute(
            "?[src, dst, relation_id] := *link[src, dst, relation_id, rt], "
            "(src == $q or dst == $q)\n:rm link {src, dst, relation_id}",
            {"q": int(question_id)},
        )
        return result is not None

    def rebuild(self, db) -> int:
        """Replace the derived graph with rows from the main database."""
        from .database import QuestionRelation

        with self._lock:
            client = self._connection()
            if client is None:
                return 0
            try:
                client.run("::remove link")
            except Exception:
                pass
            try:
                client.run(_LINK_DDL)
            except Exception as exc:
                print(f"[Question Graph] rebuild failed (type={type(exc).__name__})")
                return 0
            self._ready = True
            rows = db.query(
                QuestionRelation.id, QuestionRelation.source_id,
                QuestionRelation.target_id, QuestionRelation.relation_type,
            ).all()
            payload = []
            for relation_id, source_id, target_id, relation_type in rows:
                payload.append([int(source_id), int(target_id), int(relation_id), relation_type])
                payload.append([int(target_id), int(source_id), int(relation_id), relation_type])
            for start in range(0, len(payload), 500):
                batch = payload[start:start + 500]
                literals = ",".join(
                    f"[{src},{dst},{rid},'{str(rtype).replace(chr(39), chr(39) * 2)}']"
                    for src, dst, rid, rtype in batch
                )
                try:
                    client.run(
                        "?[src, dst, relation_id, relation_type] <- [" + literals + "]\n"
                        ":put link {src, dst, relation_id => relation_type}"
                    )
                except Exception as exc:
                    print(f"[Question Graph] rebuild batch failed (type={type(exc).__name__})")
                    return 0
            return len(rows)

    def edge_count(self) -> int | None:
        result = self._execute(
            "?[count(src)] := *link[src, dst, relation_id, rt]"
        )
        if result is None:
            return None
        try:
            return int(result["rows"][0][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def neighbors(self, question_id: int, max_depth: int) -> list[tuple[int, int]] | None:
        result = self._execute(_WALK_SQL, {"start": int(question_id), "max": int(max_depth)})
        if result is None:
            return None
        try:
            return [(int(row[0]), int(row[2])) for row in result["rows"]]
        except (KeyError, IndexError, TypeError, ValueError):
            return None


_GRAPH = QuestionGraph()


def get_graph() -> QuestionGraph:
    return _GRAPH
