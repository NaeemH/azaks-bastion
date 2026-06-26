"""Shared pytest fixtures for azaks-bastion.

These mirror the patterns used across the azops-cli series so a new tool gets
zero-network, zero-credential CLI testing out of the box:

- ``fake_az``       -- queue JSON payloads to be returned by successive ``az``
                       calls (patches ``shutil.which`` + ``subprocess.run``).
- ``fixture_loader``-- load a sanitized JSON fixture from ``tests/fixtures/``,
                       asserting no real GUIDs leaked in.

Delete or adapt whatever your tool doesn't need.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Defense-in-depth: a careless fixture re-record must not leak a real
# subscription / tenant GUID. Only the all-zero scrubbed GUID is allowed.
GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
SCRUBBED_GUID = "00000000-0000-0000-0000-000000000000"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture by base name from ``tests/fixtures/``."""
    path = FIXTURES_DIR / f"{name}.json"
    text = path.read_text(encoding="utf-8")
    for match in GUID_RE.findall(text):
        if match.lower() != SCRUBBED_GUID:
            raise AssertionError(
                f"Fixture {name!r} contains unscrubbed GUID {match!r}. "
                f"Re-record and re-scrub before committing."
            )
    return json.loads(text)


@pytest.fixture
def fixture_loader() -> Any:
    """Test-injectable handle to :func:`load_fixture`."""
    return load_fixture


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def fake_az(mocker: MockerFixture) -> Any:
    """Patch ``az`` discovery + ``subprocess.run`` to return queued JSON payloads.

    Usage::

        def test_something(fake_az, fixture_loader):
            fake_az([fixture_loader("pool-show"), fixture_loader("list-empty")])
            ...

    Each call to the patched ``subprocess.run`` consumes the next queued payload.
    If your tool binds ``subprocess``/``shutil`` in a dedicated module, point the
    patch targets at that module instead (e.g. ``azaks_bastion.azcli.subprocess.run``).
    """
    mocker.patch("shutil.which", return_value="/usr/bin/az")
    queue: list[str] = []

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if not queue:
            raise AssertionError(
                "fake_az() ran out of queued payloads -- did the code under test "
                "make more `az` calls than the test expected?"
            )
        return _completed(queue.pop(0))

    mocker.patch("subprocess.run", side_effect=fake_run)

    def enqueue(payloads: list[Any]) -> None:
        queue.extend(json.dumps(p) for p in payloads)

    return enqueue
