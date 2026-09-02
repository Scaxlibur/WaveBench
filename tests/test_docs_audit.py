from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "wavebench-docs"
    / "scripts"
    / "audit_docs.py"
)
SPEC = importlib.util.spec_from_file_location("wavebench_docs_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def test_markdown_parser_ignores_fences_and_builds_duplicate_anchors():
    lines = (
        "# 文档 API",
        "[有效](guide.md#运行-check)",
        "```markdown",
        "[忽略](missing.md)",
        "# 也忽略",
        "```",
        "## 运行 `check`",
        "## 运行 `check`",
    )

    visible = AUDIT.visible_markdown_lines(lines)
    headings, anchors = AUDIT.markdown_structure(visible)
    document = AUDIT.Document(
        path=Path("README.md"),
        relative=Path("README.md"),
        canonical=Path("README.md"),
        lines=lines,
        visible_lines=visible,
        headings=headings,
        anchors=anchors,
    )

    assert [heading.title for heading in headings] == ["文档 API", "运行 `check`", "运行 `check`"]
    assert {"文档-api", "运行-check", "运行-check-1"} <= anchors
    assert AUDIT.markdown_links(document) == [(2, "guide.md#运行-check")]


def test_documentation_ip_allowlist_only_accepts_nonidentifying_examples():
    assert AUDIT._allowed_documentation_ip("192.0.2.10")
    assert AUDIT._allowed_documentation_ip("127.0.0.1")
    assert not AUDIT._allowed_documentation_ip("192.168.1.42")


def test_link_check_distinguishes_missing_targets_and_anchors(tmp_path):
    readme = tmp_path / "README.md"
    guide = tmp_path / "guide.md"
    readme.write_text(
        "# Index\n[missing](missing.md)\n[bad anchor](guide.md#missing)\n[good](guide.md#section)\n",
        encoding="utf-8",
    )
    guide.write_text("# Guide\n## Section\n", encoding="utf-8")
    documents = [AUDIT.load_document(path, tmp_path) for path in (readme, guide)]

    findings, inbound = AUDIT.check_links(documents, tmp_path)

    assert [(finding.level, finding.line) for finding in findings] == [
        ("error", 2),
        ("warning", 3),
    ]
    assert inbound[guide.resolve()] == {readme.resolve()}


def test_structure_does_not_flag_long_archive_pages(tmp_path):
    archive = tmp_path / "docs" / "archive" / "record.md"
    current = tmp_path / "docs" / "current.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Archive\n\nHistorical detail\n", encoding="utf-8")
    current.write_text("# Current\n\nCurrent detail\n", encoding="utf-8")
    documents = [AUDIT.load_document(path, tmp_path) for path in (archive, current)]

    findings = AUDIT.check_structure(documents, {}, max_lines=1)

    assert not any(
        finding.path == "docs/archive/record.md" and "long page" in finding.message
        for finding in findings
    )
    assert any(
        finding.path == "docs/current.md" and "long page" in finding.message
        for finding in findings
    )
