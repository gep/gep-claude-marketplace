"""Live Jev tests: run only with `-m live_jev`; need TYPESAFE_API_KEY (CI sets LIVE_JEV_REQUIRED=1)."""

import os

import pytest

from dg_testkit.envs import IsolatedEnv
from dg_testkit.live_jev import LiveJev


@pytest.fixture
def live_env(tmp_path):
    """A clean env plus the real TYPESAFE_API_KEY, a generous timeout and logs in tmp_path; Jev alone (Clef off)."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        if os.environ.get("LIVE_JEV_REQUIRED") == "1":
            pytest.fail("TYPESAFE_API_KEY not set but LIVE_JEV_REQUIRED=1")
        pytest.skip("TYPESAFE_API_KEY not set")
    return IsolatedEnv.clean(
        {
            "TYPESAFE_API_KEY": key,
            "GATE_JEV_TIMEOUT_MS": "10000",
            "GATE_LOG": str(tmp_path / "gate.log"),
            "GATE_CLEF_ENABLED": "0",
        }
    )


def pytest_terminal_summary(terminalreporter):
    if not LiveJev.rows:
        return
    text = LiveJev.markdown()
    terminalreporter.write_line("")
    terminalreporter.write_line(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
