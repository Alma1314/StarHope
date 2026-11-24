"""Embedded semantic index: derived storage, clear fallback, no paid calls."""
import math

import pytest

from starhope.semantic_index import (
    EmbeddingConfig,
    SemanticIndex,
    SemanticIndexer,
    question_embedding_text,
    resolve_embedding_config,
    source_hash,
)


class _Question:
    def __init__(self, content, subject="数学", subject_topics=""):
        self.content = content
        self.subject = subject
        self.subject_topics = subject_topics


def _fake_embed(texts, config):
    # Deterministic vectors: arithmetic -> near x-axis, geometry -> near y-axis.
    vectors = []
    for text in texts:
        lowered = text.lower()
        x = 1.0 if ("等差" in lowered or "数列" in lowered or "arithmetic" in lowered) else 0.2
        y = 1.0 if ("几何" in lowered or "三角形" in lowered or "geometry" in lowered) else 0.2
        norm = math.sqrt(x * x + y * y)
        vectors.append([x / norm, y / norm])
    return vectors


def test_question_text_drops_image_targets_and_keeps_context():
    text = question_embedding_text(_Question("求值 ![图](/static/uploads/x.png)", subject_topics="数列"))
    assert "/static/uploads" not in text
    assert "[图]" in text and "数列" in text


def test_resolve_config_prefers_explicit_and_falls_back(tmp_path):
    explicit = resolve_embedding_config({
        "EMBEDDING_API_BASE": "https://example.test/v1/",
        "EMBEDDING_API_KEY": "k",
        "EMBEDDING_MODEL": "m",
        "SILICONFLOW_API_KEY": "ignored",
    })
    assert explicit == EmbeddingConfig("https://example.test/v1", "k", "m", "custom")
    siliconflow = resolve_embedding_config({"SILICONFLOW_API_KEY": "sk"})
    assert siliconflow.provider == "siliconflow"
    assert siliconflow.model == "BAAI/bge-m3"
    assert resolve_embedding_config({}) is None


def test_index_search_filters_and_replaces_model(tmp_path):
    index = SemanticIndex(tmp_path / "semantic.sqlite")
    index.upsert(1, "m1", source_hash("m1", "数列"), _fake_embed(["数列"], None)[0])
    index.upsert(2, "m1", source_hash("m1", "几何"), _fake_embed(["几何"], None)[0])
    index.upsert(3, "m1", source_hash("m1", "三角形"), _fake_embed(["三角形"], None)[0])

    results = index.search(_fake_embed(["等差数列"], None)[0], 3, model="m1")
    assert results[0][0] == 1
    assert index.search(
        _fake_embed(["等差数列"], None)[0], 3, model="m1", allowed_ids={2, 3}
    )[0][0] == 2

    # A different embedding model invalidates the whole derived index.
    index.upsert(4, "m2", source_hash("m2", "数列"), _fake_embed(["数列"], None)[0])
    assert index.indexed_count("m1") == 0
    assert index.indexed_count("m2") == 1


def test_indexer_rehashes_only_changed_questions(monkeypatch, tmp_path, db_session):
    from starhope import semantic_index
    from starhope.database import Question

    monkeypatch.setattr(
        semantic_index, "resolve_embedding_config",
        lambda environment=None: EmbeddingConfig("https://example.test/v1", "k", "m1", "custom"),
    )
    question = Question(
        content="已知等差数列求和", question_type="detailed_answer",
        difficulty="normal", subject="数学", subject_topics="数列",
    )
    db_session.add(question)
    db_session.commit()

    calls = []

    def embed(texts, config):
        calls.append(list(texts))
        return _fake_embed(texts, config)

    indexer = SemanticIndexer(index=SemanticIndex(tmp_path / "semantic.sqlite"), embed=embed)
    indexer._process(question.id)
    assert len(calls) == 1
    assert indexer.index.indexed_count("m1") == 1

    indexer._process(question.id)
    assert len(calls) == 1  # source hash unchanged

    question.content = "已知等差数列求前n项和"
    db_session.commit()
    indexer._process(question.id)
    assert len(calls) == 2


def test_semantic_router_returns_recall_results(monkeypatch, tmp_path, db_session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starhope import semantic_index
    from starhope.database import Question

    monkeypatch.setattr(
        semantic_index, "resolve_embedding_config",
        lambda environment=None: EmbeddingConfig("https://example.test/v1", "k", "m1", "custom"),
    )
    first = Question(content="已知等差数列求和", question_type="detailed_answer",
                     difficulty="normal", subject="数学", subject_topics="数列")
    second = Question(content="已知等差数列求通项", question_type="detailed_answer",
                      difficulty="normal", subject="数学", subject_topics="数列")
    other = Question(content="几何三角形面积", question_type="detailed_answer",
                     difficulty="normal", subject="数学")
    db_session.add_all([first, second, other])
    db_session.commit()

    index = SemanticIndex(tmp_path / "semantic.sqlite")
    indexer = SemanticIndexer(index=index, embed=_fake_embed)
    for row in (first, second, other):
        text = question_embedding_text(row)
        index.upsert(row.id, "m1", source_hash("m1", text), _fake_embed([text], None)[0])

    def override_db():
        yield db_session

    app = FastAPI()
    app.include_router(semantic_index.create_semantic_router(override_db, indexer=indexer))
    client = TestClient(app)

    status = client.get("/api/semantic/status").json()
    assert status["configured"] is True and status["indexed"] == 3

    similar = client.get(f"/api/semantic/similar/{first.id}?limit=2").json()
    assert [item["id"] for item in similar["results"]][0] == second.id
    assert len(similar["results"]) == 1  # Unrelated geometry must not fill the limit.
    assert "不代表重复判定" in similar["score_note"]

    for subject, stage in (("物理", first.education_stage), ("数学", "小学")):
        distractor = Question(content=first.content, subject=subject, education_stage=stage)
        db_session.add(distractor)
        db_session.commit()
        text = question_embedding_text(distractor)
        index.upsert(distractor.id, "m1", source_hash("m1", text),
                     _fake_embed([question_embedding_text(first)], None)[0])
    # Filter before recall: higher scoring cross-subject/stage rows cannot
    # occupy the candidate limit, even when the wording is identical.
    assert [item["id"] for item in client.get(
        f"/api/semantic/similar/{first.id}?limit=1"
    ).json()["results"]] == [second.id]

    searched = client.post("/api/semantic/search", data={
        "query": "等差数列", "limit": 2, "subject": "数学",
    }).json()
    assert searched["results"][0]["id"] in {first.id, second.id}
    assert all(item["subject"] == "数学" for item in searched["results"])

    vector = _fake_embed([question_embedding_text(first)], None)[0]
    for score, expected_ids in ((0.89, []), (0.91, [second.id])):
        perpendicular = [-vector[1], vector[0]]
        near = [score * a + math.sqrt(1 - score ** 2) * b
                for a, b in zip(vector, perpendicular)]
        index.upsert(second.id, "m1", "boundary-test", near)
        assert [item["id"] for item in client.get(
            f"/api/semantic/similar/{first.id}?limit=2"
        ).json()["results"]] == expected_ids

    monkeypatch.setattr(semantic_index, "resolve_embedding_config", lambda environment=None: None)
    assert client.get(f"/api/semantic/similar/{first.id}").status_code == 503
    assert client.post("/api/semantic/search", data={"query": "等差数列"}).status_code == 503


def test_frontend_panel_and_script_are_wired():
    from pathlib import Path

    html = Path("static/index.html").read_text(encoding="utf-8")
    script = Path("static/js/semantic.js").read_text(encoding="utf-8")
    assert 'id="paperRelationsWrapper"' in html
    assert 'id="findSimilarBtn"' in html
    assert "/static/js/semantic.js" in html
    assert "starhope:question-opened" in script
    assert "variant_of" in script and "same_method" in script


def test_indexer_skips_without_configuration(monkeypatch, tmp_path, db_session):
    from starhope import semantic_index
    from starhope.database import Question

    monkeypatch.setattr(semantic_index, "resolve_embedding_config", lambda environment=None: None)
    question = Question(
        content="无嵌入配置", question_type="detailed_answer",
        difficulty="normal", subject="数学",
    )
    db_session.add(question)
    db_session.commit()
    indexer = SemanticIndexer(index=SemanticIndex(tmp_path / "semantic.sqlite"),
                              embed=lambda texts, config: pytest.fail("must not embed"))
    indexer._process(question.id)
    assert indexer.index.indexed_count() == 0
