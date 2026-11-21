"""Printed answers from another chapter must never match by bare number."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from starhope.book_import import assemble_book, extract_book, validate_batch, build_book_prompt
from starhope.ai_providers import resolve_text_provider
from starhope.task_manager import TaskCancelled

METADATA = {"question_types": ["detailed_answer", "single_choice"], "difficulties": ["normal", "challenge"]}
PROVIDER = resolve_text_provider("SILICONFLOW/test-model", {"SILICONFLOW_API_KEY": "fake-no-network"})


def row(kind="question", chapter="第一章", number="习题1", page=1, **extra):
    return {"kind": kind, "chapter": chapter, "section": "练习1.1", "number": number,
            "start_page": page, "end_page": page, "content": "求$x+1$。" if kind == "question" else "$2$。",
            "answer_markdown": "", "question_type": "detailed_answer" if kind == "question" else "",
            "difficulty": "normal" if kind == "question" else "", "topics": ["一次函数"] if kind == "question" else [],
            **extra}


def packet(records, **extra):
    return {"records": records, "unresolved": [], "context": {"chapter": "第一章", "section": "练习1.1", "role": "body"}, **extra}


def reply(value, finish="stop"):
    return SimpleNamespace(json=lambda: {"choices": [{"finish_reason": finish,
        "message": {"content": json.dumps(value, ensure_ascii=False)}}]})


def test_book_matches_distant_answers_by_chapter_and_keeps_source_pages():
    rows = [row(chapter="第一章"), row(chapter="第二章", page=5),
            row("answer", chapter="第二章", page=500, content="$20$。"),
            row("answer", chapter="第一章", page=501)]
    before = deepcopy(rows)
    questions, report = assemble_book(rows, title="物理书", subject="物理")
    assert [q["answer_markdown"] for q in questions] == ["$2$。", "$20$。"]
    assert questions[0]["book_answer_pages"] == [501]
    assert "答案第501" in questions[0]["source"]
    assert all(q["subject"] == "物理" and q["book_import"] for q in questions)
    assert report["matched_answers"] == 2 and not report["unmatched_answers"]
    assert rows == before


@pytest.mark.parametrize("change", ["duplicate_question", "duplicate_answer", "no_headings", "inline_conflict"])
def test_ambiguous_answers_are_retained_without_guessing(change):
    rows = [row(), row("answer", page=90)]
    if change == "duplicate_question": rows.insert(0, row(page=2))
    if change == "duplicate_answer": rows.append(row("answer", page=91))
    if change == "no_headings":
        for item in rows: item.update(chapter="", section="")
    if change == "inline_conflict": rows[0]["answer_markdown"] = "原有紧邻答案"
    questions, report = assemble_book(rows, title="书", subject="数学")
    assert report["matched_answers"] == 0 and report["unmatched_answers"]
    assert all(q["answer_markdown"] in ("", "原有紧邻答案") for q in questions)


@pytest.mark.parametrize("bad", ["page", "gap", "image", "level", "duplicate_field"])
def test_invalid_source_and_envelope_are_rejected(bad):
    pages = [{"page_number": 1, "markdown": "原文"}, {"page_number": 3, "markdown": "原文"}]
    value = packet([row()])
    if bad == "page": value["records"][0]["start_page"] = True
    if bad == "gap": value["records"][0]["end_page"] = 3
    if bad == "image": value["records"][0]["content"] = "![](/static/uploads/other.png)"
    if bad == "level": value["records"][0]["difficulty"] = "猜测"
    if bad == "duplicate_field": value["unknown"] = True
    with pytest.raises(ValueError): validate_batch(value, pages, [1], METADATA)


def test_context_page_records_are_skipped_without_losing_core_records():
    pages = [{"page_number": number, "markdown": "原文"} for number in range(1, 7)]
    value = packet([row(page=5), row(page=2, number="习题2")])
    adopted, notes, _context = validate_batch(value, pages, [1, 2, 3, 4], METADATA)
    assert [item["start_page"] for item in adopted] == [2]
    assert any("上下文页" in note and "第5页" in note for note in notes)


def test_batch_with_only_context_records_completes_empty():
    pages = [{"page_number": number, "markdown": "原文"} for number in range(1, 7)]
    adopted, notes, _context = validate_batch(packet([row(page=6)]), pages, [1, 2, 3, 4], METADATA)
    assert adopted == []
    assert any("上下文页" in note and "第6页" in note for note in notes)


def test_cross_page_context_ownership_and_original_answer_pairing():
    pages = [{"page_number": number, "markdown": f"第{number}页内容"} for number in range(1, 10)]
    calls = []
    def post(provider, payload, **kwargs):
        data = json.loads(payload["messages"][1]["content"])
        calls.append((data, kwargs))
        core = data["core_pages"]
        if core[0] == 1: return reply(packet([row(page=4, end_page=5)]))
        if core[0] == 5: return reply(packet([row("answer", page=8)]))
        return reply(packet([]))
    diagnostics = {}
    questions = extract_book(pages, title="书", subject="数学", provider=PROVIDER, metadata=METADATA,
                             diagnostics=diagnostics, post=post)
    assert len(questions) == 1 and questions[0]["book_pages"] == [4, 5]
    assert questions[0]["answer_markdown"] == "$2$。"
    assert [value[0]["core_pages"] for value in calls] == [[1, 2, 3, 4], [5, 6, 7, 8], [9]]
    assert calls[0][0]["pages"][-1]["page_number"] == 6
    assert all(value[1]["retry_connection"] is False and value[1]["allow_redirects"] is False for value in calls)
    assert diagnostics["book_import"]["calls"] == 3
    assert not diagnostics["book_import"]["generated_answers"]
    assert "不能求解" in build_book_prompt("数学", METADATA)


def test_truncated_or_failed_batch_preserves_other_batches_and_does_not_retry():
    pages = [{"page_number": number, "markdown": "题目"} for number in range(1, 9)]
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return reply(packet([row()])) if len(calls) == 1 else reply(packet([]), finish="length")
    diagnostics = {}
    questions = extract_book(pages, title="书", subject="数学", provider=PROVIDER, metadata=METADATA,
                             diagnostics=diagnostics, post=post)
    assert len(calls) == 2 and len(questions) == 1
    assert diagnostics["book_import"]["batches"][1]["status"] == "failed"
    assert diagnostics["book_import"]["unresolved"]


def test_context_echo_does_not_fail_the_owning_batch():
    pages = [{"page_number": number, "markdown": "原文"} for number in range(1, 9)]
    calls = []
    def post(provider, payload, **kwargs):
        data = json.loads(payload["messages"][1]["content"])
        calls.append(data)
        if data["core_pages"][0] == 1:
            return reply(packet([row(page=5), row(page=2)]))
        return reply(packet([]))
    diagnostics = {}
    questions = extract_book(pages, title="书", subject="数学", provider=PROVIDER, metadata=METADATA,
                             diagnostics=diagnostics, post=post)
    assert len(questions) == 1 and questions[0]["book_pages"] == [2]
    first = diagnostics["book_import"]["batches"][0]
    assert first["status"] == "completed" and first["records"] == 1
    assert any("上下文页" in note for note in diagnostics["book_import"]["unresolved"])


def test_cancellation_and_oversized_input_never_start_a_paid_request():
    def forbidden(*args, **kwargs): pytest.fail("must not call model")
    def cancel(): raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        extract_book([{"page_number": 1, "markdown": "原文"}], title="书", subject="数学", provider=PROVIDER,
                     metadata=METADATA, diagnostics={}, check_cancelled=cancel, post=forbidden)
    with pytest.raises(ValueError):
        extract_book([{"page_number": i, "markdown": "原文"} for i in range(601)], title="书", subject="数学",
                     provider=PROVIDER, metadata=METADATA, diagnostics={}, post=forbidden)


def test_import_receipt_binds_subject_and_topics_but_keeps_historical_default():
    from starhope.document_project_import import import_payload_hash
    original = {"content": "原文"}
    default = {**original, "subject": "数学", "subject_topics": ""}
    assert import_payload_hash(original) == import_payload_hash(default)
    assert import_payload_hash(default) != import_payload_hash({**default, "subject": "物理"})
    assert import_payload_hash(default) != import_payload_hash({**default, "subject_topics": "函数"})
