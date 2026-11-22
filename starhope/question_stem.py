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

from starhope.word_math import iter_word_math_spans, word_literal_ranges

_LEADING_NUMBER = re.compile(
    r"^\s*(?:"
    r"(?:(?:题目|第)\s*)?(?P<plain>\d{1,3})\s*[.．、:：]"
    r"|[（(]\s*(?P<paren>\d{1,3})\s*[）)]"
    r"|(?:\[\s*(?P<bracket>\d{1,3})\s*\]|【\s*(?P<boxed>\d{1,3})\s*】)"
    r"|(?:题目\s*|第\s*)?(?P<label>\d{1,3})\s*题(?=[\s:：.．、]|$)\s*[:：.．、]?"
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
    number = int(next(value for value in match.groupdict().values() if value))
    declared = _source_numbers(source)
    if declared:
        return rest if number in declared else content
    # A parenthesized leading number without source evidence is usually a
    # sub-question label, not the paper's question number.
    return content if match.group("paren") else rest


_SOURCE_LINE = re.compile(r"(?m)^[ \t]*(?:题目来源|试题来源|来源)\s*[:：]\s*([^\n]{1,200})[ \t]*$")
_SOURCE_PREFIX = re.compile(
    r"^\s*(?:【([^【】\n]{1,160})】|（([^（）\n]{1,160})）|\(([^()\n]{1,160})\))\s*"
)
_ANSWER_LINE = re.compile(
    r"(?m)^[ \t]*(?:【\s*(?:参考答案|答案|解析|详解|解答)\s*】\s*[:：]?"
    r"|(?:参考答案|答案|解析|详解|解答)\s*[:：])[ \t]*"
)
_PAREN_ANSWER = re.compile(r"[（(]([^()（）\n]{1,80})[)）][。．]?\s*$")


def split_trailing_parenthesis_answer(content: str, question_type: str) -> tuple[str, str]:
    match = _PAREN_ANSWER.search(content)
    if not match or not content[:match.start()].strip():
        return content, ""
    stem, answer = content[:match.start()].rstrip(), match[1].strip()
    if question_type in {"single_choice", "multi_choice"}:
        valid = (re.fullmatch(r"[A-D](?:\s*[、,，/]?\s*[A-D])*", answer) and
                 (question_type == "multi_choice" or len(answer) == 1) and
                 re.search(r"的是\s*$|[为是选]\s*$|\\end\s*\{choices\*?\}\s*$", stem))
    elif question_type == "fill_in_blank":
        blanks = list(re.finditer(r"\\fillin(?![A-Za-z])", stem))
        valid = (len(blanks) == 1 and blanks[0].end() == len(stem))
    else:
        valid = False
    return (stem, answer) if valid else (content, "")


def clean_question_fields(content: str, answer: str = "", source: str = "", question_type: str = "") -> tuple[str, str, str]:
    """Move explicit peripheral metadata/answers out of a stem without guessing.

    Only boundary source lines/labelled exam prefixes and a trailing explicit
    answer block are eligible. Conflicting answers remain in the stem for review.
    This is for new saves and derived comparison copies, never a bulk migration.
    """
    content, answer, source = str(content or ""), str(answer or ""), str(source or "")

    def protected(text, position):
        return any(start <= position < end for start, end in (
            word_literal_ranges(text) + [(span.start, span.end) for span in iter_word_math_spans(text)]
        ))

    def add_source(value):
        nonlocal source
        value = value.strip()
        if value and value not in source.split(" · ") and value != source.strip():
            source = f"{source.rstrip()} · {value}" if source.strip() else value

    # A source line is metadata only at the document boundary, never in a
    # quoted example, formula, or in the middle of the problem's conditions.
    for match in reversed(list(_SOURCE_LINE.finditer(content))):
        if ((not content[:match.start()].strip() or not content[match.end():].strip())
                and not protected(content, match.start())):
            rest = content[:match.start()] + content[match.end():]
            if rest.strip():
                add_source(match[1])
                content = rest.strip()

    # Strip the number first so a conventional source prefix can be recognized.
    def move_number(text):
        stripped = strip_leading_question_number(text, source)
        if stripped != text and not _source_numbers(source):
            match = _LEADING_NUMBER.match(text)
            number = int(next(value for value in match.groupdict().values() if value))
            add_source(f"第{number}题")
        return stripped

    content = move_number(content)
    prefix = _SOURCE_PREFIX.match(content)
    if prefix:
        label = next(value for value in prefix.groups() if value)
        explicit = re.fullmatch(r"(?:题目来源|试题来源|来源)\s*[:：]\s*(.+)", label)
        exam = (re.search(r"(?:19|20)\d{2}", label) and
                re.search(r"高考|中考|模拟|联考|月考|期中|期末|统考|学考|真题", label) and
                not re.search(r"[$\\=<>]|已知|设|若|当|求|函数|概率|人|增长|减少|总分|平均|满足|条件|参加|录取|为|是|有|无", label) and
                not re.search(r"\d", re.sub(r"(?:19|20)\d{2}|第\s*\d{1,3}\s*题|\d{1,2}\s*分", "", label)))
        if (explicit or exam) and content[prefix.end():].strip():
            add_source(explicit[1] if explicit else label)
            content = move_number(content[prefix.end():])

    for marker in _ANSWER_LINE.finditer(content):
        stem, extracted = content[:marker.start()].rstrip(), content[marker.end():].strip()
        if stem and extracted and not protected(content, marker.start()):
            if not answer.strip() or answer.strip() == extracted:
                return stem, answer if answer.strip() else extracted, source
            break
    stem, extracted = split_trailing_parenthesis_answer(content, question_type)
    if extracted and not protected(content, len(stem)):
        if not answer.strip() or answer.strip() == extracted:
            content, answer = stem, answer if answer.strip() else extracted
    return content, answer, source
