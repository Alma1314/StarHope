"""Conservative bare-option repair for imported choice questions.

Only a proven complete A/B/C/D group at the end of a question is wrapped in
``\\begin{choices}``. Anything ambiguous, protected, or already structured is
left byte-for-byte unchanged.
"""
from __future__ import annotations

import re

CHOICE_QUESTION_TYPES = frozenset({"choice", "single_choice", "multi_choice"})

_LABEL = re.compile(
    r"(?m)(?:^[ \t]*|(?<=[ \t]))[（(]?([A-D])[)）.．、][ \t]*"
)
_TIKZ = re.compile(r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}")


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    from .content_locks import _formulas
    from .question_assets import markdown_literal_ranges

    ranges = [*markdown_literal_ranges(text), *[(item.start, item.end) for item in _formulas(text)]]
    for opening, closing in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ('"', '"')):
        ranges.extend(match.span() for match in re.finditer(
            re.escape(opening) + r"[\s\S]*?" + re.escape(closing), text,
        ))
    ranges.extend(match.span() for match in _TIKZ.finditer(text))
    return ranges


def _inside(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


def wrap_bare_choices(content: str) -> tuple[str, bool]:
    """Return ``(content, converted)``; never guesses a partial option group."""
    if not isinstance(content, str) or not content.strip():
        return content, False
    if "\\begin{choices}" in content:
        return content, False
    protected = _protected_ranges(content)
    labels = [match for match in _LABEL.finditer(content) if not _inside(match.start(1), protected)]
    if len(labels) < 4:
        return content, False
    group = labels[-4:]
    if [match.group(1) for match in group] != list("ABCD"):
        return content, False
    first = group[0]
    stem = content[:first.start()]
    if not stem.strip():
        return content, False
    options = []
    for index, match in enumerate(group):
        end = group[index + 1].start() if index < 3 else len(content)
        option = content[match.end():end].strip()
        if not option:
            return content, False
        options.append(option)
    # A blank-line paragraph after the final option is a separate block, not
    # part of option D; refusing keeps trailing prose out of the environment.
    if re.search(r"\n[ \t]*\n\s*\S", content[group[-1].end():]):
        return content, False

    body = "\n".join(f"\\item {option}" for option in options)
    rebuilt = (
        stem.rstrip()
        + "\n\n\\begin{choices}\n"
        + body
        + "\n\\end{choices}"
    )
    return rebuilt, True


def normalize_question_choice_format(question) -> bool:
    """Format one imported card in place; only choice question types."""
    if not isinstance(question, dict):
        return False
    if question.get("question_type") not in CHOICE_QUESTION_TYPES:
        return False
    content = question.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    rebuilt, converted = wrap_bare_choices(content)
    if converted:
        question["content"] = rebuilt
    return converted
