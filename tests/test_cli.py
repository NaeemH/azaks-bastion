"""Smoke tests for the CLI surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from azaks_bastion import __version__
from azaks_bastion.cli import _path_hint, app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "azaks-bastion" in result.stdout
    assert "list" in result.stdout
    assert "tunnel" in result.stdout


def test_list_renders_table(fake_az: Any, fixture_loader: Any) -> None:
    fake_az([fixture_loader("bastion-list")])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "prod-hub-bastion" in result.stdout
    assert "northeurope" in result.stdout


def test_list_json_emits_summaries(fake_az: Any, fixture_loader: Any) -> None:
    fake_az([fixture_loader("bastion-list")])
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["name"] == "prod-hub-bastion"
    assert payload[0]["resource_group"] == "prod-hub-neu-rg"
    assert payload[0]["sku"] == "Standard"


def test_list_empty_is_friendly(fake_az: Any, fixture_loader: Any) -> None:
    fake_az([fixture_loader("bastion-list-empty")])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "no Bastion hosts" in result.output


def test_list_surfaces_az_failure(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value=None)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 2
    assert "az` CLI not found" in result.output


def test_tunnel_happy_path_with_arm_id(mocker: MockerFixture) -> None:
    open_tunnel = mocker.patch("azaks_bastion.cli.open_tunnel", return_value=0)
    target_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/jump"
    result = runner.invoke(
        app,
        [
            "tunnel",
            "--bastion",
            "hub",
            "--bastion-rg",
            "hub-rg",
            "--target",
            target_id,
            "--local-port",
            "2222",
            "--resource-port",
            "22",
        ],
    )
    assert result.exit_code == 0
    assert "127.0.0.1:2222" in result.output
    _, kwargs = open_tunnel.call_args
    assert kwargs["target_id"] == target_id
    assert kwargs["local_port"] == 2222


def test_tunnel_resolves_vm_name(mocker: MockerFixture) -> None:
    resolve = mocker.patch(
        "azaks_bastion.cli.resolve_target_id",
        return_value="/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/jump",
    )
    mocker.patch("azaks_bastion.cli.open_tunnel", return_value=0)
    mocker.patch("azaks_bastion.cli.free_local_port", return_value=5555)
    result = runner.invoke(
        app,
        ["tunnel", "-b", "hub", "--bastion-rg", "hub-rg", "-t", "jump", "--target-rg", "rg"],
    )
    assert result.exit_code == 0
    assert "127.0.0.1:5555" in result.output
    resolve.assert_called_once()


def test_tunnel_propagates_nonzero_exit(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.cli.open_tunnel", return_value=1)
    result = runner.invoke(
        app,
        [
            "tunnel",
            "-b",
            "hub",
            "--bastion-rg",
            "hub-rg",
            "-t",
            "/subscriptions/x/vm",
            "-l",
            "2222",
        ],
    )
    assert result.exit_code == 1
    assert "exited with status 1" in result.output


def test_tunnel_surfaces_resolve_error(mocker: MockerFixture) -> None:
    mocker.patch("azaks_bastion.bastion.shutil.which", return_value=None)
    result = runner.invoke(
        app,
        ["tunnel", "-b", "hub", "--bastion-rg", "hub-rg", "-t", "jump", "--target-rg", "rg"],
    )
    assert result.exit_code == 2
    assert "az` CLI not found" in result.output


def test_list_derives_rg_from_id(fake_az: Any) -> None:
    fake_az(
        [
            [
                {
                    "name": "id-only-bastion",
                    "id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/derived-rg/providers/Microsoft.Network/bastionHosts/id-only-bastion",
                    "location": "eastus",
                    "sku": {"name": "Standard"},
                }
            ]
        ]
    )
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["resource_group"] == "derived-rg"


@pytest.fixture()
def fake_local_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake HOME whose ~/.local/bin holds an installed console script."""
    monkeypatch.setenv("HOME", str(tmp_path))
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "azaks-bastion").write_text("#!/bin/sh\n")
    return local_bin


def test_path_hint_when_local_bin_missing_from_path(fake_local_bin: Path) -> None:
    hint = _path_hint("/usr/bin:/bin")
    assert hint is not None
    assert "pipx ensurepath" in hint
    assert str(fake_local_bin) in hint


def test_path_hint_silent_when_local_bin_on_path(fake_local_bin: Path) -> None:
    path_env = os.pathsep.join(["/usr/bin", str(fake_local_bin)])
    assert _path_hint(path_env) is None


def test_path_hint_matches_unexpanded_tilde_entry(fake_local_bin: Path) -> None:
    """A literal ``~/.local/bin`` PATH entry should count as present."""
    assert _path_hint(os.pathsep.join(["/usr/bin", "~/.local/bin"])) is None


def test_path_hint_none_when_not_installed_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    assert _path_hint("/usr/bin") is None


def test_root_prints_hint_to_output(
    fake_local_bin: Path, fake_az: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    fake_az([[]])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "pipx ensurepath" in " ".join(result.output.split())
