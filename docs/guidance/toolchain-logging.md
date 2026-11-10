---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
version: 2.1.0
updated: 2026-11-10
tags: [logging]
---

# Toolchain - `logging`

All logging goes through the package logger. Create module loggers from the module name, so
records propagate to the package logger:

```python
import logging

logger = logging.getLogger(__name__)
```

## (1) Package logging (library)

The package `__init__.py` adds a `NullHandler` to the package logger and does nothing else -
no handlers, consoles, file reads or `rich.reconfigure()` at import:

```python
logging.getLogger(__name__).addHandler(logging.NullHandler())
```

## (2) CLI logging (application)

`pyclig` configures logging for each run of the CLI, on the package of the group callback:

- Without `-d`, warnings and errors print as `warning:` / `error:` lines on STDERR
- With `-d` or `--debug` (accepted before or after the command), `DEBUG` records print with times
  and call sites - laid out by a `RichHandler` on a terminal, else one plain line each.
  `FORCE_COLOR` does not change the layout
- The logger stops propagating during the run and is restored afterwards
- When the root logger has no handlers, third-party `WARNING` records and `warnings.warn` print
  as `warning:` lines too - the message only, with `file:line` under `-d`. A root configured by
  the application is left alone, and both are restored afterwards
- `<PROG>_LOG_FILE=FILE` appends every record of the run, at `DEBUG`, to `FILE`; a crash adds
  its traceback and the hint `debug log: FILE`
- `with ctx.logs_on_failure():` holds a block's info records and prints them only when an
  exception leaves the block; raise to fail, and keep the block outside `progress` and `status`

Do not configure handlers yourself, and do not keep a logging config file. Do not call
`logging.captureWarnings`.

## (3) CLI output

Each run has two Rich consoles, created when the run starts and passed in - never module globals:

- `ctx.out` (STDOUT): the primary output, including anything machine-readable
- `ctx.err` (STDERR): logs, errors, progress and other messages

Pass `ctx.err` to every `Progress` or `Status`, so log lines and live displays share one console.
Check the TTY of the stream being written: do not disable STDERR progress because STDOUT is
piped. Gate animation on `console.is_interactive`, which `-q` also turns off.
