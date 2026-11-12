[unix]
set shell := ["bash", "-euo", "pipefail", "-c"]

[windows]
set shell := ["pwsh.exe", "-c"]

set dotenv-load := true

[default]
@_:
    just --list

[doc("Setup development environment, pre-commit hooks & secrets baseline")]
[group("DEV")]
setup:
    uv sync
    uv run pkgdx init

[doc("Generate a `detect-secrets` baseline for the repository")]
[group("DEV")]
secrets-baseline:
    echo "NOTE: Run this once after initial setup & re-run after intentionally adding secrets to the codebase (e.g. test fixtures)."
    uv run detect-secrets scan --exclude-files "(\.venv|\.secrets\.baseline|.*\.lock)" > .secrets.baseline

[doc("Create `coverage` report")]
[group("DEV")]
coverage *FLAGS:
    @uv run coverage run -m pytest {{FLAGS}}
    @uv run coverage report --show-missing --skip-covered --fail-under=75
    @uv run coverage xml
    @uv run coverage html

[doc("Git prune (aggressive)")]
[group("DEV")]
git-prune:
    git gc --prune=now --aggressive

[doc("Symlink `AGENTS.md` -> `CLAUDE.md`")]
[group("DEV")]
symlink-agents:
    @uv run python -c "import pathlib; p=pathlib.Path('CLAUDE.md'); p.unlink(missing_ok=True); p.symlink_to('AGENTS.md')"

[doc("Read & check Parquet files in `$DKAIS_DATA_DIR`")]
[group("TEST")]
test-processed:
    uv run ./scripts/read_parquet.py

[doc("Demo `dkais` CLI")]
[group("DEMO")]
demo:
    @echo '{{CLEAR}}'
    @echo '{{ BOLD + CYAN + INVERT + "--- dkais demo ---\n" + NORMAL }}'
    @sleep 1

    @echo '{{ BOLD + CYAN + "LIST - query remote AIS data\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais list --data-dir D:/Data/AISDK --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais list --data-dir 'D:\Data\AISDK' --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "LIST - can use $DKAIS_DATA_DIR for local data dir\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + '$ DKAIS_DATA_DIR=D:/Data/AISDK' + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais list --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais list --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "DOWNLOAD - download remote AIS data\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais download --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais download --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "DOWNLOAD - is idempotent (without --force)\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais download --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais download --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "PROCESS - process downloaded CSV into Parquet\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais process --all --dry-run" + NORMAL }}'
    @sleep 1
    @uv run dkais process --all --dry-run
    @sleep 2

    @echo ""
    @echo '{{ GREEN + ITALIC + "$ dkais process --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais process --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "PROCESS - is idempotent (without --force)\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais process --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais process --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @echo '{{ BOLD + CYAN + "LIST - highlights Raw & Processed data in --data-dir \\ $DKAIS_DATA_DIR\n" + NORMAL }}'
    @echo '{{ GREEN + ITALIC + "$ dkais list --start 2026-10-01 --end 2026-10-02" + NORMAL }}'
    @sleep 1
    @uv run dkais list --start 2026-10-01 --end 2026-10-02
    @sleep 2

    @echo ""
    @uv run scripts/read_parquet.py

    @echo ""
    @echo '{{ BOLD + CYAN + INVERT + "--- Test dkais in Colab - docs/example/DKAIS_EXAMPLE.ipynb ---\n" + NORMAL }}'
