---
context-hierarchy: Layer 0
context-hierarchy-role: Global identity
immutable: false
recommended-context-tokens: 900
---

# DK AIS [`dkais`]

This Python package enables the download of historic AIS data provided by the
Danish Maritime Authority and its processing into typed Parquet.

This project adheres to [YAGNI](docs/guidance/standard-yagni.md) principles.

## Context hierarchy

Markdown files declare their layer in frontmatter - read
[standard-context-hierarchy.md](docs/guidance/standard-context-hierarchy.md) before creating or
editing one. Files marked `immutable: true` are never edited in passing: propose the change and
wait for approval.

## Reference

Standards and references live in `docs/guidance` as `standard-*.md` and `toolchain-*.md` files
(Layer 3). Load a file when its subject is in play, which can be determined by the frontmatter.
