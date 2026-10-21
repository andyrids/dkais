---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [mypy, typing]
---

# Toolchain - `Mypy`

Mypy is used to enforce standards for typing.

## Commands

- `uv run pkgdx-typing-hook` - Type-check the whole project (`tests/` excluded)
- `uv run pkgdx-typing-hook -p <package>` - Type-check a single package
- `uv run prek run --all-files` - Run the `typing` hook alongside all other hooks

## Configuration

The Mypy config ships inside the dev dependency `pkgdx` (`src/pkgdx/standards/mypy.ini`) and is
applied by the `pkgdx-typing-hook` shim - prefer the hook over a hand-rolled `mypy` invocation.
Enforce the usage of the type hints for all function/method args and return values.

With no target, the bundled `files = .` and `exclude = (^|/)tests/` settings check the project
from the working directory. An explicit target (`-p`, `-m` or file paths) overrides that default.

A `-p`/`-m` target is resolved as an installed package, so it needs a `py.typed` marker in the
package directory - without one, Mypy exits with "missing py.typed marker". The default target reads
files from disk and needs no marker.

## Guidance

When something is imported from a dependency, it's resolved to `Any` if Mypy can't resolve the
import.

```ini
disallow_any_unimported = true
```

- Missing stubs can be found at [typeshed/stubs](https://github.com/python/typeshed/tree/main/stubs)
- A type ignore (`# type: ignore[no-any-unimported]`) can be used when stubs are unavailable

```python
from requests import Request


def my_function(request: Request) -> None:  # type: ignore[no-any-unimported]
    ...
```

Explicit is better than implicit - `arg: Optional[str] = None` over `arg: str = None`.

```ini
no_implicit_optional = true
```

It is better to ignore only the specific type of an error. Prefer `# type: ignore[<error-code>]`
over `# type: ignore`.

```ini
show_error_codes = true
warn_unused_ignores = true
```
