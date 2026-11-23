"""Synthetic PDFs only: local recovery cannot erase source fidelity risks."""
import json
from io import BytesIO
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from PIL import Image


from starhope import markitdown_helper as local
from starhope import pdf_inspector_helper as inspector
from starhope.pdf_source_scopes import _NativePdfEvidence, _open
from starhope.task_manager import TaskCancelled


TEXT = "普通中文提问：请根据原文简述这一次学习过程，列举生活中的实例并给出文字解释。"


def pdf(*, pages=1, risk=None):
    font = fitz.Font(fontname="china-s")
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page()
            page.insert_font(fontname="prose", fontbuffer=font.buffer)
            page.insert_text((40, 80), f"{index + 1}. {TEXT}", fontname="prose", fontsize=10)
            if risk == "math":
                page.insert_text((40, 110), "x=2", fontsize=10)
            elif risk == "covered":
                page.draw_rect(fitz.Rect(38, 66, 480, 85), fill=(1, 1, 1), overlay=True)
            elif risk == "columns":
                page.insert_text((350, 80), "另栏文字", fontname="prose", fontsize=10)
            elif risk == "vector":
                page.draw_line((50, 100), (100, 150))
            elif risk == "bitmap":
                image = BytesIO()
                Image.new("RGB", (12, 12), "red").save(image, format="PNG")
                page.insert_image(fitz.Rect(50, 100, 100, 150), stream=image.getvalue())
            elif risk == "invisible":
                page.insert_text((40, 110), "隐藏内容", fontname="prose", fontsize=10, render_mode=3)
            elif risk == "rotated":
                page.set_rotation(90)
        return document.tobytes(deflate=True)


def reject_native(monkeypatch, count=1):
    monkeypatch.setattr(inspector, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(inspector, "pdf_inspector", SimpleNamespace(
        extract_pages_markdown=lambda *_a, **_kw: SimpleNamespace(pages=[
            SimpleNamespace(page=i, markdown="原提取故障证据", needs_ocr=True)
            for i in range(count)
        ])))


def test_real_converter_recovers_rejected_page_without_ocr_and_keeps_private_proof(monkeypatch):
    reject_native(monkeypatch)
    result = inspector.inspect_and_extract_pdf(pdf(), include_source_review_evidence=True)
    assert result["pages_needing_ocr"] == []
    assert result["pdf_type"] == "text_based"
    row = result["pages"][0]
    assert row["source"] == "markitdown"
    assert row["markdown"] == "1. " + TEXT
    assert row["original_markdown"] == "原提取故障证据"
    assert row["markitdown"]["adopted"] is True
    assert row["markitdown"]["original_quality_reasons"]
    evidence = _open(_NativePdfEvidence, result["_native_source_review_evidence"])
    assert evidence["pages"][0]["reliable"] is True
    assert evidence["pages"][0]["native_origin"] == "markitdown"


@pytest.mark.parametrize("risk", ["math", "covered", "columns", "vector", "bitmap", "invisible", "rotated"])
def test_risky_pages_never_reach_converter(monkeypatch, risk):
    reject_native(monkeypatch)
    def unexpected(*_a, **_kw):
        pytest.fail("A risky page must retain its OCR route")
    monkeypatch.setattr(local, "_run_converter", unexpected)
    result = inspector.inspect_and_extract_pdf(pdf(risk=risk))
    assert result["pages_needing_ocr"] == [0]
    assert result["pages"][0]["markdown"] == "原提取故障证据"


def test_page_selection_and_order_remain_original_and_already_native_is_not_converted(monkeypatch):
    rows = [dict(page_index=i, markdown=f"{i + 1}. {TEXT}", needs_ocr=i != 1,
                 source="pdf-inspector", quality_reasons=["原提取故障"] if i != 1 else []) for i in [2, 1, 0]]
    monkeypatch.setattr(inspector, "_inspect_and_extract_pdf", lambda *_a, **_kw:
        inspector._build_result(available=True, pages=rows, pdf_type="mixed"))
    result = inspector.inspect_and_extract_pdf(pdf(pages=3), page_indices=[2, 1, 0])
    assert [p["page_index"] for p in result["pages"]] == [2, 1, 0]
    assert [p["source"] for p in result["pages"]] == ["markitdown", "pdf-inspector", "markitdown"]
    assert result["markdown"].index("PAGE:3") < result["markdown"].index("PAGE:2") < result["markdown"].index("PAGE:1")


@pytest.mark.parametrize("payload", [None, {"markdown": "改错一个字"}, {"markdown": ""}, {"markdown": 3}])
def test_failed_or_changed_candidate_does_not_replace_original(monkeypatch, payload):
    reject_native(monkeypatch)
    def convert(directory, _check):
        if payload is not None:
            (directory / "0.json").write_text(json.dumps(payload), encoding="utf-8")
        return "unavailable" if payload is None else "completed"
    monkeypatch.setattr(local, "_run_converter", convert)
    result = inspector.inspect_and_extract_pdf(pdf())
    assert result["pdf_type"] == "scanned"
    assert result["pages_needing_ocr"] == [0]
    assert result["pages"][0]["markdown"] == "原提取故障证据"
    assert result["pages"][0]["markitdown"]["adopted"] is False


def test_timeout_and_cancel_stop_only_own_child_without_real_wait(monkeypatch, tmp_path):
    calls = []
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: calls.append("terminate"),
                              wait=lambda **kw: calls.append(kw["timeout"]) or 0)
    monkeypatch.setattr(local.subprocess, "Popen", lambda *_a, **_kw: process)
    ticks = iter([0, 31])
    monkeypatch.setattr(local.time, "monotonic", lambda: next(ticks))
    assert local._run_converter(tmp_path, lambda: None) == "timeout"
    assert calls == ["terminate", 2]
    calls.clear()
    monkeypatch.setattr(local.time, "monotonic", lambda: 0)
    def cancel():
        raise TaskCancelled("synthetic cancellation")
    with pytest.raises(TaskCancelled):
        local._run_converter(tmp_path, cancel)
    assert calls == ["terminate", 2]


def test_page_budget_does_not_start_unbounded_conversion(monkeypatch):
    reject_native(monkeypatch, 3)
    monkeypatch.setattr(local, "MAX_PAGES", 1)
    seen = []
    def convert(directory, _check):
        seen.extend(path.stem for path in directory.glob("*.pdf"))
        (directory / "0.json").write_text(json.dumps({"markdown": "1. " + TEXT}), encoding="utf-8")
        return "completed"
    monkeypatch.setattr(local, "_run_converter", convert)
    result = inspector.inspect_and_extract_pdf(pdf(pages=3))
    assert seen == ["0"]
    assert result["pages_needing_ocr"] == [1, 2]
    assert result["pdf_type"] == "mixed"


def test_pdf_task_reuses_recovered_text_and_reports_no_ocr(monkeypatch):
    # Run this API/task test only in the source-only validation copy, as with
    # the existing PDF task suites. Every possible provider call is forbidden.
    import main
    import requests
    reject_native(monkeypatch)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live model calls"))
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *_a, **_kw: pytest.fail("Recovered page must not be OCRed"))
    splits = []
    def split(text, *_args, **_kwargs):
        splits.append(text)
        return [{"content": TEXT, "answer_markdown": "", "question_type": "detailed_answer"}]
    monkeypatch.setattr(main, "parse_paper_text_internal", split)
    task_id = "synthetic-markitdown-pdf-task"
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf(), "synthetic.pdf", pdf_strategy="native_preferred",
                                  pdf_verify_suspicions=False)
        result = main.DOCUMENT_TASKS.snapshot(task_id)
        assert result["status"] == "completed", result.get("error")
        assert len(splits) == 1 and TEXT in splits[0]
        report = result["diagnostics"]["pdf_extraction"]
        assert report["native_pages"] == report["markitdown_pages"] == 1
        assert report["full_vision_pages"] == report["regional_pages"] == 0
        assert result["data"][0]["content"] == TEXT
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
