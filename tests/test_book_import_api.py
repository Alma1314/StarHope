"""Isolated application wiring; model replies and native PDF text are fixtures."""
from functools import partial
import json
from types import SimpleNamespace
import uuid

import pymupdf as fitz

from starhope.ai_providers import resolve_text_provider


def test_upload_routes_book_options_without_automatic_solution_generation(client, monkeypatch):
    import main
    client.headers['X-Local-Token'] = main.LOCAL_TOKEN
    seen = []
    def queue(content, filename, kind, request_id, options, worker, args):
        seen.append((options, worker, args))
        return {"status": "success", "task_id": "fake-book"}
    monkeypatch.setattr(main, "_queue_document_upload", queue)
    response = client.post('/api/upload/pdf-task', data={"pdf_document_kind": "book", "book_subject": "物理", "book_education_stage": "初中",
        "generate_answers": "true", "pdf_strategy": "native_preferred"},
        files={"file": ("书籍.pdf", b"%PDF-fixture", "application/pdf")})
    assert response.status_code == 200, response.text
    options, worker, args = seen[0]
    assert options["pdf_document_kind"] == "book" and options["book_subject"] == "物理"
    assert options["book_education_stage"] == "初中" and worker.keywords["book_education_stage"] == "初中"
    assert options["generate_answers"] is False and args[0] is False
    assert isinstance(worker, partial) and worker.keywords["pdf_document_kind"] == "book"
    for data in ({"pdf_document_kind": "bad"}, {"pdf_document_kind": "book", "book_subject": "bad"}):
        response = client.post('/api/upload/pdf-task', data=data,
            files={"file": ("书籍.pdf", b"%PDF-fixture", "application/pdf")})
        assert response.status_code == 400
    assert len(seen) == 1


def test_real_pdf_worker_uses_book_extractor_and_preserves_answers(client, monkeypatch):
    import main
    provider = resolve_text_provider("SILICONFLOW/test", {"SILICONFLOW_API_KEY": "fake-no-network"})
    monkeypatch.setattr(main, "resolve_text_provider", lambda _: provider)
    text = "第一章 运动\n练习1.1\n1. 速度是多少？\n【答案】每秒2米。"
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pdf_type": "native", "pages": [{"page_index": i, "markdown": text, "needs_ocr": False} for i in range(2)]})
    monkeypatch.setattr(main, "MAX_PDF_TASK_PAGES", 1)
    def forbidden(*a, **k): raise AssertionError("book must not use math exam splitter or solve")
    monkeypatch.setattr(main, "parse_paper_text_internal", forbidden)
    monkeypatch.setattr(main, "ocr_pdf_page_image", forbidden)
    calls = []
    def post(provider, payload, **kwargs):
        calls.append(payload)
        row = {"kind": "question", "chapter": "第一章 运动", "section": "练习1.1", "number": "1",
               "start_page": 1, "end_page": 1, "content": "速度是多少？", "answer_markdown": "每秒2米。",
               "question_type": "detailed_answer", "difficulty": "normal", "topics": ["速度"]}
        value = {"records": [row], "unresolved": [], "context": {"chapter": "第一章 运动", "section": "练习1.1", "role": "body"}}
        return SimpleNamespace(json=lambda: {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(value, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    with fitz.open() as document:
        document.new_page(width=100, height=100)
        document.new_page(width=100, height=100)
        content = document.tobytes()
    identifier = str(uuid.uuid4())
    main.DOCUMENT_TASKS.create(identifier, document_type="pdf", temp_assets=[], document_input={"options": {"paper_title": "物理书"}})
    try:
        main.run_pdf_parsing_task(identifier, content, "书.pdf", pdf_document_kind="book", book_subject="物理", book_education_stage="初中")
        result = main.DOCUMENT_TASKS.snapshot(identifier)
        assert result["status"] == "completed", result.get("error")
        assert len(calls) == 1 and result["generate_answers"] is False
        assert result["data"][0]["education_stage"] == "初中"
        assert result["data"][0]["subject"] == "物理"
        assert result["data"][0]["subject_topics"] == "速度"
        assert result["data"][0]["answer_markdown"] == "每秒2米。"
        assert result["data"][0]["category_compulsory"] == ""
        assert result["page_numbers"] == [1, 2]
        paper_id = str(uuid.uuid4())
        main.DOCUMENT_TASKS.create(paper_id, document_type="pdf", temp_assets=[])
        try:
            main.run_pdf_parsing_task(paper_id, content, "试卷.pdf")
            assert main.DOCUMENT_TASKS.snapshot(paper_id)["status"] == "error"
            assert len(calls) == 1, "ordinary paper limit must still stop before AI"
        finally:
            main.DOCUMENT_TASKS.remove(paper_id)
    finally:
        task = main.DOCUMENT_TASKS.remove(identifier)
        if task: main._delete_task_temp_assets(task.get("temp_assets", []))
