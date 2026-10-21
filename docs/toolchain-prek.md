---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [prek, pre-commit]
---

# Toolchain - `Prek`

Prek is used as a pre-commit hook manager and is installed as a dependency. Running Prek through
`uv run` ensures that the project virtual environment is activated and utilized.

## Commands

- `uv run prek install` - Install Git shims
- `uv run prek run` - Run hooks for files staged in Git
- `uv run prek run -vvv` - Run hooks with verbose output
- `uv run prek run --all-files` - Run hooks for all files
- `uv run prek validate-config prek.toml` - Validate a Prek config

## Configuration

- Project config: `prek.toml`
  - MUST not be changed by hand - re-run `uv run pkgdx init` instead
