"""Conservative repairs for AI-generated exam-zh fill-in markup."""

from __future__ import annotations

import re
from bisect import bisect_right

from starhope.math_markdown import _is_escaped
from starhope.question_assets import _mask_literals, markdown_literal_ranges


_COMMAND = re.compile(r"\\(fillin|texttt|includegraphics)(?![A-Za-z])\*?")


def _group_end(value: str, start: int) -> int | None:
    """Consume a whole TeX argument, including nested braces in options."""
    stack = ["]" if value[start] == "[" else "}"]
    for index in range(start + 1, len(value)):
        if _is_escaped(value, index):
            continue
        char = value[index]
        if char == "{":
            stack.append("}")
        elif char == "[" and stack[-1] == "]":
            stack.append("]")
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        elif char == "}" or (char == "]" and stack[-1] == "]"):
            return None
    return None


def normalize_fillin_math_wrappers(value: str) -> str:
    r"""Unwrap only a complete math span consisting of fill-in macros.

    Regex locates commands; balanced arguments and complete math boundaries
    prevent a broad substitution from consuming formulas, answers or source
    examples. Mixed math such as $x=\fillin$ is deliberately preserved.
    """
    if not isinstance(value, str) or not value or r"\fillin" not in value:
        return value or ""

    ranges = markdown_literal_ranges(value)
    masked = _mask_literals(value, tex_comments=False)
    # Reuse the existing literal scanner without interpreting prose '%' as
    # TeX comments. Preserve HTML attributes and read-only formula-lock nodes.
    ranges.extend(match.span() for match in re.finditer(
        r"<starhope-math\b[^>]*>[\s\S]*?(?:</starhope-math\s*>|\Z)|"
        r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*|/?)>", value, re.IGNORECASE,
    ))
    start = None
    for index, (original, replacement) in enumerate(zip(value, masked)):
        if original != replacement and start is None:
            start = index
        elif original == replacement and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(value)))
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    ranges = merged
    starts = [left for left, _ in ranges]

    def covered(position: int) -> bool:
        index = bisect_right(starts, position) - 1
        return index >= 0 and position < merged[index][1]

    # Protect whole argument lists before scanning dollars: an answer such as
    # \fillin[width=2cm][$x^2$] must not supply the outer closing delimiter.
    macros = []
    cursor = 0
    for match in _COMMAND.finditer(value):
        if match.start() < cursor or _is_escaped(value, match.start()) or covered(match.start()):
            continue
        end = match.end()
        argument = end
        count, valid = 0, True
        while True:
            while argument < len(value) and value[argument].isspace():
                argument += 1
            if value[argument:argument + 1] not in ("[", "{"):
                break
            # Native exam-zh uses s O{} o: at most two optional arguments,
            # never a mandatory {...} group. Moving a following math group
            # (e.g. {x_1}) into prose would invalidate otherwise legal TeX.
            if match.group(1) == "fillin" and (value[argument] != "[" or count == 2):
                break
            stop = _group_end(value, argument)
            if stop is None:
                # A malformed command makes the remaining boundaries unsafe.
                ranges.append((match.start(), len(value)))
                end = len(value)
                valid = False
                break
            end = argument = stop
            count += 1
        cursor = end
        if match.group(1) == "fillin":
            if valid:
                macros.append((match.start(), end))
        else:
            ranges.append((match.start(), end))

    spans = [(start, end, False) for start, end in ranges]
    spans.extend((start, end, True) for start, end in macros)
    prefix = "STARHOPEFILLINWRAP"
    while prefix in value:
        prefix += "X"
    pieces, originals, cursor = [], {}, 0
    for start, end, is_macro in sorted(spans, key=lambda row: (row[0], -row[1])):
        if start < cursor:
            continue
        token = f"{prefix}{'F' if is_macro else 'L'}{len(originals)}END"
        pieces.extend((value[cursor:start], token))
        originals[token] = value[start:end]
        cursor = end
    pieces.append(value[cursor:])
    protected = "".join(pieces)
    only_fillins = re.compile(r"\s*(?:" + re.escape(prefix) + r"F[0-9]+END\s*)+")
    token_pattern = re.compile(re.escape(prefix) + r"[FL][0-9]+END")

    whitespace = re.compile(r"\s*")

    def next_source_char(position: int) -> str:
        token = token_pattern.match(protected, position)
        return originals[token.group()][:1] if token else protected[position:position + 1]

    def unwrap(source: str, finish: int) -> str:
        size = 2 if source.startswith(("$$", r"\(", r"\[")) else 1
        inner = source[size:-size]
        if not only_fillins.fullmatch(inner):
            return source
        # Removing '$' must not create \fillincm or turn a visible [2] / '*'
        # into a new optional macro argument. Keep ambiguous source intact.
        following = next_source_char(whitespace.match(protected, finish).end())
        if following in ("[", "*", "%"):
            return source
        if not inner[-1:].isspace() and next_source_char(finish).isalpha():
            last = token_pattern.findall(inner)[-1]
            if re.fullmatch(r"\\fillin\*?", originals[last]):
                return source
        return inner

    # Scan whole delimiters before replacing. A single '$' cannot close on
    # half of '$$'; incomplete/mismatched shells remain unchanged.
    pieces, cursor, copied = [], 0, 0
    openings = re.compile(r"\$+|\\[([]")
    while match := openings.search(protected, cursor):
        cursor = match.end()
        if _is_escaped(protected, match.start()):
            continue
        opening = match.group()
        if opening.startswith("$") and len(opening) > 2:
            break
        closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
        endings = re.compile(r"\$+" if opening.startswith("$") else re.escape(closing))
        end_match = next((end for end in endings.finditer(protected, cursor)
                          if not _is_escaped(protected, end.start())), None)
        if end_match is None or end_match.group() != closing:
            break
        end = end_match.end()
        pieces.extend((protected[copied:match.start()], unwrap(protected[match.start():end], end)))
        copied = cursor = end
    pieces.append(protected[copied:])
    result = "".join(pieces)
    return token_pattern.sub(lambda match: originals[match.group()], result)


def normalize_fillin_macro(text: str) -> str:
    """Normalize proven blank lines; preserve literal TeX, subscripts and macros.

    Keep the legacy callable available to internal tools; new math-shell
    handling lives in the shared backend module and never migrates old rows.
    """
    if not text or not isinstance(text, str):
        return text or ""
    spans = []

    def escaped(position):
        previous = position - 1
        while previous >= 0 and text[previous] == "\\":
            previous -= 1
        return (position - previous - 1) % 2 == 1

    def covered(position):
        return any(start <= position < end for start, end, _ in spans)

    def group_end(position):
        stack = ["]" if text[position] == "[" else "}"]
        for index in range(position + 1, len(text)):
            if escaped(index):
                continue
            char = text[index]
            if char in "[{":
                stack.append("]" if char == "[" else "}")
            elif char in "]}":
                if char != stack[-1]:
                    return len(text)
                stack.pop()
                if not stack:
                    return index + 1
        return len(text)

    # Code/HTML literals, links, URLs and asset paths cannot supply blank lines.
    literal = re.compile(
        r"<(?P<literal_tag>code|pre|starhope-math)\b[^>]*>[\s\S]*?</(?P=literal_tag)\s*>|<!--[\s\S]*?(?:-->|\Z)|"
        r"!?\[[^\]\n]*\]\([^\n)]*\)|(?:https?|ftp)://[^\s<>\"']+|"
        r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*|/?)>", re.IGNORECASE)
    spans.extend((m.start(), m.end(), None) for m in literal.finditer(text))
    cursor = 0
    while match := re.search(r"`+|(?m:^[ \t]{0,3}~{3,})", text[cursor:]):
        start, end = cursor + match.start(), cursor + match.end()
        token = match.group().lstrip(" \t")
        if covered(start) or escaped(start):
            cursor = end
            continue
        line_start = text.rfind("\n", 0, start) + 1
        fence = len(token) >= 3 and not text[line_start:start].strip()
        if fence:
            newline = text.find("\n", end)
            body = newline + 1 if newline >= 0 else len(text)
            closing = re.search(r"(?m)^[ \t]{0,3}" + re.escape(token[0]) + "{" + str(len(token)) + r",}[ \t]*(?:\n|\Z)", text[body:])
            end = body + closing.end() if closing else len(text)
        else:
            closing = next((m for m in re.finditer(r"`+", text[end:]) if len(m.group()) == len(token)), None)
            end = end + closing.end() if closing else len(text)
        spans.append((start, end, None))
        cursor = end
    # Conservatively retain Markdown indented code. Lists' own content stays
    # active; four additional columns establish an indented literal block.
    offset, previous_blank, code_start, list_indent = 0, True, None, None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        leading = re.match(r"[ \t]*", body).group()
        indent = len(leading.expandtabs(4))
        marker = re.match(r"(?:[-+*]|[0-9]{1,9}[.)])[ \t]+", body[len(leading):])
        threshold = list_indent + 4 if list_indent is not None else 4
        if not body.strip():
            previous_blank = True
        elif indent >= threshold and (previous_blank or code_start is not None):
            if code_start is None:
                code_start = offset
            previous_blank = False
        else:
            if code_start is not None:
                spans.append((code_start, offset, None))
                code_start = None
            if marker:
                list_indent = indent + len(marker.group().expandtabs(4))
            elif indent < 4:
                list_indent = None
            previous_blank = False
        offset += len(line)
    if code_start is not None:
        spans.append((code_start, len(text), None))

    environments = re.compile(r"\\begin\s*\{(tikzpicture|verbatim\*?|Verbatim\*?|lstlisting|minted)\}")
    for match in environments.finditer(text):
        if covered(match.start()) or escaped(match.start()):
            continue
        closing = re.search(r"\\end\s*\{" + re.escape(match.group(1)) + r"\}", text[match.end():])
        spans.append((match.start(), match.end() + closing.end() if closing else len(text), None))
    commands = re.compile(r"\\(verb|Verb|lstinline|mintinline|detokenize|texttt|url|path|includegraphics|fillin|underline)(?![A-Za-z])\*?")
    for match in commands.finditer(text):
        if covered(match.start()):
            continue
        name, cursor = match.group(1), match.end()
        while cursor < len(text) and text[cursor] in " \t":
            cursor += 1
        if name in {"verb", "Verb", "lstinline", "mintinline"}:
            if cursor < len(text) and text[cursor] == "[":
                cursor = group_end(cursor)
            if name == "mintinline" and text[cursor:cursor + 1] == "{":
                cursor = group_end(cursor)
            while cursor < len(text) and text[cursor] in " \t":
                cursor += 1
            if cursor < len(text) and not text[cursor].isspace():
                if text[cursor] == "{":
                    end = group_end(cursor)
                else:
                    closing = text.find(text[cursor], cursor + 1)
                    end = closing + 1 if closing >= 0 else len(text)
                spans.append((match.start(), end, None))
            continue
        arguments, end = [], match.end()
        while cursor < len(text) and text[cursor] in "[{":
            stop = group_end(cursor)
            arguments.append((cursor, stop))
            end = stop
            cursor = stop
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
        replacement = None
        if name == "underline" and not escaped(match.start()) and len(arguments) == 1:
            left, right = arguments[0]
            if text[left] == "{" and text[right - 1:right] == "}":
                inner = text[left + 1:right - 1]
                blank = re.fullmatch(r"(?:\s|_|\\_|~|\\(?:quad|qquad|strut)|\\hspace\{[0-9.]+(?:cm|mm|pt|em|ex|in)\})*", inner)
                if blank:
                    replacement = r"\fillin"
        # Preserve explicit fillin parameters/braces and nonblank underline.
        spans.append((match.start(), end, replacement))

    # An underscore inside a real subscript group is not an inferred blank.
    stack = []
    for index, char in enumerate(text):
        if char not in "{}" or escaped(index) or covered(index):
            continue
        if char == "{":
            stack.append((index, index > 0 and text[index - 1] == "_" and not escaped(index - 1)))
        elif stack:
            start, is_subscript = stack.pop()
            if is_subscript:
                spans.append((start + 1, index, None))
    spans.extend((start + 1, len(text), None) for start, is_subscript in stack if is_subscript)

    def normalize_segment(segment, base):
        def blank(match):
            start, end = base + match.start(), base + match.end()
            if escaped(start):
                return match.group()
            left = text[start - 1:start]
            right = text[end:end + 1]
            if re.fullmatch(r"[A-Za-z0-9_\u0370-\u03ff]", left) or re.fullmatch(r"[A-Za-z0-9_\u0370-\u03ff]", right):
                return match.group()
            if match.group().startswith("\\") and len(match.group()) < 6:
                # Single/double escaped glyphs need a declared value slot. This
                # covers the real textbook S_n = \_ without guessing literal _.
                before = text[max(0, start - 24):start].rstrip()
                after = text[end:]
                if not re.search(r"(?:=|为|是|等于|填写)\s*$", before) or not re.match(r"\s*(?:\$|\\[)\]]|[。，,；;.!?！？]|\Z)", after):
                    return match.group()
            return r"\fillin"
        return re.sub(r"(?:\\_)+|_{3,}", blank, segment)

    result, cursor = [], 0
    for start, end, replacement in sorted(spans, key=lambda row: (row[0], -row[1])):
        if start < cursor:
            continue
        result.append(normalize_segment(text[cursor:start], cursor))
        result.append(text[start:end] if replacement is None else replacement)
        cursor = end
    result.append(normalize_segment(text[cursor:], cursor))
    from starhope.fillin import normalize_fillin_math_wrappers
    from starhope.fillin_placeholders import normalize_fillin_placeholder_clusters
    return normalize_fillin_placeholder_clusters(normalize_fillin_math_wrappers("".join(result)))
