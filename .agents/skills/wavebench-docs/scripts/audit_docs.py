#!/usr/bin/env python3
"""Audit deterministic documentation integrity without external dependencies.

The checker deliberately leaves information architecture, page type, audience,
and migration decisions to a human or agent review.  Errors fail the command;
warnings become failures only with ``--strict``.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
import tomllib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


DOCUMENT_ROOTS = ("docs", "plans", ".agents/skills")
ROOT_DOCUMENTS = ("README.md", "CHANGELOG.md", "SKILL.md")
EXCLUDED_PREFIXES = ("tool-of-rei/",)
ENTRY_NAMES = {"README.md", "CHANGELOG.md", "SKILL.md"}
LONG_PAGE_LINES = 600

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HTML_ID_RE = re.compile(r"<(?:a\s+name|[^>]+\sid)=[\"']([^\"']+)[\"']", re.IGNORECASE)
LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
SEMVER_RE = re.compile(r"(?<![\w.])v?(\d+\.\d+\.\d+)(?![\w.])")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
VISA_RE = re.compile(r"\b(?:TCPIP|USB|GPIB|ASRL)\d*::[^\s`'\"]+", re.IGNORECASE)
SERIAL_RE = re.compile(
    r"(?i)\b(?:serial(?:\s+number)?|s/n|sn)\s*[:=#]\s*([a-z0-9][a-z0-9._-]{4,})"
)
HOME_PATH_RE = re.compile(r"(?:/home/[^\s)`]+|/Users/[^\s)`]+|[A-Za-z]:[\\/]Users[\\/][^\s)`]+)")

TEST_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


@dataclass(frozen=True)
class Document:
    path: Path
    relative: Path
    canonical: Path
    lines: tuple[str, ...]
    visible_lines: tuple[tuple[int, str], ...]
    headings: tuple[Heading, ...]
    anchors: frozenset[str]


@dataclass(frozen=True)
class Finding:
    level: str
    path: str
    line: int
    message: str


def repository_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def discover_markdown(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for name in ROOT_DOCUMENTS:
        candidate = root / name
        if candidate.is_file():
            paths.add(candidate)
    for directory in DOCUMENT_ROOTS:
        base = root / directory
        if base.is_dir():
            paths.update(path for path in base.rglob("*.md") if path.is_file())
    return sorted(
        path
        for path in paths
        if not path.relative_to(root).as_posix().startswith(EXCLUDED_PREFIXES)
    )


def visible_markdown_lines(lines: tuple[str, ...]) -> tuple[tuple[int, str], ...]:
    visible: list[tuple[int, str]] = []
    fence_char = ""
    fence_length = 0
    for number, line in enumerate(lines, 1):
        match = FENCE_RE.match(line)
        if fence_char:
            if match and match.group(1)[0] == fence_char and len(match.group(1)) >= fence_length:
                fence_char = ""
                fence_length = 0
            continue
        if match:
            fence_char = match.group(1)[0]
            fence_length = len(match.group(1))
            continue
        visible.append((number, line))
    return tuple(visible)


def heading_slug(title: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", title.casefold())
    text = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", text)
    slug: list[str] = []
    for character in text:
        if character.isspace():
            slug.append("-")
        elif character in {"-", "_"} or not unicodedata.category(character).startswith(("P", "S")):
            slug.append(character)
    return re.sub(r"-+", "-", "".join(slug)).strip("-")


def markdown_structure(
    visible_lines: tuple[tuple[int, str], ...],
) -> tuple[tuple[Heading, ...], frozenset[str]]:
    headings: list[Heading] = []
    anchors: set[str] = set()
    seen_slugs: defaultdict[str, int] = defaultdict(int)
    for number, line in visible_lines:
        match = HEADING_RE.match(line)
        if match:
            title = match.group(2).strip()
            headings.append(Heading(len(match.group(1)), title, number))
            base = heading_slug(title)
            suffix = seen_slugs[base]
            anchors.add(base if suffix == 0 else f"{base}-{suffix}")
            seen_slugs[base] += 1
        anchors.update(unquote(value) for value in HTML_ID_RE.findall(line))
    return tuple(headings), frozenset(anchors)


def load_document(path: Path, root: Path) -> Document:
    lines = tuple(path.read_text(encoding="utf-8").splitlines())
    visible = visible_markdown_lines(lines)
    headings, anchors = markdown_structure(visible)
    return Document(
        path=path,
        relative=path.relative_to(root),
        canonical=path.resolve(),
        lines=lines,
        visible_lines=visible,
        headings=headings,
        anchors=anchors,
    )


def markdown_links(document: Document) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    for number, line in document.visible_lines:
        links.extend((number, match.group(1).strip("<>")) for match in LINK_RE.finditer(line))
    return links


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _target_document(path: Path, documents: dict[Path, Document], root: Path) -> Document | None:
    canonical = path.resolve()
    if canonical in documents:
        return documents[canonical]
    if path.is_file() and path.suffix.lower() == ".md" and _inside(canonical, root):
        return load_document(path, root)
    return None


def check_links(
    documents: list[Document], root: Path
) -> tuple[list[Finding], dict[Path, set[Path]]]:
    findings: list[Finding] = []
    inbound: dict[Path, set[Path]] = defaultdict(set)
    by_canonical = {document.canonical: document for document in documents}

    for document in documents:
        base = document.canonical.parent if document.path.is_symlink() else document.path.parent
        for line, destination in markdown_links(document):
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue
            relative_target = unquote(parsed.path)
            target = document.path if not relative_target else base / relative_target
            target = target.resolve()
            if not _inside(target, root):
                findings.append(
                    Finding("error", document.relative.as_posix(), line, f"relative link escapes repository: {destination}")
                )
                continue
            if not target.exists():
                findings.append(
                    Finding("error", document.relative.as_posix(), line, f"relative link target does not exist: {destination}")
                )
                continue
            if target.is_file() and target.suffix.lower() == ".md":
                inbound[target].add(document.canonical)
                if parsed.fragment:
                    target_document = _target_document(target, by_canonical, root)
                    anchor = unquote(parsed.fragment).casefold()
                    if target_document and anchor not in target_document.anchors:
                        findings.append(
                            Finding("warning", document.relative.as_posix(), line, f"anchor was not found: {destination}")
                        )
    return findings, inbound


def check_structure(
    documents: list[Document], inbound: dict[Path, set[Path]], max_lines: int
) -> list[Finding]:
    findings: list[Finding] = []
    canonical_documents: dict[Path, Document] = {}
    for document in documents:
        canonical_documents.setdefault(document.canonical, document)

    titles: dict[str, list[Document]] = defaultdict(list)
    for document in canonical_documents.values():
        h1 = [heading for heading in document.headings if heading.level == 1]
        if not h1:
            findings.append(Finding("warning", document.relative.as_posix(), 1, "page has no ATX H1"))
        elif len(h1) > 1:
            findings.append(Finding("warning", document.relative.as_posix(), h1[1].line, "page has more than one H1"))
        else:
            titles[h1[0].title.casefold()].append(document)
        if len(document.lines) > max_lines and document.relative.parts[:2] != ("docs", "archive"):
            findings.append(
                Finding("warning", document.relative.as_posix(), 1, f"long page: {len(document.lines)} lines (threshold {max_lines})")
            )

        is_entry = document.relative.name in ENTRY_NAMES or (
            len(document.relative.parts) >= 2
            and document.relative.parts[-1] == "SKILL.md"
            and "skills" in document.relative.parts
        )
        is_compatibility_entry = any(
            heading.level == 1 and "旧入口" in heading.title for heading in document.headings
        )
        if not is_entry and not is_compatibility_entry and not inbound.get(document.canonical):
            findings.append(Finding("warning", document.relative.as_posix(), 1, "orphan Markdown page: no inbound Markdown link"))

    for duplicate_title, matches in titles.items():
        if len(matches) < 2:
            continue
        paths = ", ".join(item.relative.as_posix() for item in matches)
        for document in matches:
            h1 = next(heading for heading in document.headings if heading.level == 1)
            findings.append(
                Finding("warning", document.relative.as_posix(), h1.line, f"duplicate H1 {duplicate_title!r}: {paths}")
            )
    return findings


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check_version_drift(documents: list[Document], current_version: str) -> list[Finding]:
    findings: list[Finding] = []
    for document in documents:
        relative = document.relative.as_posix()
        if relative == "CHANGELOG.md" or relative.startswith("docs/project/rfcs/"):
            continue
        for number, line in document.visible_lines:
            lowered = line.casefold()
            current_claim = (
                "当前开发线" in line
                or "current development line" in lowered
                or bool(re.search(r"(?:Core|核心)\s*`?v?\d+\.\d+\.\d+`?\s*开发线", line))
            )
            if not current_claim or any(marker in line for marker in ("静态下限", "只表示", "核心基线")):
                continue
            versions = set(SEMVER_RE.findall(line))
            if versions and current_version not in versions:
                findings.append(
                    Finding(
                        "warning",
                        relative,
                        number,
                        f"current-development version may have drifted: {sorted(versions)} != {current_version}",
                    )
                )
    return findings


def _allowed_documentation_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return address.is_loopback or address.is_unspecified or any(address in network for network in TEST_NETWORKS)


def check_sensitive_resources(documents: list[Document]) -> list[Finding]:
    findings: list[Finding] = []
    for document in documents:
        relative = document.relative.as_posix()
        for number, line in enumerate(document.lines, 1):
            for match in IPV4_RE.finditer(line):
                if not _allowed_documentation_ip(match.group(0)):
                    findings.append(
                        Finding("warning", relative, number, f"review possible real network address: {match.group(0)}")
                    )
            for match in VISA_RE.finditer(line):
                resource = match.group(0)
                if "<" in resource or "..." in resource:
                    continue
                ips = IPV4_RE.findall(resource)
                if not ips or any(not _allowed_documentation_ip(value) for value in ips):
                    findings.append(Finding("warning", relative, number, f"review possible real VISA resource: {resource}"))
            if SERIAL_RE.search(line):
                findings.append(Finding("warning", relative, number, "review possible device serial number"))
            if HOME_PATH_RE.search(line):
                findings.append(Finding("warning", relative, number, "review machine-specific absolute path"))
    return findings


def audit(root: Path, max_lines: int) -> tuple[list[Document], list[Finding]]:
    documents = [load_document(path, root) for path in discover_markdown(root)]
    link_findings, inbound = check_links(documents, root)
    findings = [
        *link_findings,
        *check_structure(documents, inbound, max_lines),
        *check_version_drift(documents, project_version(root)),
        *check_sensitive_resources(documents),
    ]
    findings.sort(key=lambda item: (item.path.casefold(), item.line, item.level, item.message))
    return documents, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="report findings only for these repository-relative files or directories",
    )
    parser.add_argument("--root", type=Path, help="repository root; defaults to the current Git worktree")
    parser.add_argument("--max-lines", type=int, default=LONG_PAGE_LINES, help="long-page warning threshold")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--quiet-warnings", action="store_true", help="hide warning details but retain their count")
    args = parser.parse_args()

    try:
        root = args.root.resolve() if args.root else repository_root(Path.cwd())
        documents, findings = audit(root, args.max_lines)
    except (OSError, subprocess.CalledProcessError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f"ERROR: unable to audit documentation: {error}", file=sys.stderr)
        return 2

    selected: set[str] = set()
    for requested in args.paths:
        candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        if not _inside(candidate, root):
            print(f"ERROR: requested path escapes repository: {requested}", file=sys.stderr)
            return 2
        relative = candidate.relative_to(root)
        if candidate.is_dir():
            prefix = relative.as_posix().rstrip("/") + "/"
            selected.update(
                document.relative.as_posix()
                for document in documents
                if document.relative.as_posix().startswith(prefix)
            )
        else:
            selected.add(relative.as_posix())
    if selected:
        findings = [finding for finding in findings if finding.path in selected]

    for finding in findings:
        if args.quiet_warnings and finding.level == "warning":
            continue
        print(f"{finding.level.upper()}: {finding.path}:{finding.line}: {finding.message}")
    errors = sum(item.level == "error" for item in findings)
    warnings = sum(item.level == "warning" for item in findings)
    canonical = len({document.canonical for document in documents})
    print(
        f"SUMMARY: {len(documents)} Markdown paths ({canonical} canonical), "
        f"{errors} errors, {warnings} warnings"
        + (f", scoped to {len(selected)} requested paths" if selected else "")
    )
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
