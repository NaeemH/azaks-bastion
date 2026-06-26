"""Unit tests for the `az`-shelling helpers in azaks_bastion.bastion."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from pytest_mock import MockerFixture

from azaks_bastion import bastion
from azaks_bastion.errors import BastionAccessError, TargetNotFoundError

ARM_ID = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/jump"


def test_list_bastions_parses_array(fake_az: Any, fixture_loader: Any) -> None:
    fake_az([fixture_loader("bastion-list")])
    bastions = bastion.list_bastions()
    assert [b["name"] for b in bastions] == ["prod-hub-bastion", "prod-hub-bastion-weu"]


def test_list_bastions_missing_az(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value=None)
    with pytest.raises(BastionAccessError, match="az` CLI not found"):
        bastion.list_bastions()


def test_list_bastions_az_nonzero(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value="/usr/bin/az")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: AuthorizationFailed"
        ),
    )
    with pytest.raises(BastionAccessError, match="AuthorizationFailed"):
        bastion.list_bastions()


def test_resolve_target_id_passthrough_for_arm_id() -> None:
    assert bastion.resolve_target_id(ARM_ID) == ARM_ID


def test_resolve_target_id_requires_rg_for_name() -> None:
    with pytest.raises(TargetNotFoundError, match="--target-rg"):
        bastion.resolve_target_id("jump")


def test_resolve_target_id_resolves_vm_name(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value="/usr/bin/az")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{ARM_ID}\n", stderr=""
        ),
    )
    assert bastion.resolve_target_id("jump", resource_group="rg") == ARM_ID


def test_resolve_target_id_vm_not_found(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value="/usr/bin/az")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="ERROR: ResourceNotFound"
        ),
    )
    with pytest.raises(TargetNotFoundError, match="could not resolve VM 'jump'"):
        bastion.resolve_target_id("jump", resource_group="rg")


def test_build_tunnel_argv_minimal() -> None:
    argv = bastion.build_tunnel_argv(
        bastion="hub",
        resource_group="hub-rg",
        target_id=ARM_ID,
        resource_port=22,
        local_port=2222,
    )
    assert argv == [
        "network",
        "bastion",
        "tunnel",
        "--name",
        "hub",
        "--resource-group",
        "hub-rg",
        "--target-resource-id",
        ARM_ID,
        "--resource-port",
        "22",
        "--port",
        "2222",
    ]


def test_build_tunnel_argv_with_subscription() -> None:
    argv = bastion.build_tunnel_argv(
        bastion="hub",
        resource_group="hub-rg",
        target_id=ARM_ID,
        resource_port=443,
        local_port=8443,
        subscription="my-sub",
    )
    assert argv[-2:] == ["--subscription", "my-sub"]
    assert "443" in argv and "8443" in argv


def test_open_tunnel_returns_exit_code(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value="/usr/bin/az")
    run = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    code = bastion.open_tunnel(
        bastion="hub",
        resource_group="hub-rg",
        target_id=ARM_ID,
        resource_port=22,
        local_port=2222,
    )
    assert code == 0
    argv = run.call_args.args[0]
    assert argv[0] == "/usr/bin/az"
    assert "tunnel" in argv


def test_free_local_port_in_range() -> None:
    port = bastion.free_local_port()
    assert 1024 <= port <= 65535
