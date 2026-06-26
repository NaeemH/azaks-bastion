"""Shell out to `az network bastion` for discovery + native-client tunnels.

Like the rest of the azops-cli series we deliberately avoid the Azure
management SDK: the `az` CLI already owns authentication, MSAL caching, and the
Bastion native-client tunnel transport. Shelling out keeps the tool small,
predictable, and pinned to whatever `az` the operator already trusts.

A typical flow to reach a *private* AKS API server:

1. ``list_bastions`` to find a Standard-SKU Bastion in (or peered to) the
   cluster's VNet.
2. ``resolve_target_id`` to turn a jump-host VM name into its ARM id.
3. ``open_tunnel`` to forward a local port to the target's port (22 for an SSH
   jump host, 443 for an IP-based tunnel straight to the private API server).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from typing import Any, cast

from azaks_bastion.errors import BastionAccessError, TargetNotFoundError

ARM_ID_PREFIX = "/subscriptions/"


def _az() -> str:
    """Return the path to the `az` CLI or raise if it is not installed."""
    az = shutil.which("az")
    if az is None:
        raise BastionAccessError(
            "`az` CLI not found on PATH. Install Azure CLI: https://aka.ms/azcli"
        )
    return az


def _condense_az_stderr(stderr: str) -> str:
    """Reduce raw ``az`` stderr to its salient ``ERROR:`` line(s).

    ``az`` prefixes genuine error lines with ``ERROR:`` and may append a
    multi-line Python traceback when it crashes. We surface only the ``ERROR:``
    lines; if none are present we fall back to the first non-empty line so the
    caller still gets something actionable.
    """
    lines = stderr.splitlines()
    error_lines = [ln.strip() for ln in lines if ln.strip().upper().startswith("ERROR:")]
    if error_lines:
        return " ".join(error_lines)
    for ln in lines:
        if ln.strip():
            return ln.strip()
    return ""


def _run_az_json(argv: list[str], *, what: str) -> Any:
    """Run ``az <argv> -o json`` and return the parsed payload.

    Raises:
        BastionAccessError: `az` is missing or the invocation failed / emitted
            non-JSON.
    """
    result = subprocess.run(
        [_az(), *argv, "--only-show-errors", "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = _condense_az_stderr(result.stderr or "")
        raise BastionAccessError(
            f"{what} failed (exit {result.returncode}): {detail or '(no stderr)'}"
        )
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise BastionAccessError(f"{what} produced invalid JSON: {exc}") from exc


def list_bastions(*, subscription: str | None = None) -> list[dict[str, Any]]:
    """Return the Bastion hosts visible in the current (or named) subscription."""
    argv = ["network", "bastion", "list"]
    if subscription:
        argv += ["--subscription", subscription]
    data = _run_az_json(argv, what="`az network bastion list`")
    if not isinstance(data, list):
        raise BastionAccessError("`az network bastion list` did not return a JSON array")
    return cast(list[dict[str, Any]], data)


def resolve_target_id(
    target: str,
    *,
    resource_group: str | None = None,
    subscription: str | None = None,
) -> str:
    """Resolve ``target`` to a full ARM resource id.

    If ``target`` already looks like an ARM id it is returned unchanged.
    Otherwise it is treated as a VM name and resolved via ``az vm show``, which
    requires ``resource_group``.

    Raises:
        TargetNotFoundError: a VM name was given without a resource group, or
            the VM does not exist.
        BastionAccessError: `az` could not be invoked.
    """
    if target.startswith(ARM_ID_PREFIX):
        return target
    if not resource_group:
        raise TargetNotFoundError(
            f"target {target!r} is not an ARM resource id; pass --target-rg to "
            "resolve it as a VM name."
        )
    argv = ["vm", "show", "--name", target, "--resource-group", resource_group, "--query", "id"]
    if subscription:
        argv += ["--subscription", subscription]
    result = subprocess.run(
        [_az(), *argv, "--only-show-errors", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = _condense_az_stderr(result.stderr or "")
        raise TargetNotFoundError(
            f"could not resolve VM {target!r} in resource group {resource_group!r}: "
            f"{detail or '(no detail)'}"
        )
    resource_id = (result.stdout or "").strip()
    if not resource_id:
        raise TargetNotFoundError(
            f"VM {target!r} in resource group {resource_group!r} has no resource id"
        )
    return resource_id


def build_tunnel_argv(
    *,
    bastion: str,
    resource_group: str,
    target_id: str,
    resource_port: int,
    local_port: int,
    subscription: str | None = None,
) -> list[str]:
    """Build the ``az network bastion tunnel`` argument vector (sans the binary).

    Factored out as a pure function so it can be asserted on without spawning a
    real, blocking tunnel.
    """
    argv = [
        "network",
        "bastion",
        "tunnel",
        "--name",
        bastion,
        "--resource-group",
        resource_group,
        "--target-resource-id",
        target_id,
        "--resource-port",
        str(resource_port),
        "--port",
        str(local_port),
    ]
    if subscription:
        argv += ["--subscription", subscription]
    return argv


def open_tunnel(
    *,
    bastion: str,
    resource_group: str,
    target_id: str,
    resource_port: int,
    local_port: int,
    subscription: str | None = None,
) -> int:
    """Open a Bastion tunnel in the foreground, returning ``az``'s exit code.

    This blocks until the operator interrupts it (Ctrl-C) or the tunnel drops;
    stdio is inherited so the user sees `az`'s own progress output.
    """
    argv = [
        _az(),
        *build_tunnel_argv(
            bastion=bastion,
            resource_group=resource_group,
            target_id=target_id,
            resource_port=resource_port,
            local_port=local_port,
            subscription=subscription,
        ),
    ]
    result = subprocess.run(argv, check=False)
    return result.returncode


def free_local_port() -> int:
    """Return an unused local TCP port chosen by the OS.

    Binds ``127.0.0.1:0`` and reads back the assigned port. There is an
    inherent (small) TOCTOU window between this returning and `az` binding the
    port, which is acceptable for an interactive operator tool.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])
