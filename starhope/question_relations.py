"""Typed, directed question relations with bounded graph traversal.

Similarity is not transitive.  This module never merges or reads
``association_group_id``; a similarity chain stays a set of typed edges.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .database import RELATION_TYPES, Question, QuestionRelation
from .question_ordering import sequence_mapping
from .question_graph import get_graph

MAX_RELATION_DEPTH = 3
_SNIPPET_LIMIT = 140


def _snippet(content: str) -> str:
    collapsed = " ".join(str(content or "").split())
    return collapsed[:_SNIPPET_LIMIT] + ("…" if len(collapsed) > _SNIPPET_LIMIT else "")


def _require_question(db, question_id: int):
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "题目不存在")
    return question


def _relation_dict(row: QuestionRelation, seq_nums: dict[int, int] | None = None):
    seq_nums = seq_nums or {}
    return {
        "id": row.id,
        "source_id": row.source_id,
        "target_id": row.target_id,
        "relation_type": row.relation_type,
        "basis": row.basis or "",
        "origin": row.origin,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source_seq_num": seq_nums.get(row.source_id),
        "target_seq_num": seq_nums.get(row.target_id),
    }


def _briefs(db, question_ids):
    if not question_ids:
        return []
    seq_nums = sequence_mapping(db, question_ids)
    rows = db.query(
        Question.id, Question.content, Question.question_type,
        Question.difficulty, Question.subject,
    ).filter(Question.id.in_(question_ids)).all()
    return [{
        "id": row.id,
        "seq_num": seq_nums.get(row.id),
        "content": _snippet(row.content),
        "question_type": row.question_type,
        "difficulty": row.difficulty,
        "subject": row.subject,
    } for row in rows]


_WALK_SQL = text("""
WITH RECURSIVE walk(question_id, depth) AS (
    SELECT :start, 0
    UNION
    SELECT CASE WHEN rel.source_id = walk.question_id THEN rel.target_id ELSE rel.source_id END,
           walk.depth + 1
    FROM walk
    JOIN question_relations AS rel
      ON rel.source_id = walk.question_id OR rel.target_id = walk.question_id
    WHERE walk.depth < :max_depth
)
SELECT question_id, MIN(depth) AS depth
FROM walk
GROUP BY question_id
ORDER BY depth, question_id
""")


def create_question_relations_router(get_db):
    router = APIRouter()

    @router.get("/api/questions/{question_id}/relations")
    def list_relations(
        question_id: int,
        depth: int = Query(1, ge=1, le=MAX_RELATION_DEPTH),
        db=Depends(get_db),
    ):
        _require_question(db, question_id)
        graph = get_graph()
        rows = None
        if graph.available():
            stored = db.query(QuestionRelation.id).limit(1).first()
            if stored is not None and graph.edge_count() == 0:
                graph.rebuild(db)
            walked = graph.neighbors(question_id, depth)
            if walked is not None:
                depths = {question_id: 0, **{node: level for node, level in walked}}
                rows = [(node, level) for node, level in sorted(
                    depths.items(), key=lambda item: (item[1], item[0])
                )]
        if rows is None:
            cte_rows = db.execute(_WALK_SQL, {"start": question_id, "max_depth": depth}).all()
            rows = [(int(row.question_id), int(row.depth)) for row in cte_rows]
            depths = {node: level for node, level in rows}
        else:
            depths = {node: level for node, level in rows}
        ids = [node for node, _level in rows]
        edges = (
            db.query(QuestionRelation)
            .filter(
                QuestionRelation.source_id.in_(ids),
                QuestionRelation.target_id.in_(ids),
            )
            .order_by(QuestionRelation.id)
            .all()
        )
        seq_nums = sequence_mapping(db, ids)
        return {
            "status": "success",
            "question_id": question_id,
            "depth": depth,
            "nodes": [
                {**brief, "depth": depths.get(brief["id"], 0)}
                for brief in _briefs(db, ids)
            ],
            "edges": [_relation_dict(row, seq_nums) for row in edges],
            "relation_types": sorted(RELATION_TYPES),
            "graph_backend": "cozo" if graph.available() else "sqlite-cte",
        }

    @router.post("/api/questions/{question_id}/relations")
    def create_relation(
        question_id: int,
        target_id: int = Form(...),
        relation_type: str = Form(...),
        basis: str = Form(""),
        origin: str = Form("manual"),
        status: str = Form("confirmed"),
        db=Depends(get_db),
    ):
        _require_question(db, question_id)
        _require_question(db, target_id)
        if question_id == target_id:
            raise HTTPException(400, "不能把题目关联到自身")
        if relation_type not in RELATION_TYPES:
            raise HTTPException(400, "未知的题目关系类型")
        if origin not in {"manual", "ai"}:
            raise HTTPException(400, "未知的关系来源")
        if status not in {"suggested", "confirmed"}:
            raise HTTPException(400, "未知的关系状态")
        basis = str(basis or "").strip()
        if len(basis) > 2000 or "\x00" in basis:
            raise HTTPException(400, "关系依据须为2000字以内的文本")
        existing = db.query(QuestionRelation).filter(
            QuestionRelation.source_id == question_id,
            QuestionRelation.target_id == target_id,
            QuestionRelation.relation_type == relation_type,
        ).first()
        if existing is not None:
            raise HTTPException(409, "相同类型的关系已存在")
        row = QuestionRelation(
            source_id=question_id,
            target_id=target_id,
            relation_type=relation_type,
            basis=basis,
            origin=origin,
            status=status,
            created_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "相同类型的关系已存在")
        db.refresh(row)
        get_graph().upsert(row.id, row.source_id, row.target_id, row.relation_type)
        return {"status": "success", "relation": _relation_dict(row)}

    @router.put("/api/question-relations/{relation_id}")
    def update_relation(
        relation_id: int,
        status: str = Form(...),
        basis: str | None = Form(None),
        db=Depends(get_db),
    ):
        row = db.get(QuestionRelation, relation_id)
        if row is None:
            raise HTTPException(404, "题目关系不存在")
        if status not in {"suggested", "confirmed"}:
            raise HTTPException(400, "未知的关系状态")
        row.status = status
        if basis is not None:
            basis = str(basis).strip()
            if len(basis) > 2000 or "\x00" in basis:
                raise HTTPException(400, "关系依据须为2000字以内的文本")
            row.basis = basis
        db.commit()
        return {"status": "success", "relation": _relation_dict(row)}

    @router.delete("/api/question-relations/{relation_id}")
    def delete_relation(relation_id: int, db=Depends(get_db)):
        row = db.get(QuestionRelation, relation_id)
        if row is None:
            raise HTTPException(404, "题目关系不存在")
        db.delete(row)
        db.commit()
        get_graph().remove(relation_id)
        return {"status": "success"}

    return router
