"""The source question number belongs to metadata, not to the saved stem."""

import shutil
import subprocess
from pathlib import Path

import pytest

from starhope.question_stem import strip_leading_question_number

ROOT = Path(__file__).resolve().parents[1]


def test_strips_bare_paper_numbers():
    assert strip_leading_question_number("16. 已知点 $A,B$。") == "已知点 $A,B$。"
    assert strip_leading_question_number("1.（2018 广东，5分）直线 $l$。") == "（2018 广东，5分）直线 $l$。"
    assert strip_leading_question_number("12、已知 $x=1$。") == "已知 $x=1$。"
    assert strip_leading_question_number("第3题 已知 $x=1$。") == "已知 $x=1$。"
    assert strip_leading_question_number("题目 1：已知 $x$。") == "已知 $x$。"


def test_parenthesized_number_is_a_subquestion_without_source_evidence():
    assert strip_leading_question_number("(1) 求 $x$ 的值。") == "(1) 求 $x$ 的值。"
    assert strip_leading_question_number("（2）求证：$x>0$。") == "（2）求证：$x>0$。"


def test_source_number_decides_paper_number_vs_subquestion():
    source = "解析几何 · 第 38 课 直线方程 · 随堂练 38 Ⅱ · 16 · PDF第19-20页"
    assert strip_leading_question_number("(16) 已知 $x$。", source) == "已知 $x$。"
    assert strip_leading_question_number("16. 已知 $x$。", source) == "已知 $x$。"
    # 卷子题号不同：保留，避免误删小问号
    assert strip_leading_question_number("(1) 求 $x$。", source) == "(1) 求 $x$。"
    assert strip_leading_question_number("15. 已知 $x$。", source) == "15. 已知 $x$。"
    # 书籍来源的 "(2)(Ⅰ)" 形式仍能识别为原题号
    book_source = "椭圆 · 普查讲 40 Ⅰ · (2)(Ⅰ) · PDF第45-45页"
    assert strip_leading_question_number("(2) 已知 $x$。", book_source) == "已知 $x$。"


def test_preserves_decimals_and_plain_stems():
    assert strip_leading_question_number("1.5 万元是多少元？") == "1.5 万元是多少元？"
    assert strip_leading_question_number("已知 $x=1$。") == "已知 $x=1$。"
    assert strip_leading_question_number("12.") == "12."
    assert strip_leading_question_number("") == ""


@pytest.fixture
def headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def test_ocr_cleaner_keeps_subquestion_numbers():
    node = shutil.which("node")
    assert node, "Node is required to execute frontend regression checks"
    source = (ROOT / "static/js/ocr.js").read_text(encoding="utf-8")
    start = source.index("function cleanMathOcrText(")
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = source[start:index + 1]
                break
    else:
        raise AssertionError("unterminated cleanMathOcrText")
    script = body + r"""
const assert = require('assert');
assert.strictEqual(cleanMathOcrText('（1）求 $x$ 的值'), '（1）求 $x$ 的值');
assert.strictEqual(cleanMathOcrText('(2) 求证 $x>0$'), '(2) 求证 $x>0$');
assert.strictEqual(cleanMathOcrText('12. 已知 $x=1$'), '已知 $x=1$');
assert.strictEqual(cleanMathOcrText('第3题 已知 $x=1$'), '已知 $x=1$');
"""
    result = subprocess.run(
        [node, "-"], input=script, encoding="utf-8", cwd=ROOT,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_create_question_does_not_store_the_leading_number(client, headers):
    response = client.post(
        "/api/questions",
        data={
            "content": "7. (2018 河北模拟, 5 分) 已知 $x=1$，求 $x+1$。",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "answer_markdown": "$2$",
            "image_paths": "[]",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["question"]["content"] == "(2018 河北模拟, 5 分) 已知 $x=1$，求 $x+1$。"
