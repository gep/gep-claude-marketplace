"""UserPromptSubmit gate (the Lean `promptGate`): block iff no backend is usable; never contacts a backend."""

import json
import os
import shutil
import subprocess

import pytest

from dg_testkit import DG
from dg_testkit.envs import IsolatedEnv
from dg_testkit.fakebin import FakeBin
from dg_testkit.hook import HookRunner
from dg_testkit.jev_mock import JevMock
from dg_testkit.pythons import Pythons

OK = {"TYPESAFE_API_KEY": "k"}
HALTED = {"GATE_CLEF_ENABLED": "0"}  # no key either


def test_usable_backend_passes_silently():
    r = HookRunner.run_prompt(OK)
    assert (r.status, r.stdout) == (0, "")


def test_clef_alone_is_enough():
    assert HookRunner.run_prompt().stdout == ""


def test_halted_blocks_with_the_halt_text():
    r = HookRunner.run_prompt(HALTED)
    assert r.status == 0
    assert json.loads(r.stdout) == {
        "decision": "block",
        "reason": "destructive-guard: no classifier available (clef: disabled by GATE_CLEF_ENABLED=0; "
        "jev: TYPESAFE_API_KEY is not set): this session is halted. Fix the setting or set GATE_ENABLED=0.",
    }


def test_block_reason_is_the_session_start_text():
    session = json.loads(HookRunner.run_session(HALTED).stdout)["systemMessage"]
    assert json.loads(HookRunner.run_prompt(HALTED).stdout)["reason"] == session


def test_bad_flag_blocks():
    assert json.loads(HookRunner.run_prompt(OK, {"GATE_JEV_ENABLED": "maybe"}).stdout)["decision"] == "block"


def test_one_bad_backend_is_not_a_halt():
    assert HookRunner.run_prompt(OK, {"GATE_CLEF_URL": "http://example.com"}).stdout == ""


@pytest.mark.parametrize("value", ["0", "false", "OFF"])
def test_gate_disabled_is_silent(value):
    assert HookRunner.run_prompt(HALTED, {"GATE_ENABLED": value}).stdout == ""


def test_never_contacts_a_backend(jev: JevMock):
    HookRunner.run_prompt(OK, {"GATE_JEV_URL": jev.url})
    assert jev.hits == 0


def test_odd_stdin_is_ignored():
    proc = subprocess.run(
        ["bash", str(DG / "hooks" / "prompt-gate.sh")],
        input=b"\xff not json",
        capture_output=True,
        env=IsolatedEnv.clean({"GATE_INFRA_PYTHON": Pythons.require_new_python(os.environ)}, HALTED),
        check=False,
    )
    assert json.loads(proc.stdout)["decision"] == "block"


# Interpreter lookup, like gate.sh: GATE_INFRA_PYTHON, python3.14, python3, always -I. Faults stay silent.
SH = DG / "hooks" / "prompt-gate.sh"


def _bin(tmp_path) -> FakeBin:
    fake = FakeBin(tmp_path)
    for tool in ("dirname", "tr", "touch"):
        fake.link(tool)
    return fake


def _wrapper(fake: FakeBin, name: str, python: str, marker=None):
    body = f'touch "{marker}"\n' if marker else ""
    path = fake.path / name
    path.write_text(f'#!/bin/sh\n{body}exec "{python}" "$@"\n')
    path.chmod(0o755)


def _run(fake: FakeBin, cwd=None, **env) -> str:
    proc = subprocess.run(
        [shutil.which("bash"), str(SH)],
        input=b"{}",
        capture_output=True,
        env=IsolatedEnv.clean({"PATH": str(fake.path)}, HALTED, env),
        cwd=cwd,
        check=False,
    )
    assert proc.returncode == 0
    return proc.stdout.decode()


def test_no_python_is_silent(tmp_path):
    assert _run(_bin(tmp_path)) == ""


def test_old_python_is_silent(tmp_path, old_python):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", old_python)
    assert _run(fake) == ""


def test_missing_explicit_interpreter_is_silent_without_fallback(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    assert _run(fake, GATE_INFRA_PYTHON="/nope") == ""


def test_broken_interpreter_is_silent(tmp_path):
    fake = _bin(tmp_path)
    broken = fake.path / "python3"
    broken.write_text("#!/bin/sh\necho partial\nexit 1\n")
    broken.chmod(0o755)
    assert _run(fake) == ""


def test_python314_preferred_over_python3(tmp_path, old_python):
    fake = _bin(tmp_path)
    ran, other = tmp_path / "ran-314", tmp_path / "ran-3"
    _wrapper(fake, "python3.14", Pythons.require_new_python(os.environ), marker=ran)
    _wrapper(fake, "python3", old_python, marker=other)
    assert json.loads(_run(fake))["decision"] == "block"
    assert ran.exists()
    assert not other.exists()


def test_python3_used_when_no_python314(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    assert json.loads(_run(fake))["decision"] == "block"


def test_planted_json_module_is_ignored(tmp_path):
    fake = _bin(tmp_path)
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ))
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "json.py").write_text("raise SystemExit(0)\n")
    assert json.loads(_run(fake, cwd=planted, PYTHONPATH=str(planted)))["decision"] == "block"


def test_gate_disabled_starts_no_interpreter(tmp_path):
    fake = _bin(tmp_path)
    ran = tmp_path / "ran"
    _wrapper(fake, "python3", Pythons.require_new_python(os.environ), marker=ran)
    assert _run(fake, GATE_ENABLED="0") == ""
    assert not ran.exists()
