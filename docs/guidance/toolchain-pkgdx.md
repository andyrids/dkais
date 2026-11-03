---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
version: 1.1.1
updated: 2026-11-02
tags: [pkgdx, prek, pre-commit, config, exclude, submodules]
---

# Toolchain - `pkgdx`

`pkgdx` is the dev dependency that carries the canonical standards. It ships the Ruff, Mypy and
PyMarkdown configs, the hook shims that apply them, and the template for the hooks it manages in
`prek.toml`.

## Commands

- `uv run pkgdx init` - Merge the `pkgdx` hooks into `prek.toml`, install the Git shims, check for
  hook updates and create `.secrets.baseline`
- `uv run pkgdx init --reset` - As `init`, but overwrite `prek.toml` (see Resetting)
- `uv run pkgdx config` - Show which config each hook uses (user or bundled) and its path, then
  each path the project excludes
- `uv run pkgdx config --export` - Copy the bundled configs into the user config directory,
  keeping any file already there
- `uv run prek update --repo https://gitlab.com/andyrids/pkgdx` - Move the `pkgdx` hooks to the
  latest `pkgdx` release

## Hooks in `prek.toml`

`prek.toml` is shared: `pkgdx` manages its own hooks there, and the project can add its own. These
entries belong to `pkgdx`:

- The `https://gitlab.com/andyrids/pkgdx` repo and its hooks (`pkgdx-lint`, `pkgdx-format`,
  `pkgdx-markdown`, `pkgdx-secrets`)
- The `pkgdx-typing` hook in the `local` repo
- The `check-toml`, `check-yaml` and `detect-private-key` hooks in the `builtin` repo

Their behaviour is set by the `pkgdx` release, not by the project:

- MUST NOT edit or remove the `pkgdx` entries by hand
- COULD add custom `local` hooks next to `pkgdx-typing`, or remote hooks as new `[[repos]]` entries

On an existing `prek.toml`, `init` only adds what is missing - repos matched by `repo`, hooks by
`id` - and never edits or removes an entry, `rev` included. Project hooks survive it.

## Excluding files and folders

`pkgdx-lint`, `pkgdx-format` and `pkgdx-typing` walk the project themselves, so they check and
auto-fix everything they find. Submodules and vendored or generated code SHOULD be listed in the
project's `pyproject.toml`:

```toml
[tool.pkgdx]
exclude = ["submodules", "vendor", "src/app/_gen.py"]
```

The list adds to the config in use, bundled or user, and is committed, so it applies in CI and
survives `init --reset` and `rev` updates.

- Entries are literal paths relative to that `pyproject.toml`, `/` separated and case-sensitive.
- A folder covers everything below it, and `vendor` never matches `src/vendor`. List a folder
  rather than its files.
- An invalid list, including any glob, control character or name ending in a dot or space, fails
  each hook that applies the list with exit code 1 before a tool runs.
- A path that does not exist is allowed, as an uninitialized submodule is normal. `pkgdx config`
  marks it `(not found)`, which also shows up a typo, though not wrong case on a
  case-insensitive filesystem.
- The nearest `pyproject.toml` above where the hook runs is read, so a workspace needs an entry at
  each level that runs hooks.
- A hook run from inside an excluded folder checks that folder.

These hooks apply the list: `pkgdx-lint`, `pkgdx-format`, `pkgdx-typing` and `pkgdx-markdown`. An
explicit target changes what they do:

- `pkgdx-lint` and `pkgdx-format` bypass the list for a file target, and for a folder target that
  is listed or inside a listed folder. A folder target above a listed path still skips that path.
- `pkgdx-typing` bypasses the list for a file or `-m` target. A folder or `-p` target still applies
  it below that target, so a listed folder exits 2, and `-p` on a listed package checks only its
  `__init__.py` and what that imports, so it can exit 0 with errors in its other modules.
- `pkgdx-markdown` drops any path it is given at or below a listed path, which covers every file
  prek passes. It does not look inside a folder or glob, and it runs a command with `scan` options
  as given.

The other hooks do not apply the list, so they still see tracked files: `pkgdx-secrets`,
`check-toml`, `check-yaml` and `detect-private-key`. A secret in vendored code is still a leaked
secret, and false positives belong in `.secrets.baseline`. A top-level `exclude` in `prek.toml` can
filter them, but a reset loses it.

With a list set and no arguments, Mypy runs with `--follow-imports=silent`: it reads excluded code
the project imports for its types and hides its errors, though a syntax error there still stops
it. From a subfolder, this also hides errors in project modules outside that folder.

A run with any argument, even a flag such as `--pretty`, leaves import following alone, so it
reports those errors and can fail where the no-argument hook passes. Add `--follow-imports=silent`
to match. If every Python file is excluded, Mypy exits 2.

## Resetting

`uv run pkgdx init --reset` overwrites `prek.toml` with the `pkgdx` template, losing every project
hook, remote repo and edit.

- MUST copy project hooks out of `prek.toml` before a reset, and add them back after

## Upgrading

1. `uv lock --upgrade-package pkgdx`, then `uv sync`
2. `uv run prek update --repo https://gitlab.com/andyrids/pkgdx` to move `rev` to the same release.
   The remote hooks are defined in the `pkgdx` repo at that `rev`, so they follow it.
3. `pkgdx-typing` is copied into `prek.toml` in full, so neither step refreshes it. When the
   `pkgdx` CHANGELOG reports a change to it, apply it with a reset (see Resetting).

Remote hooks run the `pkgdx` at `rev`, while `pkgdx config` and `pkgdx-typing` run the locked one.
To adopt `[tool.pkgdx] exclude`, SHOULD complete steps 1 and 2, add the table and run
`uv run prek run --all-files` before removing a workaround such as `.ignore`. With `rev` still old,
`--fix` rewrites a listed path even though `pkgdx config` shows it as excluded.

## Config overrides

Each hook resolves its config when it runs: a file of the same name in the user config directory
replaces the bundled one, otherwise the bundled one is used.

- Directory: `PKGDX_CONFIG_DIR`, else `%APPDATA%\pkgdx` (Windows), else `$XDG_CONFIG_HOME/pkgdx`
  or `~/.config/pkgdx`
- Files: `ruff.toml`, `mypy.ini`, `pymarkdown.toml`, and `hooks.toml` (read only by `pkgdx init`)
- An override replaces the whole file - nothing is merged, so it stops following `pkgdx` releases
- `PKGDX_CONFIG_DIR` MUST be set wherever the hooks run, CI included. Unset, the hooks fall back to
  the platform directory and then the bundled configs without warning; set to a path that is not an
  existing directory, they fail with exit code 1 (`pkgdx-secrets` is unaffected)

## Installation

`pkgdx` is a dev dependency, resolved from PyPI.
