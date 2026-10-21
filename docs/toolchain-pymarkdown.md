---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [pymarkdown, markdown, lint]
---

# Toolchain - `PyMarkdown`

PyMarkdown is used to implement Markdown linting standards.

## Commands

- `uv run pkgdx-markdown-hook <file>` - Lint a file
- `uv run prek run --all-files` - Run the `markdown` hook alongside all other hooks

## Configuration

The PyMarkdown config ships inside dev dependency `pkgdx` and is applied by the
`pkgdx-markdown-hook` shim - prefer the hook over a hand-rolled `pymarkdown` invocation.

A bare `uv run pymarkdown scan <file>` uses PyMarkdown's own defaults (80 characters, setext
headings), not the project's, so its output is misleading - it reports violations the hook does
not raise.

## Gotcha - `BadTokenizationError`

With the `markdown-tables` extension enabled (as in the bundled config), a pipe inside certain list
items crashes the PyMarkdown tokenizer - an upstream bug, present in 0.9.40. Known triggers:

```markdown
- User review & acceptance MUST be explicit before continuation:
  - "approved" | "continue" - proceed as presented
```

```markdown
> quote
>
> - `A | B` first item
> - second item
```

Escaping the pipe (`\|`) does not help. The hook passes `--continue-on-error`, so the crash fails
only the affected file and names it, while every other file is still linted:

```text
docs/example.md:0:0: An unhandled error occurred processing the document.
```

The diagnostic names no line or rule, so bisect within the file by rewriting one block at a time.
The fix is to drop the pipe - write 'or' - rather than to restructure the list, since restructuring
does not reliably help.
