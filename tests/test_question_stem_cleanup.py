"""Peripheral answer/source text is separated conservatively before saving."""

import pytest

from starhope.question_stem import clean_question_fields, strip_leading_question_number
from starhope.question_duplicates import compare_questions


@pytest.mark.parametrize("raw, expected", [
    ("【12】已知$x=1$。", "已知$x=1$。"),
    ("[12] 已知$x=1$。", "已知$x=1$。"),
    ("第3题：已知$x=1$。", "已知$x=1$。"),
    ("（1）求值", "（1）求值"),
    ("1.5万元", "1.5万元"),
])
def test_number_boundaries(raw, expected):
    assert strip_leading_question_number(raw) == expected


def test_moves_explicit_source_and_answer_without_losing_them():
    raw = "12.（2024 北京高考）已知$x=1$，求$x+1$。\n【答案】$2$\n【解析】代入即可。"
    result = clean_question_fields(raw, "", "原始收录")
    assert result == ("已知$x=1$，求$x+1$。", "$2$\n【解析】代入即可。", "原始收录 · 第12题 · 2024 北京高考")
    assert clean_question_fields(*result) == result


@pytest.mark.parametrize("raw", [
    "来源：某校模拟卷\n求$x$。",
    "求$x$。\n来源：某校模拟卷",
    "【来源：某校模拟卷】求$x$。",
])
def test_explicit_boundary_source_moves_to_metadata(raw):
    assert clean_question_fields(raw) == ("求$x$。", "", "某校模拟卷")


@pytest.mark.parametrize("raw", [
    "（2024年某校高考人数为100人）求概率。",
    "（0,1）内的实数满足什么条件？",
    "分析数据的来源：抽样调查。",
    "题目\n来源：下面给出统计表\n求概率。",
    "```text\n答案：样例\n```\n求值。",
    "题目\n```text\n答案：样例\n```",
    "题目\n$$\\text{答案：示例}$$",
    "答案：只有答案，不能删空题干",
])
def test_preserves_conditions_literals_and_ambiguous_text(raw):
    assert clean_question_fields(raw) == (raw, "", "")


def test_conflicting_answer_is_preserved_for_review():
    raw = "求$x$。\n答案：$1$"
    assert clean_question_fields(raw, "$2$") == (raw, "$2$", "")
    assert clean_question_fields(raw, "$1$") == ("求$x$。", "$1$", "")


@pytest.mark.parametrize("raw, qtype, stem, answer", [
    ("下列正确的是（A）", "single_choice", "下列正确的是", "A"),
    ("结果为\\fillin（$2$）", "fill_in_blank", "结果为\\fillin", "$2$"),
    ("结果为\\fillin、\\fillin（2）", "fill_in_blank", "结果为\\fillin、\\fillin（2）", ""),
    ("考虑点（A）", "single_choice", "考虑点（A）", ""),
    ("下列正确的是（A，B）", "single_choice", "下列正确的是（A，B）", ""),
])
def test_trailing_answer_requires_unambiguous_context(raw, qtype, stem, answer):
    assert clean_question_fields(raw, question_type=qtype)[:2] == (stem, answer)


def test_duplicate_comparison_ignores_extracted_number_source_and_answer():
    result = compare_questions(
        {"content": "12.（2024 北京高考）求$x+1$。\n答案：$2$"},
        {"content": "求$x+1$。", "answer_markdown": "$2$"},
    )
    assert result.silent_exact


def test_precheck_and_save_share_cleanup_and_snapshot(client):
    from main import LOCAL_TOKEN
    headers = {"X-Local-Token": LOCAL_TOKEN}
    content = "[12]（2024 北京高考）求$x+1$。\n答案：$2$"
    payload = {"content": content, "answer_markdown": "", "source": "", "question_type": "detailed_answer"}
    checked = client.post("/api/questions/check-duplicates", headers=headers,
                          json={"items": [{"client_key": "cleanup", **payload}]})
    assert checked.status_code == 200
    saved = client.post("/api/questions", headers=headers, data={
        **payload, "difficulty": "normal", "image_paths": "[]",
        "duplicate_snapshot_hash": checked.json()["items"][0]["snapshot_hash"],
    })
    assert saved.status_code == 200, saved.text
    question = saved.json()["question"]
    assert question["content"] == "求$x+1$。"
    assert question["answer_markdown"] == "$2$"
    assert question["source"] == "第12题 · 2024 北京高考"
    repeated = client.post("/api/questions", headers=headers, data={
        **payload, "content": "求$x+1$。", "difficulty": "normal", "image_paths": "[]",
    })
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "duplicate_already_exists"
    normalized = client.post("/api/questions", headers=headers, data={
        **payload, "content": r"求\(x + 1\)。", "difficulty": "normal", "image_paths": "[]",
    })
    assert normalized.status_code == 409
    assert normalized.json()["code"] == "duplicate_already_exists"
