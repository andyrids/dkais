"""Main entry point for the dkais CLI."""

import pyclig

from dkais import _cli, exceptions

DESCRIPTION = (
    "dkais downloads Danish Maritime Authority historical AIS archives from "
    "http://aisdata.ais.dk/ and converts them to Parquet, partitioned by YEAR "
    "and MONTH."
)


@pyclig.group(
    "dkais",
    help=DESCRIPTION,
    short_help="DMA AIS data manager",
    examples=[
        "dkais list --start 2026-10",
        "dkais download --start 2026-10 --data-dir D:/ais",
        "dkais process --start 2026-10 --data-dir D:/ais",
    ],
    errors=(exceptions.Error,),
    epilog="For more information see https://github.com/andyrids/dkais.",
)
def cli() -> None:
    """Provide a unified `dkais` CLI entrypoint - list, download, process."""


for command in (
    _cli.command_list,
    _cli.command_download,
    _cli.command_process,
):
    cli.add_command(command)


if __name__ == "__main__":
    cli()
