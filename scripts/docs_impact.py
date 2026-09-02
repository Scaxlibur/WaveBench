#!/usr/bin/env python3
"""Report canonical documentation that may be affected by a code diff."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ImpactRule:
    title: str
    paths: tuple[str, ...]
    pages: tuple[str, ...]


RULES = (
    ImpactRule(
        "CLI / command surface",
        ("src/wavebench/cli.py",),
        ("docs/reference/cli.md",),
    ),
    ImpactRule(
        "run plan / templates",
        (
            "src/wavebench/services/run_plan.py",
            "src/wavebench/services/run_templates.py",
            "plans/",
            "scripts/generate_docs.py",
        ),
        (
            "docs/reference/run-schema.md",
            "docs/reference/generated/run-schema.md",
            "docs/tutorials/from-template-to-report.md",
            "docs/how-to/run-an-experiment.md",
        ),
    ),
    ImpactRule(
        "configuration",
        ("src/wavebench/config.py", "wavebench.example.toml"),
        ("docs/reference/configuration.md", "docs/getting-started/configure-bench.md"),
    ),
    ImpactRule(
        "artifacts / reports",
        ("src/wavebench/services/run_artifacts.py", "src/wavebench/report/"),
        ("docs/reference/artifacts.md", "docs/how-to/run-an-experiment.md"),
    ),
    ImpactRule(
        "capability / plugin API",
        (
            "src/wavebench/instruments/",
            "src/wavebench/plugins/registry.py",
            "src/wavebench/services/capability_explain.py",
        ),
        (
            "docs/concepts/capability-model.md",
            "docs/reference/plugins/index.md",
            "docs/development/plugin-development.md",
            "docs/development/instrument-drivers.md",
        ),
    ),
    ImpactRule(
        "safety / session behavior",
        (
            "src/wavebench/services/access_policy.py",
            "src/wavebench/services/operation_specs.py",
            "src/wavebench/services/resource_lease.py",
            "src/wavebench/transport/",
        ),
        (
            "docs/concepts/safety-model.md",
            "docs/concepts/sessions-and-recovery.md",
            "docs/reference/errors.md",
        ),
    ),
    ImpactRule(
        "RF source operations",
        ("src/wavebench/services/rf_source_service.py", "src/wavebench/instruments/rf_source_extensions.py"),
        (
            "docs/how-to/use-rf-source.md",
            "docs/concepts/capability-model.md",
            "docs/concepts/safety-model.md",
            "docs/reference/run-schema.md",
        ),
    ),
    ImpactRule(
        "package / release metadata",
        ("pyproject.toml", "CHANGELOG.md"),
        ("README.md", "docs/getting-started/installation.md"),
    ),
)


def _matches(path: str, candidate: str) -> bool:
    return path.startswith(candidate) if candidate.endswith("/") else path == candidate


def impacts_for_paths(paths: list[str]) -> list[tuple[ImpactRule, tuple[str, ...]]]:
    normalized = sorted({path.replace("\\", "/") for path in paths if path})
    impacts: list[tuple[ImpactRule, tuple[str, ...]]] = []
    for rule in RULES:
        matches = tuple(
            path for path in normalized if any(_matches(path, candidate) for candidate in rule.paths)
        )
        if matches:
            impacts.append((rule, matches))
    return impacts


def changed_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return []
    return result.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD~1", help="base Git revision")
    parser.add_argument("--head", default="HEAD", help="head Git revision")
    parser.add_argument("--paths", nargs="*", help="explicit changed paths; avoids Git lookup")
    args = parser.parse_args(argv)

    paths = args.paths if args.paths is not None else changed_paths(args.base, args.head)
    if not paths:
        print("Documentation impact: no changed paths available; pass --paths for a local review.")
        return 0

    impacts = impacts_for_paths(paths)
    if not impacts:
        print("Documentation impact: no canonical-page mapping for this diff.")
        return 0

    print("Documentation impact candidates (review, not an automatic finding):")
    for rule, matches in impacts:
        print(f"- {rule.title}: {', '.join(rule.pages)}")
        print(f"  changed: {', '.join(matches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
