"""Embedded Cozo graph index mirrors typed relations and stays derivable."""
import pytest

pytest.importorskip("pycozo")

from starhope.question_graph import QuestionGraph


def test_graph_upsert_traverse_and_remove(tmp_path):
    graph = QuestionGraph(tmp_path / "graph.db")
    try:
        assert graph.upsert(10, 1, 2, "variant_of")
        assert graph.upsert(11, 2, 3, "same_method")
        assert graph.edge_count() == 4

        walked = dict(graph.neighbors(1, 2))
        assert walked == {2: 1, 3: 2}
        assert dict(graph.neighbors(1, 1)) == {2: 1}

        assert graph.remove(10)
        assert dict(graph.neighbors(1, 2)) == {}
        assert graph.edge_count() == 2

        assert graph.remove_question(3)
        assert graph.edge_count() == 0
    finally:
        graph.close()


def test_graph_rebuild_from_database(tmp_path, db_session):
    from starhope.database import Question, QuestionRelation

    first = Question(content="第一题", question_type="detailed_answer",
                     difficulty="normal", subject="数学")
    second = Question(content="第二题", question_type="detailed_answer",
                      difficulty="normal", subject="数学")
    third = Question(content="第三题", question_type="detailed_answer",
                     difficulty="normal", subject="数学")
    db_session.add_all([first, second, third])
    db_session.commit()
    db_session.add_all([
        QuestionRelation(source_id=first.id, target_id=second.id,
                         relation_type="variant_of"),
        QuestionRelation(source_id=second.id, target_id=third.id,
                         relation_type="related"),
    ])
    db_session.commit()

    graph = QuestionGraph(tmp_path / "graph.db")
    try:
        assert graph.rebuild(db_session) == 2
        assert dict(graph.neighbors(first.id, 2)) == {second.id: 1, third.id: 2}
        # Rebuild is idempotent, not additive.
        assert graph.rebuild(db_session) == 2
        assert graph.edge_count() == 4
    finally:
        graph.close()
