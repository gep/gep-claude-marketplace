"""Fixtures of the destructive-guard tests."""

import pytest

from dg_testkit.jev_mock import JevMock
from dg_testkit.pythons import Pythons


@pytest.fixture
def old_python() -> str:
    python = Pythons.find_old_python()
    if python is None:
        pytest.skip("no Python < 3.14 available")
    return python


@pytest.fixture
def jev():
    """A local Jev mock; `jev.url`, `jev.answer(...)`, `jev.bodies`, `jev.hits`."""
    with JevMock() as mock:
        yield mock


@pytest.fixture
def clef():
    """A second local mock standing in for Clef (same request and answer shape as Jev)."""
    with JevMock() as mock:
        yield mock
