import hashlib
import sqlite3
from pathlib import Path

from scripts.export_markdown import export

SCHEMA = """
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    question_type TEXT,
    category_compulsory TEXT,
    category_chapter TEXT,
    category_knowledge TEXT,
    difficulty TEXT,
    source TEXT,
    answer_markdown TEXT,
    review TEXT,
    image_paths TEXT,
    tags TEXT,
    display_order INTEGER,
    created_at TEXT,
    image_layouts TEXT,
    figure_align TEXT,
    figure_align_custom INTEGER,
    figure_size TEXT,
    usage_count INTEGER
)
"""


def _make_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO questions VALUES (1, ?, 'single_choice', '高中', '必修', '集合',"
        " 'normal', '某市2024届一模 第1题', 'B', '', '[]', '函数,最值', NULL,"
        " '2026-11-01T00:00:00', '{}', 'right', 0, 'auto', 0)",
        ("已知集合 $A=\\{1,2\\}$，则 $A\\cup A=$\n\\begin{choices}\n"
         "\\item $\\{1\\}$\n\\item $\\{1,2\\}$\n\\end{choices}",),
    )
    connection.execute(
        "INSERT INTO questions VALUES (2, ?, 'detailed_answer', '', '', '', 'normal', '',"
        " '配方得最小值为 $-1$。', '注意定义域。', '[]', '', NULL,"
        " '2026-11-02T00:00:00', '{}', 'right', 0, 'auto', 0)",
        ("已知函数 $f(x)=x^2-2x$，求最小值。",),
    )
    connection.commit()
    return connection


def test_export_creates_one_markdown_file_per_question(tmp_path):
    db_path = tmp_path / "bank.db"
    connection = _make_db(db_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    output = tmp_path / "md"
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    assert export(output, db_path=db_path, uploads_dir=uploads) == 2

    first = (output / "q00001.md").read_text(encoding="utf-8")
    assert "id: 1" in first
    assert "seq_num: 1" in first
    assert "question_type: \"single_choice\"" in first
    assert "已知集合 $A=\\{1,2\\}$" in first
    assert "## 答案与解析" in first
    assert "\nB\n" in first
    second = (output / "q00002.md").read_text(encoding="utf-8")
    assert "配方得最小值为 $-1$。" in second
    assert "## 教师评析" in second
    assert "注意定义域。" in second
    index = (output / "index.md").read_text(encoding="utf-8")
    assert "共 2 题" in index
    assert "[q00001.md](q00001.md)" in index
    # The database is opened read-only and never migrated or rewritten.
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before

    # Re-export is idempotent and removes files for questions deleted since.
    connection.execute("DELETE FROM questions WHERE id=2")
    connection.commit()
    connection.close()
    assert export(output, db_path=db_path, uploads_dir=uploads) == 1
    assert not (output / "q00002.md").exists()
    assert "共 1 题" in (output / "index.md").read_text(encoding="utf-8")
