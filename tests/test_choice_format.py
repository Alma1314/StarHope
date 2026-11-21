"""Bare imported option labels are wrapped only when the whole group is proven."""
from starhope.choice_format import normalize_question_choice_format, wrap_bare_choices


def test_line_start_bare_options_become_choices_environment():
    source = (
        "已知 $b>a>c$ 与 $b>c>a$ 的大小关系，则\n"
        "A. $b>a>c$\n"
        "B. $b>c>a$\n"
        "C. $a>b>c$\n"
        "D. $a>c>b$"
    )
    rebuilt, converted = wrap_bare_choices(source)
    assert converted
    assert rebuilt == (
        "已知 $b>a>c$ 与 $b>c>a$ 的大小关系，则\n\n"
        "\\begin{choices}\n"
        "\\item $b>a>c$\n"
        "\\item $b>c>a$\n"
        "\\item $a>b>c$\n"
        "\\item $a>c>b$\n"
        "\\end{choices}"
    )


def test_inline_and_parenthesized_labels_are_supported():
    source = "下列正确的是 (A) $1$ (B) $2$ (C) $3$ (D) $4$"
    rebuilt, converted = wrap_bare_choices(source)
    assert converted
    assert rebuilt.startswith("下列正确的是")
    assert rebuilt.count("\\item ") == 4
    assert "(A)" not in rebuilt and "(B)" not in rebuilt


def test_existing_choices_environment_is_untouched():
    source = "题干\n\\begin{choices}\n\\item $1$\n\\item $2$\n\\item $3$\n\\item $4$\n\\end{choices}"
    assert wrap_bare_choices(source) == (source, False)


def test_partial_or_trailing_text_is_not_guessed():
    assert wrap_bare_choices("题干\nA. 1\nB. 2\nC. 3") == ("题干\nA. 1\nB. 2\nC. 3", False)
    trailing = "题干 A. 1 B. 2 C. 3 D. 4\n\n故选A"
    assert wrap_bare_choices(trailing) == (trailing, False)


def test_labels_inside_math_code_or_tikz_are_not_options():
    math = "设 $A. B. C. D.$ 为集合，求它的子集个数"
    assert wrap_bare_choices(math) == (math, False)
    code = "示例：\n```\nA. 1\nB. 2\nC. 3\nD. 4\n```\n求输出"
    assert wrap_bare_choices(code) == (code, False)
    tikz = "图：\n\\begin{tikzpicture}\n\\node {A. 1 B. 2 C. 3 D. 4};\n\\end{tikzpicture}\n求值"
    assert wrap_bare_choices(tikz)[1] is False


def test_only_choice_question_types_are_normalized_in_place():
    card = {"question_type": "single_choice", "content": "题干 A. 1 B. 2 C. 3 D. 4"}
    assert normalize_question_choice_format(card)
    assert "\\begin{choices}" in card["content"]
    detail = {"question_type": "detailed_answer", "content": "题干 A. 1 B. 2 C. 3 D. 4"}
    assert not normalize_question_choice_format(detail)
    assert detail["content"] == "题干 A. 1 B. 2 C. 3 D. 4"


def test_prompts_keep_requiring_choices_environment():
    from pathlib import Path
    from starhope.book_import import build_book_prompt

    prompt = build_book_prompt("数学", {"question_types": [], "difficulties": []})
    assert "完整choices环境" in prompt and "裸标号" in prompt
    paper = Path("starhope/prompts.py").read_text(encoding="utf-8")
    assert "连续多道选择题" in paper and "残缺choices环境" in paper
