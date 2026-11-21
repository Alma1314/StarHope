"""Semantic recall is an advisory hint, never a duplicate verdict."""

from types import SimpleNamespace

import pytest

from starhope.database import Question
from starhope.question_duplicates import build_question_fingerprint
from starhope.semantic_index import EmbeddingConfig, SemanticIndex

MODEL = "BAAI/bge-m3"
QUERY = "某班 $40$ 名学生参加志愿活动，求最多可分成几组。"


@pytest.fixture(autouse=True)
def _semantic_enabled(monkeypatch):
    """本机 .env 可能关闭提示；默认启用用例须显式隔离该环境值。"""
    monkeypatch.setenv("SEMANTIC_DUPLICATE_ADVISORY", "true")


def _config():
    return EmbeddingConfig("http://embeddings.invalid/v1", "test", MODEL, "custom")


def _headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _fake_index(tmp_path, question_id, vector):
    index = SemanticIndex(tmp_path / "semantic.sqlite")
    index.upsert(int(question_id), MODEL, "stored-hash", vector)
    return SimpleNamespace(index=index)


def test_semantic_hint_is_returned_without_changing_classification(
    db_session, tmp_path, monkeypatch
):
    import starhope.question_duplicate_service as service

    question = Question(content="完全不同情境下的等差数列题。", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    monkeypatch.setattr(service, "get_indexer", lambda: _fake_index(tmp_path, question.id, [1.0, 0.0]))
    monkeypatch.setattr(service, "resolve_embedding_config", _config)
    monkeypatch.setattr(
        service,
        "embed_texts",
        lambda texts, config, timeout=None: [[1.0, 0.0]],
    )
    query = build_question_fingerprint(
        {"content": QUERY, "question_type": "detailed_answer"}
    )

    candidates, advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )

    assert candidates == []
    assert [item["question"].id for item in advisories] == [question.id]
    assert advisories[0]["reason"] == "SEMANTIC_NEAR"
    assert advisories[0]["score"] == 1.0
    assert status["used"] is True


def test_below_threshold_and_unconfigured_never_hint(db_session, tmp_path, monkeypatch):
    import starhope.question_duplicate_service as service

    question = Question(content="与查询无关的另一道题。", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    monkeypatch.setattr(service, "get_indexer", lambda: _fake_index(tmp_path, question.id, [0.0, 1.0]))
    monkeypatch.setattr(service, "resolve_embedding_config", _config)
    monkeypatch.setattr(
        service,
        "embed_texts",
        lambda texts, config, timeout=None: [[1.0, 0.0]],
    )
    query = build_question_fingerprint(
        {"content": QUERY, "question_type": "detailed_answer"}
    )
    candidates, advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )
    assert candidates == [] and advisories == []
    assert status["used"] is True

    monkeypatch.setattr(service, "resolve_embedding_config", lambda: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("未配置时不应调用向量服务")

    monkeypatch.setattr(service, "embed_texts", forbidden)
    candidates, advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )
    assert candidates == [] and advisories == []
    assert status["configured"] is False
    assert status["reason"] == "unconfigured"


def test_embedding_failure_degrades_quietly(db_session, tmp_path, monkeypatch):
    import starhope.question_duplicate_service as service

    question = Question(content="向量服务故障时仍可保存。", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    monkeypatch.setattr(service, "get_indexer", lambda: _fake_index(tmp_path, question.id, [1.0, 0.0]))
    monkeypatch.setattr(service, "resolve_embedding_config", _config)

    def broken(texts, config, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(service, "embed_texts", broken)
    query = build_question_fingerprint(
        {"content": QUERY, "question_type": "detailed_answer"}
    )

    candidates, advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )

    assert candidates == [] and advisories == []
    assert status["reason"] == "embedding_failed"


def test_api_exposes_advisory_and_never_changes_level(
    client, db_session, tmp_path, monkeypatch
):
    import starhope.question_duplicate_service as service

    question = Question(content="另一道完全不同的几何题。", question_type="detailed_answer")
    db_session.add(question)
    db_session.commit()
    calls = {"count": 0}

    def fake_indexer():
        calls["count"] += 1
        return _fake_index(tmp_path, question.id, [1.0, 0.0])

    monkeypatch.setattr(service, "get_indexer", fake_indexer)
    monkeypatch.setattr(service, "resolve_embedding_config", _config)
    monkeypatch.setattr(
        service,
        "embed_texts",
        lambda texts, config, timeout=None: [[1.0, 0.0]],
    )

    response = client.post(
        "/api/questions/check-duplicates",
        json={
            "items": [
                {"client_key": "semantic", "content": QUERY, "question_type": "detailed_answer"}
            ]
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["level"] == "none"
    assert item["candidates"] == []
    assert calls["count"] == 1
    assert item["semantic"]["used"] is True
    match = item["semantic"]["matches"][0]
    assert match["id"] == question.id
    assert match["level"] == "semantic_near"
    assert match["reasons"] == ["SEMANTIC_NEAR"]
    assert match["auto_merge_allowed"] is False
    assert "不参与重复判定" in item["semantic"]["note"]


def test_word_level_hit_skips_embedding_call(client, db_session, monkeypatch):
    import starhope.question_duplicate_service as service

    content = "题库已有的原题 $x=1$，求 $x+1$。"
    client_db = Question(content=content, question_type="single_choice")
    db_session.add(client_db)
    db_session.commit()
    # Word-level recall needs a stored fingerprint.
    from starhope.question_duplicate_service import upsert_question_fingerprint

    upsert_question_fingerprint(
        db_session,
        client_db,
        build_question_fingerprint(
            {"content": content, "question_type": "single_choice"}
        ),
    )
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("词面已命中时不应调用向量服务")

    monkeypatch.setattr(service, "embed_texts", forbidden)

    response = client.post(
        "/api/questions/check-duplicates",
        json={"items": [{"client_key": "word", "content": content, "question_type": "single_choice"}]},
        headers=_headers(),
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["level"] == "exact"
    assert item["semantic"]["used"] is False


def test_disabled_switch_skips_embedding(db_session, tmp_path, monkeypatch):
    import starhope.question_duplicate_service as service

    question = Question(content="关闭开关后不应调用向量服务。", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    monkeypatch.setattr(service, "get_indexer", lambda: _fake_index(tmp_path, question.id, [1.0, 0.0]))
    monkeypatch.setattr(service, "resolve_embedding_config", _config)
    monkeypatch.setenv("SEMANTIC_DUPLICATE_ADVISORY", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("关闭后不应调用嵌入请求")

    monkeypatch.setattr(service, "embed_texts", forbidden)
    query = build_question_fingerprint(
        {"content": QUERY, "question_type": "detailed_answer"}
    )

    candidates, advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )

    assert candidates == [] and advisories == []
    assert status["used"] is False
    assert status["reason"] == "disabled"


def test_settings_toggle_round_trips_and_takes_effect(
    client, db_session, tmp_path, monkeypatch
):
    import main
    import starhope.question_duplicate_service as service

    monkeypatch.setattr(main, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("SEMANTIC_DUPLICATE_ADVISORY", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    headers = _headers()

    assert client.get("/api/settings").json()["semantic_duplicate_advisory"] is True
    saved = client.post(
        "/api/settings/save",
        data={"deepseek_key": "test-key", "semantic_duplicate_advisory": "false"},
        headers=headers,
    )
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["semantic_duplicate_advisory"] is False
    assert "SEMANTIC_DUPLICATE_ADVISORY=false" in (tmp_path / ".env").read_text()

    question = Question(content="设置关闭后查重不发嵌入请求。", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    monkeypatch.setattr(service, "get_indexer", lambda: _fake_index(tmp_path, question.id, [1.0, 0.0]))
    monkeypatch.setattr(service, "resolve_embedding_config", _config)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("界面关闭后不应调用嵌入请求")

    monkeypatch.setattr(service, "embed_texts", forbidden)
    query = build_question_fingerprint(
        {"content": QUERY, "question_type": "detailed_answer"}
    )
    _candidates, _advisories, status = service.find_semantic_duplicate_candidates(
        db_session,
        query,
        content=QUERY,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )
    assert status["reason"] == "disabled"

    restored = client.post(
        "/api/settings/save",
        data={"deepseek_key": "test-key", "semantic_duplicate_advisory": "true"},
        headers=headers,
    )
    assert restored.status_code == 200
    assert client.get("/api/settings").json()["semantic_duplicate_advisory"] is True


def test_large_batch_skips_semantic_recall(client, monkeypatch):
    import starhope.question_duplicate_service as service

    def forbidden(*_args, **_kwargs):
        raise AssertionError("批量查重不应逐题调用向量服务")

    monkeypatch.setattr(service, "embed_texts", forbidden)

    response = client.post(
        "/api/questions/check-duplicates",
        json={
            "items": [
                {
                    "client_key": f"batch-{index}",
                    "content": f"批量中的无关题 {index}：求 $x={index + 1}$。",
                    "question_type": "detailed_answer",
                }
                for index in range(4)
            ]
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["semantic"]["used"] is False
        assert item["semantic"]["reason"] == "batch_skipped"
