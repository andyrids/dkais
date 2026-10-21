---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [CLI, argparse]
---

# Standard - CLI

This standard is based on the [Command Line Interface Guidelines](https://clig.dev/).

## (1) Basic rules

These basic rules make a CLI easy to use and act as a solid foundation.

### Exit codes

Return zero exit code on success and non-zero on failure. Map the non-zero exit codes to the most
important failure modes.

### Output

Send output to STDOUT, including anything that is machine readable.

### Messaging

Send messaging to STDERR, including log messages, errors and exceptions. When commands are piped
together, these messages are displayed to the user and not fed into the next command.

## (2) Help

Display extensive help text when asked. Display help when passed `-h` or `--help` flags.

Display concise help text by default. When `pkgdx` or `pkgdx subcommand` requires arguments to
function and is run with no arguments, display concise help text.

NOTE: You can ignore this guideline if your program is interactive by default (e.g. `npm init`).

Concise help text:

- A description of what your program does.
- One or two example invocations.
- Descriptions of flags, unless there are lots of them.
- An instruction to pass the `--help` flag for more information.

## (3) Documentation

The purpose of help text is to give a brief, immediate sense of what your tool is, what options are
available and how to perform the most common tasks. Documentation, on the other hand, is where you
go into full detail.

- Consider web-based documentation
- Consider terminal-based documentation

## (4) Output

Human-readable output is paramount. The most simple heuristic for whether a particular output
stream (STDOUT or STDERR) is being read by a human is whether or not it is a TTY.

- Have machine-readable output where it does not impact usability
- Where human-readable output breaks machine-readable output, use `--plain`
- Display output as formatted JSON if `--json` is passed
- Display output on success, but keep it brief
- When changing state, inform the user

## (5) Errors

One of the most common reasons to consult documentation is to fix errors.

- Catch errors and rewrite them for humans
- Be mindful of signal-to-noise ratio
- Consider where the user will look first - BLUF
- Provide provide debug and traceback information on unexpected errors
- Make bug report submission effortless

## (6) Arguments and flags

- Prefer flags to args - it makes it much clearer what is going on
- Provide full-length versions of all flags
- Only use one-letter flags for commonly used flags
- Multiple arguments are fine for simple actions against multiple files
- Use standard names for flags, if there is a standard

Common options:

- `-a`, `--all`
- `-d`, `--debug`
- `-f`, `--force`
- `--json`
- `-h`, -`-help`
- `-o`, `--output`
- `-q`, `--quiet`
- `-u`, `--user`
- `--version`
- `-v` (often means either verbose or version)

## (7) Interactivity

- Only use prompts or interactive elements if STDIN is an interactive terminal
- Let the user escape

## (8) Subcommands

You can reduce complexity by making a set of subcommands.

- Be consistent across subcommands
- Use consistent names for multiple levels of subcommand

Either noun verb or verb noun ordering works, but noun verb seems to be more common.

## (9) Robustness

- Validate user input
- Responsive is more important than fast
- Show progress if something takes a long time
- Do stuff in parallel where you can, but be thoughtful about it
- Make things time out
- Make it recoverable
- Make it crash-only

## (10) Future-proofing

Interfaces should not change without a lengthy and well-documented deprecation process.

- Keep changes additive where possible
- Warn before you make a non-additive change
- Changing output for humans is usually OK
- Avoid a catch-all subcommand

## (11) Signals and control characters

- Exit on `Ctrl-C` ASAP
- Skip long-running cleanup operations on `Ctrl-C` and inform the user
