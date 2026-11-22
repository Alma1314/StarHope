"""Identical text after dropping images, whitespace and Chinese punctuation."""

from dataclasses import replace
import pytest

from starhope.question_duplicates import (
    compare_questions,
    punctuation_insensitive_text,
)


def _compare(left, right, **extra):
    return compare_questions(
        {"content": left, **extra}, {"content": right, **extra}
    )


def test_punctuation_and_whitespace_only_difference_is_a_silent_match():
    result = _compare(
        "设 $x=1$，求 $x+1$ 的值。\n并说明理由。",
        "设$x=1$ 求$x+1$的值\n\n并说明理由",
    )
    assert result.classification == "exact"
    assert result.silent_exact is True
    assert result.needs_visual_review is False
    assert "EXACT_IGNORING_PUNCTUATION" in result.reasons
    assert result.to_dict()["silent_exact"] is True


def test_visibility_differences_are_ignored_for_silent_matches():
    left = {"content": "求 $x+1$ 的值。"}
    right = {
        "content": "求 $x+1$ 的值",
        "visible_image_signatures": ("a" * 64,),
    }
    result = compare_questions(left, right)
    assert result.silent_exact is True
    assert result.needs_visual_review is False


def test_math_tokens_are_never_stripped():
    for left, right in (
        ("计算 $x-1$。", "计算 $x1$。"),
        ("求 $1.5$ 的值。", "求 $15$ 的值。"),
        ("求 $n!$。", "求 $n$。"),
        ("区间 $(0,1)$。", "区间 $01$。"),
        ("集合 $\\{1,2\\}$。", "集合 $\\{12\\}$。"),
        ("比例 $1:2$。", "比例 $12$。"),
    ):
        result = _compare(left, right)
        assert result.silent_exact is False, (left, right)


def test_choice_order_and_different_words_are_not_silent_matches():
    left = {
        "content": "下列说法正确的是\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\end{choices}"
    }
    right = {
        "content": "下列说法正确的是\n\\begin{choices}\n\\item 乙\n\\item 甲\n\\end{choices}"
    }
    assert compare_questions(left, right).silent_exact is False
    assert _compare("求最大值。", "求最小值。").silent_exact is False


def test_empty_lenient_text_never_matches():
    assert punctuation_insensitive_text("，。！？…—～") == ""
    assert _compare("。，、", "？！").silent_exact is False
    assert _compare("", "").silent_exact is False


@pytest.mark.parametrize("left,right", [
    ("求n !", "求n"), ("求f'(x)", "求f(x)"),
    ("区间(0,1)", "区间(0,1]"), ("比例1:2", "比例12"),
    ("变量x y", "变量xy"), ("两个数1 2", "两个数12"),
    ("计算`a,b`", "计算`ab`"),
    (r"计算\begin{equation}n!\end{equation}", r"计算\begin{equation}n\end{equation}"),
    ("函数性质正确的是", "函数性质不正确的是"),
    ("求实数解", "求整数解"),
])
def test_mathematical_or_literal_differences_never_silently_match(left, right):
    comparison = _compare(left, right)
    assert not comparison.silent_exact
    assert comparison.classification not in {"exact", "probable"}


def test_identical_text_is_silent_even_with_identical_assets():
    assert _compare("完全相同的题目 $x=1$。", "完全相同的题目 $x=1$。").silent_exact
    assert _compare("同一配图题", visible_image_signatures=("pixels",), right="同一配图题").silent_exact
    assert not _compare("![图](/static/uploads/a.png)", "![图](/static/uploads/b.png)").silent_exact


def test_image_dollar_signs_do_not_mask_adjacent_math():
    assert _compare("求$x$![图$](/static/uploads/a.png)，值。", "求$x$值").silent_exact


def test_library_silent_recall_does_not_depend_on_approximate_bands(db_session, tmp_path):
    from sqlalchemy import event
    from starhope.database import Question
    from starhope.question_duplicate_service import find_indexed_candidates, exact_duplicate_ids, upsert_question_fingerprint
    from starhope.question_duplicates import build_question_fingerprint

    stored = Question(content="设$x=1$，求值。")
    db_session.add(stored)
    db_session.flush()
    original = build_question_fingerprint({"content": stored.content})
    query = build_question_fingerprint({"content": "设$x=1$求值"})
    # Force both approximate stages and their length windows to miss.
    indexed = replace(original, bands=tuple((value + 1) % 65536 for value in query.bands),
                      text_bands=("0000000000000000",) * 8, token_count=10000)
    upsert_question_fingerprint(db_session, stored, indexed)
    db_session.commit()
    statements = []
    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if "silent_hash" in statement and statement.lstrip().startswith("SELECT"):
            statements.append((statement, parameters))
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        candidates = find_indexed_candidates(db_session, query, uploads_dir=tmp_path, url_prefix="static/uploads")
        assert exact_duplicate_ids(db_session, query) == [stored.id]
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert [candidate.question.id for candidate in candidates] == [stored.id]
    assert candidates[0].comparison.silent_exact
    plan = db_session.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statements[0][0], statements[0][1]).all()
    assert "idx_question_fingerprints_silent" in str(plan)


def test_api_reports_silent_exact_for_library_match(client, db_session):
    from main import LOCAL_TOKEN
    from starhope.database import Question
    from starhope.question_duplicate_service import upsert_question_fingerprint
    from starhope.question_duplicates import build_question_fingerprint

    content = "设 $x=2$，求 $x+1$ 的值。"
    stored = Question(content=content, question_type="detailed_answer")
    db_session.add(stored)
    db_session.commit()
    upsert_question_fingerprint(
        db_session,
        stored,
        build_question_fingerprint(
            {"content": content, "question_type": "detailed_answer"}
        ),
    )
    db_session.commit()

    response = client.post(
        "/api/questions/check-duplicates",
        json={
            "items": [
                {
                    "client_key": "silent",
                    "content": "设$x=2$ 求$x+1$的值",
                    "question_type": "detailed_answer",
                }
            ]
        },
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["level"] == "exact"
    assert item["silent_exact"] is True
    assert item["candidates"][0]["silent_exact"] is True
    assert item["candidates"][0]["needs_visual_review"] is False


def test_batch_local_silent_pairs_are_reported_per_pair(client):
    from main import LOCAL_TOKEN

    response = client.post(
        "/api/questions/check-duplicates",
        json={
            "items": [
                {"client_key": "a", "content": "同一道题 $x^2=4$。", "question_type": "detailed_answer"},
                {"client_key": "b", "content": "同一道题$x^2=4$", "question_type": "detailed_answer"},
            ]
        },
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert response.status_code == 200
    items = {item["client_key"]: item for item in response.json()["items"]}
    assert items["a"]["silent_exact"] is False
    matches = items["b"]["batch_matches"]
    assert matches and matches[0]["silent_exact"] is True
    assert matches[0]["other_index"] == 0
