---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
version: 1.1.0
updated: 2026-10-31
tags: [CLI]
---

# Standard - CLI

Attribution: adapted from the [Command Line Interface Guidelines](https://clig.dev/) by Aanand
Prasad, Ben Firshman, Carl Tashian & Eva Parish, licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The guidance is condensed &
reworded; this file is shared under the same licence.

CLIs build on [`pyclig`](https://github.com/andyrids/pyclig), which implements the rules marked
(pyclig) - do not reimplement them.

## (1) Basic rules

### Exit codes

Return zero on success and non-zero on failure (pyclig): `0` ok, `1` failure, `2` usage, `70`
internal error, `130` interrupt.

### Output

Send the primary output, including anything machine-readable, to STDOUT (`ctx.out`).

### Messaging

Send messages - logs, errors, warnings, progress - to STDERR (`ctx.err`), so they reach the user
instead of the next command in a pipe.

## (2) Help

Show full help for `-h` and `--help` (pyclig).

Show concise help when the program runs without the arguments it needs (pyclig, for a bare
invocation). Ignore this for programs that are interactive by default (e.g. `npm init`).

Concise help holds:

- A description of what the program does
- One or two example invocations (`examples=`)
- Descriptions of flags, unless there are many
- A pointer to `--help`

## (3) Documentation

Help gives a quick sense of the tool, its options and its common tasks. Documentation goes into
full detail.

- Consider web-based documentation
- Consider terminal-based documentation

## (4) Output

Human-readable output comes first. Whether a stream is a TTY is the simplest test of whether a
person reads it - check the stream being written, not the other one.

- Provide machine-readable output where it does not hurt usability
- Use `--plain` where human-readable output breaks machine-readable output
- Print formatted JSON when `--json` is passed
- Print output on success, but keep it brief
- Tell the user when state changes

## (5) Errors

Errors are a common reason to read the documentation.

- Catch errors and rewrite them for humans - wrap third-party failures in the package `Error`
- Mind the signal-to-noise ratio
- Put the most important information where the user looks first
- Show debug and traceback information for unexpected errors (pyclig)
- Make bug reports effortless (pyclig: the `Issues` project URL)
- Never parse user input as markup (pyclig)

## (6) Arguments and flags

- Prefer flags to arguments - they make the command clearer
- Provide long versions of all flags
- Use one-letter flags only for common options
- Several arguments are fine for simple actions on several files
- Use standard names for flags where a standard exists

Standard names:

- `-a`, `--all` (`pyclig.options.select_all`)
- `-d`, `--debug` (pyclig, on every command)
- `-f`, `--force` (`pyclig.options.force`)
- `--json` (`pyclig.options.json_output`)
- `-h`, `--help` (pyclig)
- `-o`, `--output`
- `-q`, `--quiet`
- `-u`, `--user`
- `-v`, `--version` (pyclig; `-v` can also mean verbose)

## (7) Interactivity

- Prompt only when STDIN is an interactive terminal
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
- Changing output meant for humans is usually fine
- Avoid a catch-all subcommand

## (11) Signals and control characters

- Exit as soon as possible on Ctrl-C (pyclig)
- Skip long-running cleanup on Ctrl-C and tell the user
