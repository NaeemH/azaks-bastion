"""Typer CLI: `azaks-bastion` (alias `aksb`).

Open an Azure Bastion tunnel to a private AKS API server or jump host.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from azaks_bastion import __version__
from azaks_bastion.bastion import (
    free_local_port,
    list_bastions,
    open_tunnel,
    resolve_target_id,
)
from azaks_bastion.errors import AzaksBastionError

app = typer.Typer(
    name="azaks-bastion",
    help="Open an Azure Bastion tunnel to a private AKS API server or jump host.",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)
stdout = Console()
stderr = Console(stderr=True)


def _path_hint(path_env: str | None) -> str | None:
    """Return a one-line `pipx ensurepath` hint, or None.

    Fires only when our console scripts are installed in ``~/.local/bin`` but
    that directory is not on ``PATH`` (e.g. the user invoked us via full path
    or ``python -m`` and would otherwise hit ``command not found``).
    """
    local_bin = Path.home() / ".local" / "bin"
    if not ((local_bin / "azaks-bastion").exists() or (local_bin / "aksb").exists()):
        return None
    entries = {
        os.path.normpath(os.path.expanduser(p)) for p in (path_env or "").split(os.pathsep) if p
    }
    if os.path.normpath(str(local_bin)) in entries:
        return None
    return (
        f"hint: {local_bin} is not on your PATH, so `azaks-bastion`/`aksb` may "
        "not be found. Run `pipx ensurepath` and restart your shell."
    )


def _version_callback(value: bool) -> None:
    if value:
        stdout.print(f"azaks-bastion [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def _root(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Common options."""
    hint = _path_hint(os.environ.get("PATH"))
    if hint:
        stderr.print(f"[dim]{hint}[/dim]", soft_wrap=True, highlight=False)


# --------------------------------------------------------------- option types ----
SubscriptionOpt = Annotated[
    str | None,
    typer.Option(
        "--subscription",
        "-s",
        envvar="AZURE_SUBSCRIPTION_ID",
        help="Azure subscription id or name. Falls back to [bold]AZURE_SUBSCRIPTION_ID[/bold].",
    ),
]
JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable JSON instead of a table."),
]
NoTruncateOpt = Annotated[
    bool,
    typer.Option(
        "--no-truncate",
        help="Render full column values without ellipsis (implied when output is piped).",
    ),
]


def _bastion_resource_group(bastion: dict[str, Any]) -> str:
    """Best-effort resource group for a Bastion host record."""
    rg = bastion.get("resourceGroup")
    if rg:
        return str(rg)
    arm_id = str(bastion.get("id", ""))
    parts = arm_id.split("/")
    for i, segment in enumerate(parts):
        if segment.lower() == "resourcegroups" and i + 1 < len(parts):
            return parts[i + 1]
    return "-"


def _summarize(bastion: dict[str, Any]) -> dict[str, str]:
    """Reduce a raw Bastion host record to the columns we render."""
    return {
        "name": str(bastion.get("name", "-")),
        "resource_group": _bastion_resource_group(bastion),
        "location": str(bastion.get("location", "-")),
        "sku": str((bastion.get("sku") or {}).get("name", "-")),
    }


# ----------------------------------------------------------------- list ----
@app.command("list")
def cmd_list(
    subscription: SubscriptionOpt = None,
    as_json: JsonOpt = False,
    no_truncate: NoTruncateOpt = False,
) -> None:
    """List Azure Bastion hosts visible in the subscription.

    Use this to find a Standard-SKU Bastion in (or peered to) your private
    cluster's VNet before opening a tunnel.
    """
    try:
        bastions = list_bastions(subscription=subscription)
    except AzaksBastionError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rows = [_summarize(b) for b in bastions]

    if as_json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        stderr.print("[yellow]no Bastion hosts found in this subscription.[/yellow]")
        return

    render = stdout if stdout.is_terminal and not no_truncate else Console(width=200)
    table = Table(title="Azure Bastion hosts")
    table.add_column("NAME", style="cyan", no_wrap=no_truncate or not stdout.is_terminal)
    table.add_column("RESOURCE GROUP")
    table.add_column("LOCATION")
    table.add_column("SKU")
    for row in rows:
        table.add_row(row["name"], row["resource_group"], row["location"], row["sku"])
    render.print(table)


# ----------------------------------------------------------------- tunnel ----
@app.command("tunnel")
def cmd_tunnel(
    bastion: Annotated[
        str,
        typer.Option("--bastion", "-b", help="Name of the Azure Bastion host."),
    ],
    bastion_rg: Annotated[
        str,
        typer.Option("--bastion-rg", help="Resource group containing the Bastion host."),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="Target ARM resource id, or a VM name (then pass [bold]--target-rg[/bold]).",
        ),
    ],
    target_rg: Annotated[
        str | None,
        typer.Option(
            "--target-rg", help="Resource group of the target VM (when --target is a name)."
        ),
    ] = None,
    resource_port: Annotated[
        int,
        typer.Option(
            "--resource-port",
            "-p",
            min=1,
            max=65535,
            help="Port on the target to forward (22 for an SSH jump host, 443 for an API server).",
        ),
    ] = 22,
    local_port: Annotated[
        int | None,
        typer.Option(
            "--local-port",
            "-l",
            min=1,
            max=65535,
            help="Local port to bind. Defaults to an OS-assigned free port.",
        ),
    ] = None,
    subscription: SubscriptionOpt = None,
) -> None:
    """Open an Azure Bastion tunnel to a target VM or private endpoint.

    Forwards [bold]127.0.0.1:LOCAL_PORT[/bold] to the target's
    [bold]RESOURCE_PORT[/bold] through the Bastion host. Requires a Standard-SKU
    Bastion with native-client support. Runs in the foreground until
    interrupted.
    """
    try:
        target_id = resolve_target_id(target, resource_group=target_rg, subscription=subscription)
        port = local_port or free_local_port()
        stdout.print(
            f"[green]opening tunnel[/green] via [cyan]{bastion}[/cyan] "
            f"-> [bold]127.0.0.1:{port}[/bold] -> target port [bold]{resource_port}[/bold]"
        )
        stdout.print("  [dim]press Ctrl-C to close the tunnel[/dim]")
        code = open_tunnel(
            bastion=bastion,
            resource_group=bastion_rg,
            target_id=target_id,
            resource_port=resource_port,
            local_port=port,
            subscription=subscription,
        )
    except AzaksBastionError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if code != 0:
        stderr.print(f"[red]error:[/red] bastion tunnel exited with status {code}")
        raise typer.Exit(code=code)
