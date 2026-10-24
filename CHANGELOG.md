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

- [pkgdx](https://gitlab.com/andyrids/pkgdx) canonical standards management.
- CI/CD pipeline for linting, testing & release.
- Standards & guidelines documentation in `docs/`
- `dkais list` command to catalogue bucket archives & local state (`--json`).
- `dkais download` command for concurrent boto3 downloads with Rich progress.
- `dkais process` command converting all CSV dialects to `YEAR=/MONTH=` Parquet.
- `dkais process` extracts one day ahead, in memory when RAM allows, with zlib-ng.
- `scripts/` profiling & benchmark tools for `dkais process`.

### Changed

- `download`/`process` output is cargo-style on a terminal, plain lines when not a terminal.
- Expected failures print one line; tracebacks & verbose logs only with `--debug`.
- Summaries go to STDOUT, as a `key=value` line when STDOUT is not a terminal.

### Fixed

- Redirected output no longer crashes on non-ASCII glyphs under Windows cp1252.
