"""Run simulated `dkais download` & `dkais process` output styles.

Each style is a module in this directory exposing `download(ctx)` and
`process(ctx)`; see `_sim.py` for the shared harness. Nothing touches S3, the
network or a data directory.

USAGE:
    uv run python scripts/cli_styles/demo.py <style> [--phase both]
    uv run python scripts/cli_styles/demo.py --all [--speed 2]
    uv run python scripts/cli_styles/demo.py --list

OPTIONS:
    --phase      download, process or both (default: both)
    --speed      Simulation speed multiplier (default: 1)
    --width      Fixed console width (default: terminal width)
    --no-color   Disable colour, as with NO_COLOR
    --no-fail    Skip the injected download & process failures
    --record     Write final frames as text to DIR/<style>.txt
"""

import argparse
import importlib
import sys
from pathlib import Path

import _sim

STYLES = ("minimal", "cargo")


def run_style(name: str, args: argparse.Namespace) -> int:
    """Run one style's phases, returning the highest exit code.

    Args:
        name: The style module name.
        args: The parsed CLI arguments.

    Returns:
        The highest exit code over the phases run.
    """
    style = importlib.import_module(name)
    err, out = _sim.make_consoles(
        args.width, no_color=args.no_color, record=bool(args.record)
    )
    phases = ("download", "process") if args.phase == "both" else (args.phase,)
    code = 0
    ctx = _sim.make_context(err, out, speed=args.speed, fail=not args.no_fail)
    for phase in phases:
        # Harness chrome, standing in for the shell prompt between commands
        err.print(
            f"\n[dim]$ dkais {phase} --start {ctx.start} --end {ctx.end}[/]"
        )
        try:
            code = max(code, int(getattr(style, phase)(ctx)))
        except KeyboardInterrupt:
            # A style that lets Ctrl-C escape fails the demo contract
            err.print("[red]KeyboardInterrupt escaped the style")
            return 130
    if args.record:
        _sim.save_record(ctx, Path(args.record) / f"{name}.txt")
    return code


def main() -> int:
    """Parse arguments & run the selected styles.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("style", nargs="?", choices=STYLES)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--phase", choices=("download", "process", "both"), default="both"
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--width", type=int)
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--no-fail", action="store_true")
    parser.add_argument("--record", metavar="DIR")
    args = parser.parse_args()

    # Redirected streams on Windows default to cp1252, which can't encode the
    # styles' glyphs (the real CLI shares this, see the hand-off notes)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.list:
        sys.stdout.write("\n".join(STYLES) + "\n")
        return 0
    if not (args.all or args.style):
        parser.error("give a style, --all or --list")

    names = STYLES if args.all else (args.style,)
    pause = args.all and sys.stdin.isatty() and not args.record
    code = 0
    for i, name in enumerate(names):
        if args.all:
            sys.stderr.write(f"\n\n===== {name} =====\n")
        code = max(code, run_style(name, args))
        if pause and i < len(names) - 1:
            input("\nPress Enter for the next style...")
    return code


if __name__ == "__main__":
    sys.exit(main())
