---
context-hierarchy: Layer 3
context-hierarchy-role: Reference material
immutable: true
recommended-context-tokens: 2500
tags: [pytest, unit-testing]
---

# Toolchain - `Pytest`

Pytest is used for unit testing, with tests colocated in `tests/`.

## Commands

- `uv run pytest -v` - Run the full test suite (excludes the `conformance` tier)
- `uv run pytest tests/test_setup.py -v` - Run a single test module
- `uv run pytest -m conformance -v` - Run only the `conformance` tier
- `uv run coverage run -m pytest` then `uv run coverage report` - Run under coverage (see
  `reference-toolchain-coverage.md`); excludes the `conformance` tier, same as a bare `pytest` run

## Configuration

- `pyproject.toml` `[tool.pytest.ini_options]` - `addopts = ["--import-mode=importlib", "-m", "not
  conformance"]`

## Conventions

- Test modules: `tests/test_<module>.py`, mirroring `src/dkais/<module>.py`
- Shared fixtures live once in `tests/conftest.py` and are consumed via dependency injection
  rather than re-declared per test module
- Progress and log TTY tests patch `GLOBAL_CONSOLE.is_terminal`, not `sys.stdout.isatty`. Logs and
  progress are STDERR messaging ([clig.dev](https://clig.dev/) basics + output); STDOUT TTY is a
  separate concern for primary CLI output
- Any dataclass instances should be built via factory fixtures that supply defaults for every field
  and accept `**overrides` - tests override only the fields they assert on, so a model field
  addition touches a single conftest default rather than every construction site
- Mock external processes with `unittest.mock.patch` (see `mock_subprocess_run`) instead of
  invoking real subprocesses
- Use `tmp_path_factory` for isolated filesystem fixtures (see `mock_project`)
- Test names: `test_<behaviour>_<condition>`, e.g. `test_setup_progress_disabled_in_non_tty`
- One behavioural assertion focus per test; state the expected behaviour in a one-line docstring
- A test written for a bug fix SHOULD be shown to fail against the previous implementation - a
  regression test that passes both before and after the fix asserts nothing
- A test asserting corrected *wording* SHOULD assert the wrong form is absent as well as the
  right form present - a one-way assertion can pass on a substring
