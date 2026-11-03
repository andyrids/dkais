---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
version: 1.0.0
updated: 2026-10-30
tags: [context-hierarchy, frontmatter, immutable, documentation]
---

# Standard - context hierarchy

Every markdown file written for agents and contributors declares its layer in YAML frontmatter.
The layer says what kind of file it is and when to load it, so the global identity is read once
and reference material is pulled in only when its subject is in play.

## (1) Layers

| Layer | Role | Files | Budget | Immutable |
|---|---|---|---|---|
| Layer 0 | Global identity | `AGENTS.md` (`CLAUDE.md` symlinks to it) | 900 | `false` |
| Layer 3 | Reference material | `docs/guidance/*.md` (`standard-*`, `toolchain-*`) | 2500 | `true` |

- Read Layer 0 at the start of every task. It names the project, its constraints and where the
  reference material lives.
- Load a Layer 3 file only when its subject is in play. Match the task against its `tags`.
- The numbering is shared with the [ICM methodology](https://github.com/andyrids/icm-spec), which
  adds Layer 1 (`CONTEXT.md`, workspace routing), Layer 2 (stage routing) and Layer 4 (working
  artifacts). Without ICM a project uses Layers 0 and 3 only, so do not create a root
  `CONTEXT.md` for anything else.

## (2) Frontmatter keys

| Key | Carried by | Meaning |
|---|---|---|
| `context-hierarchy` | Every file | The layer, e.g. `Layer 3` |
| `context-hierarchy-role` | Every file | The layer's role, e.g. `Reference material` |
| `immutable` | Every file | `true` for the factory configuration (see (3)) |
| `recommended-context-tokens` | Budgeted files | Token budget, as a signal rather than a limit |
| `version` | Immutable files | Document version (see (4)) |
| `updated` | Immutable files | Date of the last approved amendment, `yyyy-mm-dd` |
| `tags` | Layer 3 | Keywords that decide when the file is loaded |

A file that outgrows its budget is worth a look - it may have started doing another layer's job.

## (3) Immutable files

`immutable: true` marks the factory configuration: the standards and toolchain references copied
from the `copier-python-package` template. 'Immutable' does not mean frozen - it means a file
changes only through discussion and approval.

- MUST NOT be edited automatically, in passing or as a side effect of another task, whether by an
  agent or a tool
- An agent that finds an immutable file wrong or out of date MUST stop and propose the change,
  with its reason, instead of applying it
- An approved amendment is made in the template and pulled into each project with
  `copier update`, bumping `version` and `updated` in the same change
- A project-only amendment needs the same approval, and must be merged again on every
  `copier update`

`immutable: false` files, such as `AGENTS.md`, belong to the project and change with it.

## (4) Versioning immutable files

`version` applies [Semantic Versioning](https://semver.org/spec/v2.0.0.html) to guidance:

- MAJOR - a requirement (MUST) is added, changed or removed, so work that complied may no longer
  comply
- MINOR - a recommendation (SHOULD, COULD) or new section is added, and compliant work still
  complies
- PATCH - wording, examples or typos change, with no change in meaning

`updated` is the date the amendment was approved. Git history in a consuming project only shows
the `copier update` commit, so these two keys record which revision a copy holds.
