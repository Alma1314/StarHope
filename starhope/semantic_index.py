"""Embedded question-similarity index.

The index is a derived, rebuildable SQLite file under
``.system_generated/semantic_index.sqlite``.  The main database stays the
source of truth; deleting this file only costs a re-index.

Vectors come from an OpenAI-compatible ``/embeddings`` endpoint.  When the
optional ``sqlite-vec`` extension can be loaded it provides KNN search;
otherwise the same file is scanned with cosine similarity.  Similarity is
recall only -- it must never be treated as a duplicate verdict.
"""
from __future__ import annotations

import hashlib
import math
import os
import queue
import re
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from contextlib import closing
from typing import Callable, Iterable, Sequence

import requests
from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import or_

from .ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS
from .database import Question
from .paths import SEMANTIC_INDEX_FILE

EMBEDDING_MAX_CHARS = 12000
EMBEDDING_BATCH_SIZE = 16
_VECTOR_TABLE = "vec_question_embeddings"
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SCORE_NOTE = (
    "相似度分数只用于召回排序，不代表重复判定；"
    "请核对公式、数值条件和实际解法后再建立关系。"
)


@dataclass(frozen=True)
class EmbeddingConfig:
    api_base: str
    api_key: str
    model: str
    provider: str


def resolve_embedding_config(environment=None) -> EmbeddingConfig | None:
    env = os.environ if environment is None else environment

    def value(key: str) -> str:
        raw = env.get(key)
        return str(raw).strip() if raw is not None else ""

    base = value("EMBEDDING_API_BASE").rstrip("/")
    key = value("EMBEDDING_API_KEY")
    model = value("EMBEDDING_MODEL") or "BAAI/bge-m3"
    if base and key:
        return EmbeddingConfig(base, key, model, "custom")
    siliconflow = value("SILICONFLOW_API_KEY")
    if siliconflow:
        return EmbeddingConfig(
            (value("SILICONFLOW_API_BASE") or "https://api.siliconflow.cn/v1").rstrip("/"),
            siliconflow, model, "siliconflow",
        )
    bailian = value("ALI_BAILIAN_API_KEY")
    if bailian:
        return EmbeddingConfig(
            (value("ALI_BAILIAN_API_BASE")
             or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
            bailian, value("EMBEDDING_MODEL") or "text-embedding-v4", "bailian",
        )
    return None


def question_embedding_text(question) -> str:
    """Question text without image targets; includes subject and free topics."""
    body = _IMAGE_REF.sub(" [图] ", str(question.content or ""))
    parts = [str(question.subject or "数学"), str(question.subject_topics or ""), body]
    text = "\n".join(part for part in parts if part.strip())
    return text[:EMBEDDING_MAX_CHARS]


def source_hash(model: str, text: str) -> str:
    return hashlib.sha256(model.encode("utf-8") + b"\x00" + text.encode("utf-8")).hexdigest()


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not norm:
        return [0.0] * len(vector)
    return [value / norm for value in vector]


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return [item[0] for item in struct.iter_unpack("<f", blob)]


def embed_texts(texts: Sequence[str], config: EmbeddingConfig) -> list[list[float]]:
    """One bounded POST; no retries and no response-body logging."""
    cleaned = [str(text or "")[:EMBEDDING_MAX_CHARS] for text in texts]
    response = requests.post(
        f"{config.api_base}/embeddings",
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        json={"model": config.model, "input": cleaned},
        timeout=AI_REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise RuntimeError(f"向量服务返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("向量服务返回的不是有效 JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(cleaned):
        raise RuntimeError("向量服务返回条目不完整")
    vectors: list[list[float]] = []
    dim = None
    for item in data:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("向量服务返回的向量无效")
        try:
            converted = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("向量服务返回的向量包含非数值") from exc
        if not all(math.isfinite(value) for value in converted):
            raise RuntimeError("向量服务返回的向量包含非有限数值")
        if dim is None:
            dim = len(converted)
        elif len(converted) != dim:
            raise RuntimeError("向量服务返回的维度不一致")
        vectors.append(_normalize(converted))
    return vectors


class SemanticIndex:
    """SQLite-backed vector store; safe to delete and rebuild."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or SEMANTIC_INDEX_FILE)

    # -- connections -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS embeddings(
                    question_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_model ON embeddings(model);
                CREATE TABLE IF NOT EXISTS meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _load_vec(connection: sqlite3.Connection) -> bool:
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
            return True
        except Exception:
            return False

    def _meta(self, connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _reset_if_model_changed(self, connection, model: str) -> None:
        if self._meta(connection, "model") in (None, model):
            self._set_meta(connection, "model", model)
            return
        connection.execute("DELETE FROM embeddings")
        try:
            # A virtual table can only be dropped with its module loaded.
            connection.execute(f"DROP TABLE IF EXISTS {_VECTOR_TABLE}")
        except sqlite3.Error:
            pass
        self._set_meta(connection, "model", model)
        self._set_meta(connection, "vec_dim", "")

    def _prepare_vec_table(self, connection, dim: int) -> bool:
        if not self._load_vec(connection):
            return False
        installed = self._meta(connection, "vec_dim")
        if installed == str(dim):
            return True
        try:
            connection.execute(f"DROP TABLE IF EXISTS {_VECTOR_TABLE}")
        except sqlite3.Error:
            return False
        connection.execute(
            f"CREATE VIRTUAL TABLE {_VECTOR_TABLE} USING vec0(question_id INTEGER PRIMARY KEY, embedding float[{dim}])"
        )
        self._set_meta(connection, "vec_dim", str(dim))
        return True

    # -- writes ------------------------------------------------------
    def upsert(self, question_id: int, model: str, text_hash: str, vector: Sequence[float]) -> None:
        vector = _normalize(list(vector))
        with closing(self._connect()) as connection, connection:
            self._reset_if_model_changed(connection, model)
            connection.execute(
                "INSERT INTO embeddings(question_id, model, source_hash, dim, vector, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(question_id) DO UPDATE SET model=excluded.model, "
                "source_hash=excluded.source_hash, dim=excluded.dim, "
                "vector=excluded.vector, updated_at=excluded.updated_at",
                (question_id, model, text_hash, len(vector), _pack(vector),
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            if self._prepare_vec_table(connection, len(vector)):
                connection.execute(f"DELETE FROM {_VECTOR_TABLE} WHERE question_id = ?", (question_id,))
                connection.execute(
                    f"INSERT INTO {_VECTOR_TABLE}(question_id, embedding) VALUES(?, ?)",
                    (question_id, _pack(vector)),
                )

    def delete(self, question_id: int) -> None:
        if not self.path.exists():
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM embeddings WHERE question_id = ?", (question_id,))
            if self._meta(connection, "vec_dim"):
                try:
                    connection.execute(f"DELETE FROM {_VECTOR_TABLE} WHERE question_id = ?", (question_id,))
                except sqlite3.Error:
                    pass

    def hash_for(self, question_id: int) -> str | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT source_hash FROM embeddings WHERE question_id = ?", (question_id,)
            ).fetchone()
        return row[0] if row else None

    def vector_for(self, question_id: int) -> list[float] | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT vector FROM embeddings WHERE question_id = ?", (question_id,)
            ).fetchone()
        return _unpack(row[0]) if row else None

    def indexed_ids(self, model: str) -> set[int]:
        if not self.path.exists():
            return set()
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT question_id FROM embeddings WHERE model = ?", (model,)
            ).fetchall()
        return {int(row[0]) for row in rows}

    def indexed_count(self, model: str | None = None) -> int:
        if not self.path.exists():
            return 0
        with closing(self._connect()) as connection, connection:
            if model:
                row = connection.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return int(row[0])

    def backend(self) -> str:
        if not self.path.exists():
            return "cosine"
        with closing(self._connect()) as connection, connection:
            return "sqlite-vec" if self._load_vec(connection) else "cosine"

    # -- reads -------------------------------------------------------
    def search(
        self,
        query_vector: Sequence[float],
        limit: int,
        *,
        model: str,
        allowed_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        if limit <= 0:
            return []
        query = _normalize(list(query_vector))
        with closing(self._connect()) as connection, connection:
            if self._meta(connection, "model") not in (None, model):
                return []
            if allowed_ids is not None and not allowed_ids:
                return []
            # KNN is only safe once the virtual table mirrors every stored
            # vector; a freshly created vec table is still empty.
            stored = int(connection.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
            ).fetchone()[0])
            knn_complete = False
            if self._prepare_vec_table(connection, len(query)):
                try:
                    knn_count = int(connection.execute(
                        f"SELECT COUNT(*) FROM {_VECTOR_TABLE}"
                    ).fetchone()[0])
                    knn_complete = knn_count == stored
                except sqlite3.Error:
                    knn_complete = False
            if knn_complete and (allowed_ids is None or len(allowed_ids) > 2000):
                try:
                    rows = connection.execute(
                        f"SELECT question_id, distance FROM {_VECTOR_TABLE} "
                        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                        (_pack(query), max(limit * 4, 50)),
                    ).fetchall()
                    results = []
                    for question_id, distance in rows:
                        if allowed_ids is not None and int(question_id) not in allowed_ids:
                            continue
                        score = max(-1.0, min(1.0, 1.0 - (float(distance) ** 2) / 2.0))
                        results.append((int(question_id), score))
                    if len(results) >= limit:
                        return results[:limit]
                except sqlite3.Error:
                    pass
            rows = connection.execute(
                "SELECT question_id, vector FROM embeddings WHERE model = ?", (model,)
            ).fetchall()
        scored = []
        for question_id, blob in rows:
            question_id = int(question_id)
            if allowed_ids is not None and question_id not in allowed_ids:
                continue
            vector = _unpack(blob)
            if len(vector) != len(query):
                continue
            scored.append((question_id, math.fsum(a * b for a, b in zip(query, vector))))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]


class SemanticIndexer:
    """Single background worker for save-triggered embedding refreshes."""

    def __init__(self, index: SemanticIndex | None = None, session_factory=None,
                 embed: Callable[[Sequence[str], EmbeddingConfig], list[list[float]]] = embed_texts):
        self.index = index or SemanticIndex()
        self._session_factory = session_factory
        self._embed = embed
        self._queue: queue.Queue[int] = queue.Queue(maxsize=500)
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def embed(self, texts, config: EmbeddingConfig) -> list[list[float]]:
        return self._embed(texts, config)

    def _sessions(self):
        if self._session_factory is None:
            from .database import SessionLocal
            self._session_factory = SessionLocal
        return self._session_factory()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopped.clear()
            self._thread = threading.Thread(
                target=self._run, name="starhope-semantic-index", daemon=True
            )
            self._thread.start()

    def schedule(self, question_ids: Iterable[int]) -> int:
        scheduled = 0
        self.start()
        with self._lock:
            for question_id in question_ids:
                question_id = int(question_id)
                if question_id in self._pending:
                    continue
                try:
                    self._queue.put_nowait(question_id)
                except queue.Full:
                    break
                self._pending.add(question_id)
                scheduled += 1
        return scheduled

    def remove(self, question_id: int) -> None:
        try:
            self.index.delete(int(question_id))
        except Exception as exc:
            print(f"[Semantic Index] delete failed (type={type(exc).__name__})")

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                question_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(question_id)
            except Exception as exc:
                print(f"[Semantic Index] refresh failed (type={type(exc).__name__})")
            finally:
                with self._lock:
                    self._pending.discard(question_id)
                self._queue.task_done()

    def _process(self, question_id: int) -> None:
        config = resolve_embedding_config()
        if config is None:
            return
        session = self._sessions()
        try:
            question = session.get(Question, question_id)
            if question is None:
                self.index.delete(question_id)
                return
            text = question_embedding_text(question)
        finally:
            session.close()
        text_hash = source_hash(config.model, text)
        if self.index.hash_for(question_id) == text_hash:
            return
        vector = self._embed([text], config)[0]
        self.index.upsert(question_id, config.model, text_hash, vector)

    def close(self) -> None:
        self._stopped.set()


_INDEXER = SemanticIndexer()


def get_indexer() -> SemanticIndexer:
    return _INDEXER


def _question_vector(db, indexer: SemanticIndexer, question_id: int,
                     config: EmbeddingConfig) -> list[float]:
    """Return the stored vector, refreshing this one question when stale."""
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "题目不存在")
    text = question_embedding_text(question)
    text_hash = source_hash(config.model, text)
    if indexer.index.hash_for(question_id) == text_hash:
        stored = indexer.index.vector_for(question_id)
        if stored is not None:
            return stored
    vector = indexer.embed([text], config)[0]
    indexer.index.upsert(question_id, config.model, text_hash, vector)
    return vector


def _result_rows(db, scored):
    if not scored:
        return []
    ids = [question_id for question_id, _score in scored]
    from .question_ordering import sequence_mapping

    seq_nums = sequence_mapping(db, ids)
    rows = db.query(
        Question.id, Question.content, Question.question_type,
        Question.difficulty, Question.subject,
    ).filter(Question.id.in_(ids)).all()
    by_id = {row.id: row for row in rows}
    results = []
    for question_id, score in scored:
        row = by_id.get(question_id)
        if row is None:
            continue
        collapsed = " ".join(str(row.content or "").split())
        results.append({
            "id": row.id,
            "seq_num": seq_nums.get(row.id),
            "content": collapsed[:140] + ("…" if len(collapsed) > 140 else ""),
            "question_type": row.question_type,
            "difficulty": row.difficulty,
            "subject": row.subject,
            "score": round(float(score), 6),
        })
    return results


def _filtered_ids(db, subject: str, question_type: str, difficulty: str, keyword: str):
    if not any((subject, question_type, difficulty, keyword)):
        return None
    query = db.query(Question.id)
    if subject:
        query = query.filter(Question.subject == subject)
    if question_type:
        query = query.filter(Question.question_type == question_type)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(or_(
            Question.content.like(pattern, escape="\\"),
            Question.source.like(pattern, escape="\\"),
            Question.tags.like(pattern, escape="\\"),
        ))
    return {int(row[0]) for row in query.all()}


def create_semantic_router(get_db, indexer: SemanticIndexer | None = None):
    router = APIRouter()
    indexer = indexer or get_indexer()

    @router.get("/api/semantic/status")
    def status(db=Depends(get_db)):
        config = resolve_embedding_config()
        total = int(db.query(Question.id).count())
        indexed = indexer.index.indexed_count(config.model if config else None)
        return {
            "status": "success",
            "configured": config is not None,
            "provider": config.provider if config else None,
            "model": config.model if config else None,
            "indexed": indexed,
            "total": total,
            "unindexed": max(0, total - indexed),
            "backend": indexer.index.backend(),
        }

    @router.post("/api/semantic/reindex")
    def reindex(
        limit: int = Query(50, ge=1, le=200),
        force: bool = Query(False),
        db=Depends(get_db),
    ):
        config = resolve_embedding_config()
        if config is None:
            raise HTTPException(503, "未配置向量服务；请设置 EMBEDDING_API_KEY 与 EMBEDDING_API_BASE")
        if force:
            ids = [int(row[0]) for row in db.query(Question.id).order_by(Question.id).limit(limit).all()]
        else:
            indexed = indexer.index.indexed_ids(config.model)
            ids = []
            for (question_id,) in db.query(Question.id).order_by(Question.id).yield_per(500):
                if int(question_id) not in indexed:
                    ids.append(int(question_id))
                    if len(ids) >= limit:
                        break
        return {"status": "success", "scheduled": indexer.schedule(ids), "requested": len(ids)}

    @router.post("/api/semantic/search")
    def semantic_search(
        query: str = Form(...),
        limit: int = Form(10),
        subject: str = Form(""),
        question_type: str = Form(""),
        difficulty: str = Form(""),
        keyword: str = Form(""),
        db=Depends(get_db),
    ):
        query = str(query or "").strip()
        if not query or len(query) > 2000:
            raise HTTPException(400, "检索内容须为1至2000字")
        limit = max(1, min(int(limit), 30))
        config = resolve_embedding_config()
        if config is None:
            raise HTTPException(503, "未配置向量服务；请设置 EMBEDDING_API_KEY 与 EMBEDDING_API_BASE")
        allowed = _filtered_ids(db, subject, question_type, difficulty, keyword)
        if allowed is not None and not allowed:
            return {"status": "success", "results": [], "score_note": _SCORE_NOTE}
        vector = indexer.embed([query], config)[0]
        scored = indexer.index.search(vector, limit, model=config.model, allowed_ids=allowed)
        return {
            "status": "success",
            "results": _result_rows(db, scored),
            "score_note": _SCORE_NOTE,
        }

    @router.get("/api/semantic/similar/{question_id}")
    def similar(
        question_id: int,
        limit: int = Query(10, ge=1, le=30),
        db=Depends(get_db),
    ):
        config = resolve_embedding_config()
        if config is None:
            raise HTTPException(503, "未配置向量服务；请设置 EMBEDDING_API_KEY 与 EMBEDDING_API_BASE")
        vector = _question_vector(db, indexer, question_id, config)
        scored = indexer.index.search(vector, limit + 1, model=config.model)
        scored = [item for item in scored if item[0] != question_id][:limit]
        return {
            "status": "success",
            "question_id": question_id,
            "results": _result_rows(db, scored),
            "indexed": indexer.index.indexed_count(config.model),
            "score_note": _SCORE_NOTE,
        }

    return router
