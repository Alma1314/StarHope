"""Silent punctuation matches skip the review dialog in the import UI."""

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1].joinpath("static/js/import.js").read_text(
    encoding="utf-8"
)


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


def test_badge_prefers_silent_state_before_review_signal():
    body = _function_body("duplicateResultBadge")
    assert "duplicateResultIsSilentExact(result)" in body
    assert body.index("duplicateResultIsSilentExact(result)") < body.index(
        "duplicateResultHasSignal(result)"
    )
    assert "已收录（自动跳过）" in SOURCE


def test_review_dialogs_are_guarded_by_silent_check():
    for marker in (
        "题库中已有这道题（仅标点、空格或换行不同），本次未保存，避免重复。",
        "已视为同一题，本次未重复导入。",
    ):
        assert marker in SOURCE
    # Editor save and single import both return before the review dialog.
    assert SOURCE.count("duplicateResultIsSilentExact(result)") >= 3


def test_batch_auto_skips_silent_matches_before_the_suspect_list():
    start = SOURCE.index("const silentIndices = new Set();")
    suspect = SOURCE.index("const suspectIndices = eligibleIndices.filter", start)
    assert start < suspect
    assert "other_index" in SOURCE[start:suspect]
    assert "未重复入库" in SOURCE[start:suspect]
    assert "标点/空白差异自动跳过" in SOURCE
