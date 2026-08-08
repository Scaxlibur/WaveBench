#!/usr/bin/env python3
"""Validate the repository-specific structure of the WaveBench skill.

``agentskills`` validates the Agent Skills core format.  This small, dependency-
free companion checks the progressive-disclosure layout, generic instrument
language, and WaveBench-specific distribution rules without importing WaveBench
or touching instruments.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_BODY_LINES = 500
MAX_BODY_TOKENS = 5000
EXPECTED_REFERENCES = {
    "safety-and-recovery.md",
    "run-plans.md",
    "scope-and-capture.md",
    "source-and-harmonics.md",
    "power-and-dmm.md",
    "plugins.md",
    "development-validation.md",
    "eval-prompts.md",
}

LINK_RE = re.compile(r"\]\(([^)]+)\)")
ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s(])(?:/home/|[A-Za-z]:[\\/])")
SECRET_RE = re.compile(
    r"(?i)(?:-----begin [^-]+ key-----|\bAKIA[0-9A-Z]{16}\b|"
    r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{12,})"
)
DANGEROUS_RE = re.compile(
    r"(?i)(?:curl\b[^\n|]*\|\s*(?:ba|z)?sh|wget\b[^\n|]*\|\s*(?:ba|z)?sh|"
    r"git\s+(?:reset\s+--hard|push\s+--force)|rm\s+-rf\s+/|chmod\s+777)"
)
FIXED_DEVICE_RE = re.compile(
    r"(?i)\b(?:rtm|ds|dg|dp|dm)\d{3,}[a-z0-9-]*\b"
)


@dataclass
class Finding:
    level: str
    message: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, list[Finding]]:
    findings: list[Finding] = []
    if not text.startswith("---\n"):
        return {}, text, [Finding("error", "SKILL.md 必须以 YAML frontmatter 开始")]
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text, [Finding("error", "frontmatter 缺少结束标记")]

    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    fields: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if not match:
            index += 1
            continue
        key, value = match.group(1), (match.group(2) or "").strip()
        if value in {">-", ">", "|-", "|"}:
            folded: list[str] = []
            index += 1
            while index < len(lines) and (lines[index].startswith(" ") or not lines[index]):
                folded.append(lines[index].strip())
                index += 1
            fields[key] = " ".join(part for part in folded if part).strip()
            continue
        fields[key] = value.strip('"\'')
        index += 1
    return fields, body, findings


def read_repo_root(skill_dir: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(skill_dir), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(result.stdout.strip())


def check_core(skill_dir: Path, strict_git: bool) -> list[Finding]:
    findings: list[Finding] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [Finding("error", f"缺少 {skill_md}")]

    text = skill_md.read_text(encoding="utf-8")
    fields, body, parse_findings = parse_frontmatter(text)
    findings.extend(parse_findings)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if name != skill_dir.name:
        findings.append(Finding("error", f"name={name!r} 与目录名 {skill_dir.name!r} 不一致"))
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        findings.append(Finding("error", "name 必须是小写字母、数字和单连字符"))
    if not description:
        findings.append(Finding("error", "description 不能为空"))
    if len(description) > 1024:
        findings.append(Finding("error", "description 超过 1024 个字符"))
    if "compatibility" in fields and len(fields["compatibility"]) > 500:
        findings.append(Finding("error", "compatibility 超过 500 个字符"))

    body_lines = body.splitlines()
    body_tokens = len(re.findall(r"\S+", body)) + len(re.findall(r"[\u4e00-\u9fff]", body))
    if len(body_lines) > MAX_BODY_LINES:
        findings.append(Finding("error", f"SKILL.md 正文为 {len(body_lines)} 行，超过 {MAX_BODY_LINES} 行"))
    if body_tokens > MAX_BODY_TOKENS:
        findings.append(Finding("error", f"SKILL.md 正文约 {body_tokens} tokens，超过 {MAX_BODY_TOKENS}"))

    references = skill_dir / "references"
    if not references.is_dir():
        findings.append(Finding("error", "缺少 references/ 目录"))
    else:
        actual = {path.name for path in references.iterdir() if path.is_file()}
        missing = EXPECTED_REFERENCES - actual
        if missing:
            findings.append(Finding("error", f"缺少 reference：{', '.join(sorted(missing))}"))
        nested = [path for path in references.rglob("*") if path.is_dir()]
        if nested:
            findings.append(Finding("error", "references/ 不得包含嵌套目录"))

    for target in LINK_RE.findall(body):
        target = target.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("references/"):
            relative = Path(target)
            if len(relative.parts) != 2:
                findings.append(Finding("error", f"入口 reference 链接必须保持一层：{target}"))
            elif not (skill_dir / relative).is_file():
                findings.append(Finding("error", f"入口链接目标不存在：{target}"))

    if references.is_dir():
        for path in references.glob("*.md"):
            for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
                if target.split("#", 1)[0].startswith("references/"):
                    findings.append(Finding("error", f"reference 不得递归引用 reference：{path.name} -> {target}"))

    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        findings.append(Finding("error", "缺少 agents/openai.yaml"))
    else:
        yaml_text = openai_yaml.read_text(encoding="utf-8")
        required = {
            "display_name": re.search(r"^\s+display_name:\s*['\"](.+?)['\"]\s*$", yaml_text, re.MULTILINE),
            "short_description": re.search(r"^\s+short_description:\s*['\"](.+?)['\"]\s*$", yaml_text, re.MULTILINE),
            "default_prompt": re.search(r"^\s+default_prompt:\s*['\"](.+?)['\"]\s*$", yaml_text, re.MULTILINE),
        }
        for key, match in required.items():
            if not match:
                findings.append(Finding("error", f"openai.yaml 缺少或未引用 {key}"))
        if required["short_description"]:
            length = len(required["short_description"].group(1))
            if not 25 <= length <= 64:
                findings.append(Finding("error", f"short_description 长度为 {length}，应为 25–64"))
        if required["default_prompt"] and "$wavebench" not in required["default_prompt"].group(1):
            findings.append(Finding("error", "default_prompt 必须包含 $wavebench"))
        if not re.search(r"^\s+allow_implicit_invocation:\s*true\s*$", yaml_text, re.MULTILINE):
            findings.append(Finding("error", "必须显式允许隐式触发"))

    scan_files = [skill_md]
    if references.is_dir():
        scan_files.extend(sorted(references.glob("*.md")))
    if openai_yaml.is_file():
        scan_files.append(openai_yaml)
    for scan_file in scan_files:
        visible = scan_file.read_text(encoding="utf-8")
        for label, pattern in (
            ("绝对路径", ABSOLUTE_PATH_RE),
            ("疑似秘密", SECRET_RE),
            ("危险命令", DANGEROUS_RE),
            ("固定厂商或型号", FIXED_DEVICE_RE),
        ):
            if pattern.search(visible):
                findings.append(Finding("error", f"{scan_file.relative_to(skill_dir)} 包含{label}模式"))

    root = read_repo_root(skill_dir)
    if root:
        root_link = root / "SKILL.md"
        expected = skill_md.resolve()
        if not root_link.is_symlink():
            findings.append(Finding("error", "仓库根目录 SKILL.md 必须是符号链接"))
        elif root_link.resolve() != expected:
            findings.append(Finding("error", "根目录 SKILL.md 未指向规范技能入口"))
        try:
            subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", str(skill_md.relative_to(root))],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            level = "error" if strict_git else "warning"
            findings.append(Finding(level, "规范入口尚未被 Git 跟踪；提交前应 git add"))
    else:
        findings.append(Finding("warning", "无法确定 Git 仓库根目录，跳过符号链接和追踪检查"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", nargs="?", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--strict-git", action="store_true", help="未追踪规范入口时返回失败")
    args = parser.parse_args()
    skill_dir = args.skill_dir.resolve()
    findings = check_core(skill_dir, args.strict_git)
    errors = [item for item in findings if item.level == "error"]
    for item in findings:
        print(f"{item.level.upper()}: {item.message}")
    if errors:
        print(f"FAIL: {len(errors)} 个错误")
        return 1
    print("PASS: WaveBench skill 结构检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
