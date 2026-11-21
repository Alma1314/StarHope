"""Typed question relations are directed and never merge variant groups."""
import pytest


@pytest.fixture(autouse=True)
def _no_paid_embeddings(monkeypatch):
    from starhope import semantic_index
    monkeypatch.setattr(semantic_index, "resolve_embedding_config", lambda environment=None: None)
    yield
    semantic_index.get_indexer().close()


def _create_question(client, content):
    response = client.post("/api/questions", data={
        "content": content,
        "question_type": "detailed_answer",
        "difficulty": "normal",
    })
    assert response.status_code == 200, response.text
    return response.json()["question"]["id"]


def test_typed_relations_traverse_without_merging_association_groups(client, db_session):
    import main
    from starhope.database import Question

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    first = _create_question(client, "已知等差数列首项为1，求第10项")
    second = _create_question(client, "已知等差数列首项为2，求第10项")
    third = _create_question(client, "已知等比数列首项为1，求第10项")

    response = client.post(f"/api/questions/{first}/relations", data={
        "target_id": second, "relation_type": "variant_of", "basis": "仅参数不同",
    })
    assert response.status_code == 200, response.text
    assert client.post(f"/api/questions/{second}/relations", data={
        "target_id": third, "relation_type": "same_method",
    }).status_code == 200

    # Same edge again is rejected rather than duplicated.
    assert client.post(f"/api/questions/{first}/relations", data={
        "target_id": second, "relation_type": "variant_of",
    }).status_code == 409
    # Self edges and unknown types are rejected.
    assert client.post(f"/api/questions/{first}/relations", data={
        "target_id": first, "relation_type": "variant_of",
    }).status_code == 400
    assert client.post(f"/api/questions/{first}/relations", data={
        "target_id": second, "relation_type": "made_up",
    }).status_code == 400

    graph = client.get(f"/api/questions/{first}/relations", params={"depth": 2})
    assert graph.status_code == 200, graph.text
    payload = graph.json()
    assert payload["graph_backend"] == "cozo"
    assert {node["id"] for node in payload["nodes"]} == {first, second, third}
    assert {node["id"]: node["depth"] for node in payload["nodes"]}[second] == 1
    assert {node["id"]: node["depth"] for node in payload["nodes"]}[third] == 2
    assert len(payload["edges"]) == 2

    # Similarity chains must not create or inherit a variant group.
    for question_id in (first, second, third):
        row = db_session.get(Question, question_id)
        db_session.refresh(row)
        assert not row.association_group_id

    relation_id = payload["edges"][0]["id"]
    updated = client.put(f"/api/question-relations/{relation_id}", data={
        "status": "confirmed", "basis": "人工确认",
    })
    assert updated.status_code == 200, updated.text
    assert client.delete(f"/api/question-relations/{relation_id}").status_code == 200
    remaining = client.get(
        f"/api/questions/{second}/relations", params={"depth": 2}
    ).json()["edges"]
    assert [edge["id"] for edge in remaining] == [
        edge["id"] for edge in payload["edges"] if edge["id"] != relation_id
    ]


def test_relation_depth_is_bounded(client, db_session):
    import main

    client.headers["X-Local-Token"] = main.LOCAL_TOKEN
    first = _create_question(client, "第一题")
    second = _create_question(client, "第二题")
    assert client.post(f"/api/questions/{first}/relations", data={
        "target_id": second, "relation_type": "related",
    }).status_code == 200
    assert client.get(
        f"/api/questions/{first}/relations", params={"depth": 4}
    ).status_code == 422


def test_backup_counts_include_relations_only_from_v21():
    from starhope.backup import _backup_count_tables

    assert "question_relations" not in _backup_count_tables(20)
    assert "question_relations" in _backup_count_tables(21)
