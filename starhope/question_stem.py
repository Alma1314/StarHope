"""Strip the source question number from an imported stem.

The paper number belongs to the source metadata (``questions.source``); a
parenthesized leading number may instead be a sub-question label such as
``(1)``.  Strip only when the label is unambiguous: an explicit paper number in
``source``, a bare ``N.``/``第N题`` label without a conflicting source number,
or the same number declared by the source.  Decimals (``1.5``) and
parenthesized labels without source evidence are preserved.
"""

from __future__ import annotations

import re

_LEADING_NUMBER = re.compile(
    r"^\s*(?:"
    r"(?:(?:题目|第)\s*)?(?P<plain>\d{1,3})\s*[.．、:：]"
    r"|[（(]\s*(?P<paren>\d{1,3})\s*[）)]"
    r"|(?:题目\s*|第\s*)?(?P<label>\d{1,3})\s*题(?=[\s:：.．、]|$)"
    r")\s*"
)
_DECIMAL = re.compile(r"^\s*(?:题目\s*)?\d{1,3}\s*[.．]\d")
_SOURCE_SEGMENT = re.compile(r"^[（(]?\s*(\d{1,3})\s*[）)]?(?:\s*[（(].*)?$")
_SOURCE_LABEL = re.compile(r"第\s*(\d{1,3})\s*题")


def _source_numbers(source: str) -> set[int]:
    numbers: set[int] = set()
    text = str(source or "")
    for segment in text.split("·"):
        match = _SOURCE_SEGMENT.match(segment.strip())
        if match:
            numbers.add(int(match.group(1)))
    numbers.update(int(value) for value in _SOURCE_LABEL.findall(text))
    return numbers


def strip_leading_question_number(content: str, source: str = "") -> str:
    if not isinstance(content, str) or not content:
        return content
    if _DECIMAL.match(content):
        return content
    match = _LEADING_NUMBER.match(content)
    if not match:
        return content
    rest = content[match.end():]
    if not rest.strip():
        return content
    number = int(match.group("plain") or match.group("paren") or match.group("label"))
    declared = _source_numbers(source)
    if declared:
        return rest if number in declared else content
    # A parenthesized leading number without source evidence is usually a
    # sub-question label, not the paper's question number.
    return content if match.group("paren") else rest
