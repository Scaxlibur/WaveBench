---
name: wavebench-docs
description: >-
  Develop and maintain the WaveBench repository documentation system. Use only
  for active documentation development: audits, information-architecture
  migrations, writing or rewriting project docs, generated references, and
  documentation-focused diff reviews. Documentation must be a primary requested
  deliverable; do not use for incidental doc edits during ordinary code changes,
  merely reading docs, instrument operation, or unrelated Chinese copyediting.
license: MIT
metadata:
  author: "WaveBench maintainers"
  version: "1.0.0"
  project: "wavebench"
---

# WaveBench documentation development

## Boundary

Use this skill only while developing or reviewing documentation owned by the
WaveBench repository. It governs information architecture, page responsibility,
canonical sources, migrations, navigation, examples, and documentation CI.

Do not use it for:

- normal WaveBench operation, diagnosis, measurement, or hardware control;
- answering a question by reading existing docs without changing or auditing them;
- ordinary code changes with only an incidental one-line documentation update;
- generic Markdown editing or Chinese copyediting outside WaveBench.

Hardware access is never part of a documentation audit. Runtime commands used to
verify help or schema must be offline. If a documentation example requires real
hardware, validate it statically unless the user separately authorizes the live
operation under the `wavebench` safety workflow.

## Start from repository facts

1. Work from the Git repository root and inspect `git status --short --branch`.
2. Read `README.md`, `pyproject.toml`, `CHANGELOG.md`, the relevant documentation
   indexes, and the pages directly in scope.
3. Resolve changing claims from implementation, executable help/schema, tests,
   descriptors, and release tags. Existing prose is evidence to audit, not proof
   of current behavior.
4. Classify each page by audience, reader goal, type, canonical facts, and related
   pages before editing it.
5. Preserve unrelated changes and do not move or rewrite broad document sets
   without an accepted audit and migration slice.

Read [information-architecture.md](references/information-architecture.md) when
deciding taxonomy, page contracts, sources of truth, Core/plugin ownership,
README scope, or user journeys.

## Choose one mode

| Mode | Use when | Load |
| --- | --- | --- |
| `audit` | Assess a documentation set without broad edits | [audit.md](references/audit.md) |
| `migrate` | Apply an accepted audit in small, traceable slices | [migrate.md](references/migrate.md) |
| `write` | Add or substantially rewrite a specific page | [write.md](references/write.md) |
| `review` | Review a documentation PR or diff | [review.md](references/review.md) |

Load only the selected mode plus `information-architecture.md` when that mode
needs taxonomy or source ownership. Do not turn every review into a repository-wide
audit.

## Invariants

- One changing fact has one canonical source. Guides may explain or summarize it;
  they must not become another complete copy.
- Separate current reliable behavior, explicitly marked Experimental behavior,
  and future RFC/roadmap work. Milestones and RFCs do not prove availability.
- Core documentation owns generic models and contracts. Model-specific SCPI,
  quirks, profiles, limits, and verification status belong to the instrument
  plugin repository.
- A page has one primary action: `KEEP`, `REWRITE`, `SPLIT`, `MERGE`, `MOVE`,
  `GENERATE`, `ARCHIVE`, or `DELETE`.
- Structure and facts come before prose polish. For Chinese writing or review,
  apply `tech-doc-style-chinese` only after page responsibility and sources are
  settled. Do not assume that skill's unrelated `Project-Overrides.md` applies to
  WaveBench.
- Prefer generated Reference when stable code or schema can produce it. Generated
  output must name its source and have a drift check before it becomes canonical.

## Mechanical audit

Run the dependency-free checker from the repository root:

```bash
python .agents/skills/wavebench-docs/scripts/audit_docs.py
```

The script checks deterministic breakage and reports judgment-heavy concerns as
warnings. It does not decide page type, migration action, user-journey quality, or
whether content belongs in a Guide, Reference, or Concept.

For trigger-boundary maintenance, read [eval-prompts.md](references/eval-prompts.md).

## Handoff

Report the documentation scope, canonical sources checked, files changed, audit
errors and warnings, commands or examples verified, content intentionally deferred,
and whether any live hardware, local configuration, generated data, or virtual
environment was touched.
