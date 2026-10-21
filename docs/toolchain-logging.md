---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [logging]
---

# Toolchain - `logging`

All logging is handled through the primary package logger. To ensure logs are routed correctly,
always instantiate loggers using the package name:

```python
import logging

logger = logging.getLogger(__package__)
```

## Configuration

Logging configuration lives in `src/aisdma/logging/_logging.py` as a single `dictConfig` dictionary
(`CONFIG`), separated into two distinct contexts to prevent interference with consuming
applications:

### (1) Package logging (library)

By default, the package is configured with a `NullHandler`. This prevents the library from
polluting output when imported as a dependency.

- Setup: `configure_pkg_logging()` is called in `src/aisdma/__init__.py`.

### (2) CLI logging (application)

When executed as a CLI, logging is configured from `CONFIG` - a single `logging.StreamHandler`
on STDERR, attached to the `aisdma` logger with `propagate = false`.

- Setup: `configure_cli_logging(level)` is called in `src/aisdma/__main__.py`
(if applicable) after argument parsing. The level is `DEBUG` with `-d` or `--debug`, otherwise `WARNING`.

## (3) CLI output

Send output to STDOUT. The primary output for your command should go to STDOUT. Anything that is
machine readable should also go to STDOUT — this is where piping sends things by default.

Send messaging to STDERR. Log messages, errors, and so on should all be sent to STDERR. This means
that when commands are piped together, these messages are displayed to the user and not fed into
the next command.

- STDOUT (`CLI_CONSOLE`): primary CLI output
- STDERR (`GLOBAL_CONSOLE`): logs, errors, progress, and other diagnostic metadata

Check TTY on the stream being written. Do not disable STDERR progress because STDOUT is piped.
