"""Semantic duplicate hints stay advisory in the import workspace UI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
API_SOURCE = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
INDEX_SOURCE = (ROOT / "static/index.html").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    start = SOURCE.index(f"function {name}(")
    depth = 0
    for index in range(start, len(SOURCE)):
        char = SOURCE[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[start : index + 1]
    raise AssertionError(f"unterminated function: {name}")


def test_semantic_matches_do_not_block_saving():
    body = _function_body("duplicateResultHasSignal")
    assert "semantic" not in body
    assert "SEMANTIC_NEAR" not in body


def test_semantic_advisory_badge_and_reason_are_rendered():
    assert "疑似改写（参考）" in SOURCE
    assert "SEMANTIC_NEAR" in SOURCE
    assert "语义相近，可能是改写题（仅参考，未参与重复判定）" in SOURCE
    assert "不参与重复判定" in SOURCE


def test_settings_offer_a_switch_to_disable_semantic_advisory():
    assert 'id="settingsSemanticAdvisory"' in INDEX_SOURCE
    assert "入库前查重使用语义相似提示" in INDEX_SOURCE
    assert "settings.semantic_duplicate_advisory !== false" in API_SOURCE
    assert "semantic_duplicate_advisory" in API_SOURCE
