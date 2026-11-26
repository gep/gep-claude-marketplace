"""hooks/gate.sh interpreter lookup (D1a/D1b): GATE_INFRA_PYTHON, python3.14, python3, always -I; plugin faults ask (D12)."""

import json
import os
import shutil
import subprocess

import pytest

from dg_testkit import DG
from dg_testkit.envs import IsolatedEnv
from dg_testkit.fakebin import FakeBin
from dg_testkit.hook import HookRunner
from dg_testkit.pythons import Pythons

GATE_SH = DG / "hooks" / "gate.sh"
NO_PYTHON = "plugin-fault: no Python >= 3.14 found (set GATE_INFRA_PYTHON)"
ENGINE_FAILED = "plugin-fault: internal error: engine exit 1"
# Defaults, no key: the engine ran; Clef is usable but no local Clef answers (tests point it at a closed
# port), and Jev has no key, so the call asks.
RAN = "clef-unavailable: network error"


def _bin(tmp_path) -> FakeBin:
    """A private PATH with only the tools gate.sh itself needs."""
    fake = FakeBin(tmp_path)
    for tool in ("dirname", "tr", "touch"):
        fake.link(tool)
    return fake


def _wrapper(fake: FakeBin, name: str, python: str, marker=None):
    body = f'touch "{marker}"\n' if marker else ""
    path = fake.path / name
    path.write_text(f'#!/bin/sh\n{body}exec "{python}" "$@"\n')
    path.chmod(0o755)
    return path


def _run(fake: FakeBin, command="git push -f", cwd=None, **env) -> str:
    """"" for no output, else the reason the hook asked with."""
    proc = subprocess.run(
        [shutil.which("bash"), str(GATE_SH)],
        input=HookRunner.bash_input(command, cwd or fake.path).encode(),
        capture_output=True,
        env=IsolatedEnv.clean({"PATH": str(fake.path)}, env),
        cwd=cwd,
        check=False,
    )
    assert proc.returncode == 0
    out = proc.stdout.decode()
    if not out:
        return ""
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    return decision["permissionDecisionReason"]


def test_old_python3_asks_no_python(tmp_path, old_python):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", old_python)
    assert _run(fake) == NO_PYTHON


def test_missing_explicit_interpreter_asks_without_falling_back(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    assert _run(fake, GATE_INFRA_PYTHON="/nope") == NO_PYTHON


def test_no_python_on_path_asks(tmp_path):
    assert _run(_bin(tmp_path)) == NO_PYTHON


def test_python314_preferred_over_python3(tmp_path, old_python):
    fake = _bin(tmp_path)
    new = Pythons.require_new_python(os.environ)
    ran = tmp_path / "ran-python3.14"
    other = tmp_path / "ran-python3"
    _wrapper(fake, "python3.14", new, marker=ran)
    _wrapper(fake, "python3", old_python, marker=other)
    assert _run(fake) == RAN
    assert ran.exists()
    assert not other.exists()


def test_python3_used_when_no_python314(tmp_path):
    fake = _bin(tmp_path)
    ran = tmp_path / "ran"
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ), marker=ran)
    assert _run(fake) == RAN
    assert ran.exists()


def test_broken_python3_first_on_path_asks_engine_failed(tmp_path):
    fake = _bin(tmp_path)
    broken = fake.path / "python3"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    assert _run(fake) == ENGINE_FAILED


def test_explicit_interpreter_is_used_even_if_broken(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    broken = tmp_path / "broken"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    assert _run(fake, GATE_INFRA_PYTHON=str(broken)) == ENGINE_FAILED


def test_planted_json_module_does_not_change_the_decision(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "json.py").write_text("raise SystemExit(0)\n")
    assert _run(fake, cwd=planted) == RAN
    assert _run(fake, cwd=planted, PYTHONPATH=str(planted)) == RAN


@pytest.mark.parametrize("value", ["0", "false", "OFF", "no"])
def test_gate_disabled_starts_no_interpreter(tmp_path, value):
    fake = _bin(tmp_path)
    ran = tmp_path / "ran"
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ), marker=ran)
    assert _run(fake, GATE_ENABLED=value) == ""
    assert not ran.exists()
