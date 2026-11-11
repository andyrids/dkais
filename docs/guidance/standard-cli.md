---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
version: 2.5.0
updated: 2026-11-11
tags: [CLI]
---

# Standard - CLI

Attribution: adapted from the [Command Line Interface Guidelines](https://clig.dev/) by Aanand
Prasad, Ben Firshman, Carl Tashian & Eva Parish, licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The guidance is condensed &
reworded; this file is shared under the same licence.

CLIs build on [`pyclig`](https://github.com/andyrids/pyclig), which implements the rules marked
(pyclig) - they MUST NOT be reimplemented.

## (1) Basic rules

### Exit codes

Return zero on success and non-zero on failure (pyclig): `0` ok, `1` failure, `2` usage, `70`
internal error, `130` interrupt (Ctrl-C, or Ctrl-Break on Windows), `143` terminated
(`SIGTERM`).

### Output

Send the primary output, including anything machine-readable, to STDOUT (`ctx.out`).

### Messaging

Send messages - logs, errors, warnings, progress - to STDERR (`ctx.err`), so they reach the user
instead of the next command in a pipe.

## (2) Help

Show full help for `-h` and `--help` (pyclig). `-h` wins anywhere before `--`, even after a typo
(pyclig).

Show concise help when the program or a command runs without the arguments it needs (pyclig: a bare
group, and `no_args_is_help=True` on a command). Ignore this for programs that are interactive by
default (e.g. `npm init`).

Lead with examples - `--help` lists `EXAMPLES:` before the options (pyclig).

Concise help holds:

- A description of what the program does
- One or two example invocations (`examples=`)
- Descriptions of flags, unless there are many (pyclig: a command's own options, when it has 1
  to 6)
- A pointer to `--help`

Give each command its own examples too (`examples=` on `@cli.command()`), shown in its `--help`.
An example may show its output: the lines after the invocation print indented under it.

Headings are bold where the terminal is styled, and plain on a pipe or with `TERM=dumb` (pyclig).

## (3) Documentation

Help gives a quick sense of the tool, its options and its common tasks. Documentation goes into
full detail.

- Consider web-based documentation
  - Link it from help with `docs="https://..."` on the group and on any command with its own
    page; it prints under a closing `LEARN MORE:` section (pyclig)
  - The root's help also lists `Issues`, from the distribution's `Project-URL` metadata (pyclig)
- Consider terminal-based documentation

## (4) Output

Human-readable output comes first. Whether a stream is a TTY is the simplest test of whether a
person reads it - check the stream being written, not the other one.

- Provide machine-readable output where it does not hurt usability
- Use `--plain` where human-readable output breaks machine-readable output
  (`pyclig.options.plain()` on the commands that produce records; print them with `ctx.emit`,
  which follows `ctx.format`)
- Print formatted JSON when `--json` is passed (`pyclig.options.json_output()`; `ctx.emit` prints
  the records as an indented list)
- Print output on success, but keep it brief (`pyclig.summary`, dropped by `-q` unless `failed`)
- Tell the user when state changes (`pyclig.row`, dropped by `-q` unless `essential`)
- Provide `-q` to suppress non-essential output (pyclig, on every command: `info` lines and
  animation are dropped; notices, hints and STDOUT stay)
- Disable colour when a stream is not a TTY, `NO_COLOR` is set, `TERM` is `dumb` or `--no-color` is
  passed (pyclig). `FORCE_COLOR` means colour only: a piped stream keeps its colour but stays one
  line per message and never animates; `TTY_COMPATIBLE=1` asks for terminal layout on a pipe

## (5) Errors

Errors are a common reason to read the documentation.

- Catch errors and rewrite them for humans - wrap third-party failures in the package `Error`
- Mind the signal-to-noise ratio
- Put the most important information where the user looks first
- Show debug and traceback information for unexpected errors (pyclig)
- Make bug reports effortless (pyclig: a crash links the `Issues` project URL; on github.com &
  gitlab.com it opens a new issue pre-filled with versions, platform & exception type)
- MUST NOT put the error message, arguments, paths or the environment in a URL (pyclig)
- MUST NOT parse user input as markup (pyclig)

## (6) Arguments and flags

- Prefer flags to arguments - they make the command clearer
- Provide long versions of all flags
- Use one-letter flags only for common options
- Several arguments are fine for simple actions on several files
- Use standard names for flags where a standard exists

Standard names:

- `-a`, `--all` (`pyclig.options.select_all`)
- `--confirm NAME` (`pyclig.options.confirm`, with `pyclig.confirm_name`)
- `-d`, `--debug` (pyclig, on every command; also `<PROG>_DEBUG`)
- `-f`, `--force` (`pyclig.options.force`)
- `-n`, `--dry-run` (`pyclig.options.dry_run`)
- `--json` (`pyclig.options.json_output`)
- `-h`, `--help` (pyclig)
- `--no-input` (pyclig, on every command)
- `-o`, `--output` (`pyclig.options.output`)
- `-p`, `--port` (`pyclig.options.port`)
- `--plain` (`pyclig.options.plain`)
- `-q`, `--quiet` (pyclig, on every command)
- `--timeout SECONDS` (`pyclig.options.timeout`)
- `-u`, `--user` (`pyclig.options.user`)
- `-v`, `--version` (pyclig, before or after a command name; `-v` can also mean verbose)

Run flags (`-d`, `-q`, `--no-color`, `--no-input`) work before or after the command name. A
command's own flag with the same spelling wins (pyclig).

Where an option's value can be switched off, accept a word such as `none`:
`type=pyclig.options.NoneOr(int)` hands the command None for `--limit none` and shows
`--limit INTEGER|none` in help.

A file read from STDIN with `-` MUST NOT wait on a terminal: `click.File("r")` and
`pyclig.options.secret` refuse it with a usage error (pyclig).

Configuration, environment and secrets (pyclig):

- Config is opt-in: `pyclig.group(..., config=pyclig.Config(project=".<name>.toml"))` adds
  `--config FILE` and, with `profiles=True`, `--profile NAME`. Precedence is flags >
  environment > `.env` > profile > project file > user file > system file > defaults; the files
  are TOML and fill click's `default_map`, so top-level keys configure the root and `[sub]` a
  subcommand
- Directories come from `pyclig.paths` (`config_dir`, `data_dir`, `system_config_dir`): XDG
  everywhere but Windows, never created by the library
- `.env` is read only with `Config(dotenv=True)`, fills declared `envvar=` options the
  environment left empty, never writes `os.environ`, and skips secret-named keys
- A secret comes from a file or STDIN, never a flag value or an environment variable:
  `pyclig.options.secret("token")` adds `--token-file FILE`, where `-` reads a piped STDIN and a
  terminal STDIN is a usage error
- `<PROG>_DEBUG=1` and `<PROG>_NO_COLOR=1` act as `-d` and `--no-color` before parsing

## (7) Interactivity

- Prompt only when STDIN and STDERR are terminals and `--no-input` was not passed (pyclig:
  `ctx.can_prompt`; `pyclig.confirm` fails naming `--force`, and a click `prompt=` option takes its
  default or, without one, fails naming the option)
- Confirm large or destructive actions with `pyclig.confirm`; `--force` skips the prompt; without a
  terminal, or with `--no-input`, fail and name `--force`
- Confirm severe actions by name with `pyclig.confirm_name`: `--confirm NAME` or the typed name;
  `--force` never skips it; without a terminal, or with `--no-input`, fail and name `--confirm`
- Let the user escape (pyclig: Ctrl-C at a prompt)

## (8) Subcommands

Subcommands reduce complexity.

- Be consistent across subcommands
- Name multiple levels of subcommand consistently

Noun-verb and verb-noun ordering both work; noun-verb is more common.

## (9) Robustness

- Validate user input
- Being responsive matters more than being fast
- Show progress for long operations, on STDERR
- Work in parallel where it helps, with care
- Make things time out
- Make operations recoverable
- Make the program crash-only

## (10) Future-proofing

Interfaces change only after a long, documented deprecation.

- Keep changes additive where possible
- Warn before a non-additive change
  (`deprecated="use 'prog new'"` on a command or an option; in use it prints
  `warning: command 'old' is deprecated - use 'prog new'` on STDERR - pyclig)
- Changing output meant for humans is usually fine
- Avoid a catch-all subcommand

## (11) Signals and control characters

- Say so as soon as Ctrl-C or `SIGTERM` arrives, and exit 130 or 143 (pyclig)
- Bound clean-up after an interrupt with `ctx.cleanup(timeout=...)`: a second Ctrl-C or the
  timeout skips it and says so (pyclig)
- Wrap blocking network I/O in `with ctx.cleanup(timeout=2):` too. A blocking call holds Ctrl-C
  back until it returns, notably on Windows; inside the block the notice prints at once and the
  run exits when the call returns or the timeout passes (pyclig)
- Give network calls a timeout, configurable with `--timeout SECONDS` (`pyclig.options.timeout`)

## (12) Conformance

- Every CLI has a test calling `pyclig.testing.assert_conforms(cli)`. A finding the CLI accepts is
  silenced with `ignore={"ID": "reason"}`; the IDs are the rows of pyclig's
  [`docs/clig-coverage.md`](https://github.com/andyrids/pyclig/blob/main/docs/clig-coverage.md)
- Pass `samples=[["list", "--json"], ...]` with command lines that are safe to run in a test. The
  kit runs each one and reports a failing exit or invalid `--json` (BAS-4), a message on STDOUT
  (BAS-5) and a traceback (ERR-2). Without `samples=` no command body runs
- Raising the pyclig pin to 0.7 turns on new static rules (ARG-1, ARG-3, ARG-5, ENV-2, ENV-4,
  SUB-4, SUB-5), so a passing test may report new findings: fix them, or `ignore=` with a reason
