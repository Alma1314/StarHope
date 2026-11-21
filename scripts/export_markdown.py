"""Export the question bank as one human-readable Markdown file per question.

Read-only: opens the existing SQLite database in ``mode=ro`` through
``starhope.agent_reads.AgentReader`` and never creates, migrates or writes it.
The question text is already Markdown + LaTeX, so the output keeps it verbatim;
only metadata, answer/review sections, image links and an index are added.

Usage:
    python3 -m scripts.export_markdown [--output DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from starhope.agent_reads import AgentReader
from starhope.paths import DATABASE_FILE, PROJECT_ROOT

DEFAULT_OUTPUT = PROJECT_ROOT / "data_backup" / "questions_md"
_QUESTION_FILE = re.compile(r"q\d+\.md")


def _yaml_str(value: str) -> str:
    # JSON strings are valid YAML scalars and escape quotes/backslashes/newlines.
    return json.dumps(str(value), ensure_ascii=False)


def _curriculum_line(item: dict) -> str:
    parts = [
        str(item.get("category_compulsory") or "").strip(),
        str(item.get("category_chapter") or "").strip(),
        str(item.get("category_knowledge") or "").strip(),
    ]
    return " > ".join(part for part in parts if part)


def _frontmatter(item: dict) -> list[str]:
    lines = ["---"]
    lines.append(f"id: {int(item['id'])}")
    if item.get("seq_num") is not None:
        lines.append(f"seq_num: {int(item['seq_num'])}")
    for key in ("question_type", "difficulty", "created_at"):
        value = str(item.get(key) or "").strip()
        if value:
            lines.append(f"{key}: {_yaml_str(value)}")
    lines.append(f"version: {_yaml_str(item.get('active_version_code') or '')}")
    curriculum = _curriculum_line(item)
    if curriculum:
        lines.append(f"curriculum: {_yaml_str(curriculum)}")
    for key in ("source", "tags"):
        value = str(item.get(key) or "").strip()
        if value:
            lines.append(f"{key}: {_yaml_str(value)}")
    points = item.get("knowledge_points") or []
    if points:
        lines.append("knowledge_points:")
        for point in points:
            lines.append(f"  - code: {_yaml_str(point.get('code') or '')}")
            lines.append(f"    name: {_yaml_str(point.get('name') or '')}")
            lines.append(f"    role: {_yaml_str(point.get('role') or 'main')}")
    images = [asset.get("url") for asset in (item.get("assets") or []) if asset.get("url")]
    if images:
        lines.append("images:")
        lines.extend(f"  - {_yaml_str(url)}" for url in images)
    lines.append("---")
    return lines


def _image_links(item: dict, output_dir: Path) -> list[str]:
    links = []
    for asset in item.get("assets") or []:
        url = str(asset.get("url") or "").lstrip("/")
        if not url.startswith("static/"):
            continue
        # Link relative to the Markdown file so the mirror works from any root.
        relative = os.path.relpath(PROJECT_ROOT / url, output_dir).replace(os.sep, "/")
        name = Path(url).name
        links.append(f"- ![{name}](<{relative}>)")
    return links


def render_question(item: dict, output_dir: Path) -> str:
    lines = _frontmatter(item)
    lines.append("")
    lines.append(str(item.get("content") or "").rstrip())
    answer = str(item.get("answer_markdown") or "")
    if answer.strip():
        lines.extend(["", "---", "", "## 答案与解析", "", answer.rstrip()])
    review = str(item.get("review") or "")
    if review.strip():
        lines.extend(["", "## 教师评析", "", review.rstrip()])
    links = _image_links(item, output_dir)
    if links:
        lines.extend(["", "## 附件图片", "", *links])
    return "\n".join(lines) + "\n"


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_index(items: list[dict]) -> str:
    lines = [
        "# 题库 Markdown 索引",
        "",
        f"共 {len(items)} 题。本目录由 `python3 -m scripts.export_markdown` 生成，"
        "数据库仍是唯一数据来源；在此编辑不会回写题库。",
        "",
        "| 文件 | # | 题型 | 教材定位 | 难度 | 来源 |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        filename = f"q{int(item['id']):05d}.md"
        seq = item.get("seq_num")
        lines.append(
            f"| [{filename}]({filename}) | {_cell(seq)} | {_cell(item.get('question_type'))} "
            f"| {_cell(_curriculum_line(item))} | {_cell(item.get('difficulty'))} "
            f"| {_cell(item.get('source'))} |"
        )
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def export(output_dir: Path = DEFAULT_OUTPUT, *, db_path: Path = DATABASE_FILE,
           uploads_dir: Path | None = None, active_version_code: str | None = None) -> int:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    options = {"db_path": db_path, "include_local_paths": False,
               "active_version_code": active_version_code}
    if uploads_dir is not None:
        options["uploads_dir"] = uploads_dir
    items: list[dict] = []
    with AgentReader(**options) as reader:
        page = 1
        while True:
            result = reader.search(page=page, page_size=100, sort="id_asc",
                                   profile="teaching", include_answers=True)
            items.extend(result["items"])
            if result.get("next_page") is None:
                break
            page = result["next_page"]
    expected = set()
    for item in items:
        filename = f"q{int(item['id']):05d}.md"
        expected.add(filename)
        _write_if_changed(output / filename, render_question(item, output))
    _write_if_changed(output / "index.md", render_index(items))
    # Remove files this tool previously generated for questions now deleted.
    for existing in output.iterdir():
        if existing.is_file() and _QUESTION_FILE.fullmatch(existing.name) and existing.name not in expected:
            existing.unlink()
    return len(items)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把题库导出为每题一个 Markdown 文件（只读）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录")
    args = parser.parse_args(argv)
    try:
        count = export(args.output)
    except FileNotFoundError:
        print("未找到题库数据库，未生成任何文件。", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"导出失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"已导出 {count} 道题到 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
