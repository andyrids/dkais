<!-- pyml disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> [!NOTE]
>
> - `[SemVer] - yyyy-mm-dd` or `[Unreleased]` for release heading.
> - `Added` for new features.
> - `Changed` for changes in existing functionality.
> - `Deprecated` for soon-to-be removed features.
> - `Removed` for now removed features.
> - `Fixed` for any bug fixes.
> - `Security` in case of vulnerabilities.

## [Unreleased]

### Added

- Shell completion (`_DKAIS_COMPLETE`), from click.
- `exceptions.BucketError`: S3 listing failures print `error:` instead of a traceback.

### Changed

- The CLI is built on [pyclig](https://github.com/andyrids/pyclig) & click instead of argparse.
- Exit codes: usage errors exit `2` (was `64`), unexpected errors `70` (was `2`) (#12).
- `-v` prints `dkais <version>` (was `v<version>`); bare `dkais` prints concise help.
- Help: click layout, `-d, --debug` listed for every command, commands in workflow order.
- A missing data dir is a usage error naming `DKAIS_DATA_DIR`, checked before the selection.
- `download`/`process` without `--start`/`--all` print `error:`, usage & a hint (exit 2).
- `docs/standard-cli.md`, `docs/toolchain-logging.md` & `docs/toolchain-pytest.md` (#14).

### Removed

- argparse option prefixes, e.g. `--st` for `--start`.
- `dkais.logging` (`GLOBAL_CONSOLE`, `CLI_CONSOLE`, `configure_cli_logging`) & `config.toml` (#16).
- `dkais._core` (`ExitCode`, `CLIContext`, `CLIGFormatter`, `resolve_data_dir`).
- `exceptions.DataDirNotSetError`.

### Fixed

- Usage errors go to STDERR only & print user input literally (#7).
- `-d/--debug` is accepted after the command name (#8).
- Ctrl-C anywhere prints `warning: interrupted` & exits 130, without a traceback (#9).
- STDOUT honours `FORCE_COLOR`, `TTY_COMPATIBLE` & `NO_COLOR` (#10).
- `list` prints tab-separated rows when STDOUT is piped (#11).
- `--help`, `-v` & usage errors no longer import polars, boto3 & psutil (#13).
- A broken STDOUT pipe (`| head`) exits 1 silently, on Windows too.

## [0.1.0] - 2026-10-25

### Added

- [pkgdx](https://gitlab.com/andyrids/pkgdx) canonical standards management.
- CI/CD pipeline for linting, testing & release.
- Standards & guidelines documentation in `docs/`
- `dkais list` command to catalogue bucket archives & local state (`--json`).
- `dkais download` command for concurrent boto3 downloads with Rich progress.
- `dkais process` command converting all CSV dialects to `YEAR=/MONTH=` Parquet.
- `dkais process` extracts one day ahead, in memory when RAM allows, with zlib-ng.
- `scripts/` profiling & benchmark tools for `dkais process`.

### Changed

- `download`/`process` output is minimal on a terminal (one transient progress line,
  `warning:`/`error:` messages, one summary), plain lines when not a terminal.
- Expected failures print one line; tracebacks & verbose logs only with `--debug`.
- Summaries go to STDOUT, as a `key=value` line when STDOUT is not a terminal.
- `list` warns when no data directory is set instead of silently omitting local state.

### Fixed

- Redirected output no longer crashes on non-ASCII glyphs under Windows cp1252.
- `dkais download --jobs` rejects values below 1 with a usage error.
- `dkais.__version__` falls back to `0.0.0+unknown` when package metadata is unavailable.
