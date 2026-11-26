"""SessionStart: one warning about a misconfigured setting (D3); never contacts Jev."""

import json
import os
import shutil
import subprocess

import pytest

from destructive_guard.session import SessionCheck
from dg_testkit import DG
from dg_testkit.envs import IsolatedEnv
from dg_testkit.fakebin import FakeBin
from dg_testkit.hook import HookRunner
from dg_testkit.jev_mock import JevMock

OK = {"TYPESAFE_API_KEY": "k"}
HALT_TAIL = "): this session is halted. Fix the setting or set GATE_ENABLED=0."


def message(*env, cwd=None):
    r = HookRunner.run_session(*env, cwd=cwd)
    assert r.status == 0
    return json.loads(r.stdout)["systemMessage"] if r.stdout else None


def test_both_ok_prints_nothing():
    assert HookRunner.run_session(OK).stdout == ""


def test_no_key_only_jev_is_skipped():
    # Clef is usable (default URL), Jev has no key: skipped and named, not halted.
    r = HookRunner.run_session()
    assert json.loads(r.stdout) == {
        "systemMessage": "destructive-guard: jev: TYPESAFE_API_KEY is not set (skipped this session)"
    }


def test_clef_bad_is_listed_not_halted():
    r = HookRunner.run_session(OK, {"GATE_CLEF_URL": "http://example.com"})
    assert json.loads(r.stdout) == {
        "systemMessage": "destructive-guard: clef: GATE_CLEF_URL must use https (skipped this session)"
    }


def test_jev_bad_url_is_listed():
    assert message(OK, {"GATE_JEV_URL": "http://example.com"}) == (
        "destructive-guard: jev: GATE_JEV_URL must use https (skipped this session)"
    )


def test_both_bad_listed_is_a_halt():
    r = HookRunner.run_session({"GATE_CLEF_URL": "http://example.com"})
    text = json.loads(r.stdout)["systemMessage"]
    assert text == (
        "destructive-guard: no classifier available (clef: GATE_CLEF_URL must use https; "
        "jev: TYPESAFE_API_KEY is not set" + HALT_TAIL
    )


def test_halt_has_the_exact_text_and_stops():
    r = HookRunner.run_session({"GATE_CLEF_ENABLED": "0"})
    out = json.loads(r.stdout)
    text = (
        "destructive-guard: no classifier available (clef: disabled by GATE_CLEF_ENABLED=0; "
        "jev: TYPESAFE_API_KEY is not set" + HALT_TAIL
    )
    assert out == {"continue": False, "stopReason": text, "systemMessage": text}


def test_halt_text_is_the_hooks_text():
    env = {"GATE_CLEF_ENABLED": "0", "GATE_JEV_ENABLED": "0"}
    hook = HookRunner.run(HookRunner.bash_input("ls", "."), env)
    assert json.loads(hook.stdout)["systemMessage"] == message(env)


def test_bad_flag_halts():
    msg = message(OK, {"GATE_CLEF_ENABLED": "maybe"})
    assert msg.startswith("destructive-guard: no classifier available (GATE_CLEF_ENABLED must be")
    assert msg.endswith(HALT_TAIL)
    assert message(OK, {"GATE_ENABLED": "maybe"}).endswith(HALT_TAIL)


def test_bad_shared_value_halts():
    msg = message(OK, {"GATE_JEV_ESCALATE_THRESHOLD": "7"})
    assert msg.endswith(HALT_TAIL)
    assert "clef: GATE_JEV_ESCALATE_THRESHOLD" in msg
    assert "jev: GATE_JEV_ESCALATE_THRESHOLD" in msg


def test_bad_prompt_file_halts_with_both_listed(tmp_path):
    msg = message(OK, {"GATE_JEV_PROMPT": str(tmp_path / "missing.json")})
    assert "clef: GATE_JEV_PROMPT: cannot read " in msg
    assert "jev: GATE_JEV_PROMPT: cannot read " in msg
    assert msg.endswith(HALT_TAIL)


def test_bad_backend_timeout_is_listed():
    assert message(OK, {"GATE_JEV_TIMEOUT_MS": "x"}).startswith("destructive-guard: jev: GATE_JEV_TIMEOUT_MS must be")


def test_removed_variables_are_listed_in_the_same_message():
    msg = message({"GATE_CHECKS": "none", "GATE_JEV": "1", "GATE_STRICT": "1"})
    assert msg == (
        "destructive-guard: jev: TYPESAFE_API_KEY is not set (skipped this session); "
        "ignoring the removed 0.8 variables GATE_CHECKS, GATE_STRICT, GATE_JEV"
    )
    assert message(OK, {"GATE_JEV_SCOPE": "bash"}) == (
        "destructive-guard: ignoring the removed 0.8 variables GATE_JEV_SCOPE"
    )


def test_removed_list_matches_the_plan():
    assert set(SessionCheck.REMOVED) == {
        "GATE_CHECKS", "GATE_RULES_FILE", "GATE_RULES", "GATE_DISABLE_RULES", "GATE_STRICT", "GATE_JEV",
        "GATE_JEV_ALLOW_THRESHOLD", "GATE_JEV_CAN_LIFT", "GATE_JEV_SCOPE", "GATE_JEV_LOG",
    }  # fmt: skip


@pytest.mark.parametrize("value", ["0", "false", "OFF"])
def test_gate_disabled_is_silent(value):
    assert HookRunner.run_session({"GATE_ENABLED": value, "GATE_CHECKS": "x"}).stdout == ""
    assert HookRunner.run_session({"GATE_ENABLED": value, "GATE_CLEF_ENABLED": "0"}).stdout == ""


def test_never_contacts_jev(jev: JevMock):
    HookRunner.run_session(OK, {"GATE_JEV_URL": jev.url})
    assert jev.hits == 0


def test_no_python_is_reported_once_here():
    msg = message({"GATE_INFRA_PYTHON": "/nonexistent"})
    assert msg.startswith("destructive-guard: no Python >= 3.14 found (set GATE_INFRA_PYTHON)")
    assert "GATE_ENABLED=0" in msg


def test_old_python_is_reported(old_python):
    assert message({"GATE_INFRA_PYTHON": old_python}).startswith("destructive-guard: no Python >= 3.14")


def test_no_interpreter_on_path(tmp_path):
    fake = FakeBin(tmp_path)
    for tool in ("dirname", "tr"):
        fake.link(tool)
    proc = subprocess.run(
        [shutil.which("bash"), str(DG / "hooks" / "session-start.sh")],
        capture_output=True,
        env=IsolatedEnv.clean({"PATH": str(fake.path)}),
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["systemMessage"].startswith("destructive-guard: no Python >= 3.14")
    assert os.path.isfile(DG / "hooks" / "session-start.sh")


def test_unwritable_log_is_reported(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    log = blocker / "gate.log"
    msg = message(OK, {"GATE_LOG": str(log)})
    assert msg.startswith(f"destructive-guard: cannot write GATE_LOG {log} (")
    assert msg.endswith("): no log this session")


def test_unwritable_log_joins_the_misconfigured_message(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    msg = message({"GATE_LOG": str(blocker / "gate.log")})
    assert msg.startswith("destructive-guard: jev: TYPESAFE_API_KEY is not set (skipped this session)")
    assert "; cannot write GATE_LOG " in msg


def test_writable_log_in_a_new_directory_is_silent(tmp_path):
    log = tmp_path / "logs" / "gate.jsonl"
    assert HookRunner.run_session(OK, {"GATE_LOG": str(log)}).stdout == ""
    assert log.parent.is_dir()
