"""Contract: hooks/gate.sh against local mocks: Jev alone (Clef off), the Clef -> Jev chain, and the halt."""

import json
import re

import pytest

from dg_testkit import DG, REPO
from dg_testkit.hook import HookRunner
from dg_testkit.jev_mock import JevMock

HERE = DG / "tests"
RISKY = JevMock.probs(0.1, 0.1, 0.6, 0.2)
SAFE = JevMock.probs(0.01, 0.97, 0.01, 0.01)
ASK_PREFIX = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":'


@pytest.fixture
def run(jev):
    """Runs the hook against the mock; `answer` is a probabilities dict or {"kind": ..., "status": ...}."""

    def go(stdin, answer=SAFE, env=None, cwd=None):
        if "kind" in answer or "status" in answer:
            jev.answer(**answer)
        else:
            jev.answer(answer)
        # Jev alone, as in 1.x: Clef switched off.
        key = {"TYPESAFE_API_KEY": "test-key", "GATE_JEV_URL": jev.url, "GATE_JEV_PROMPT": "cloud-change", "GATE_CLEF_ENABLED": "0"}
        return HookRunner.run(stdin, key, env or {}, cwd=cwd)

    return go


def _fake_engine(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(0o755)
    return str(p)


def test_risky_call_asks_with_jev_reason(run):
    r = run(HookRunner.bash_input("terraform apply", HERE), RISKY)
    assert r.status == 0
    assert r.stdout == (
        ASK_PREFIX + '"jev: cloud_or_data_change p=0.60 risk=0.80"}}'
    )


def test_safe_call_gives_no_output(run):
    r = run(HookRunner.bash_input("git status", HERE), SAFE)
    assert (r.status, r.stdout) == (0, "")


def test_jev_down_asks_with_a_warning(run):
    r = run(HookRunner.bash_input("git status", HERE), {"status": 503})
    assert r.stdout == (
        ASK_PREFIX + '"jev-unavailable: http 503"},"systemMessage":"destructive-guard: Jev returned http 503; '
        'asking you instead. GATE_ENABLED=0 turns the gate off."}'
    )


def test_env_call_is_not_sent_and_warns(run, jev):
    r = run(HookRunner.bash_input("cat .env", HERE), RISKY)
    assert json.loads(r.stdout) == {
        "systemMessage": "destructive-guard: call mentions a .env file and was not sent to Jev; Claude Code's permissions apply"
    }
    assert jev.hits == 0


def test_defaults_without_a_local_clef_or_a_key_ask_with_a_warning():
    """2.0 default: Clef on (usable, nothing listens), Jev without a key skipped -> every call asks."""
    r = HookRunner.run(HookRunner.bash_input("git status", HERE))
    assert r.stdout == (
        ASK_PREFIX + '"clef-unavailable: network error"},"systemMessage":"destructive-guard: Clef could not be reached; '
        'asking you instead. GATE_ENABLED=0 turns the gate off."}'
    )


HALT = (
    "destructive-guard: no classifier available (clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set): "
    "this session is halted. Fix the setting or set GATE_ENABLED=0."
)


@pytest.mark.parametrize("command", ["rm -rf /", "git status", "cat .env"])
def test_no_usable_backend_halts_every_call(command):
    r = HookRunner.run(HookRunner.bash_input(command, HERE), {"GATE_CLEF_ENABLED": "0"})
    assert r.status == 0
    assert json.loads(r.stdout) == {
        "continue": False,
        "stopReason": HALT,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set",
        },
        "systemMessage": HALT,
    }


def test_halt_names_each_backends_issue(jev):
    r = HookRunner.run(
        HookRunner.mcp_input("mcp__db__execute_sql", {"query": "select 1"}, HERE),
        {"GATE_CLEF_URL": "http://example.com/v1", "GATE_JEV_ENABLED": "0", "TYPESAFE_API_KEY": "k", "GATE_JEV_URL": jev.url},
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == (
        "no-classifier: clef: GATE_CLEF_URL must use https; jev: disabled by GATE_JEV_ENABLED=0"
    )
    assert jev.hits == 0


def test_bad_gate_flag_halts():
    r = HookRunner.run(HookRunner.bash_input("ls", HERE), {"TYPESAFE_API_KEY": "k", "GATE_CLEF_ENABLED": "maybe"})
    out = json.loads(r.stdout)
    assert out["continue"] is False
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == 'no-classifier: GATE_CLEF_ENABLED must be 0 or 1, got "maybe"'


def test_halt_ignores_calls_that_need_no_decision():
    r = HookRunner.run('{"tool_name":"Read","tool_input":{"file_path":"/x"}}', {"GATE_CLEF_ENABLED": "0"})
    assert (r.status, r.stdout) == (0, "")


@pytest.fixture
def chain(jev, clef):
    """Both backends on, each at its mock; `clef_answer` / `jev_answer` are mock kwargs."""

    def go(stdin, clef_answer, jev_answer, env=None):
        clef.answer(**clef_answer)
        jev.answer(**jev_answer)
        base = {
            "GATE_CLEF_URL": clef.url,
            "TYPESAFE_API_KEY": "test-key",
            "GATE_JEV_URL": jev.url,
            "GATE_JEV_PROMPT": "cloud-change",
        }
        return HookRunner.run(stdin, base, env or {})

    return go


def test_clef_answers_and_jev_is_not_asked(chain, jev, clef):
    r = chain(HookRunner.bash_input("terraform apply", HERE), {"probs": RISKY}, {"probs": SAFE})
    assert r.stdout == ASK_PREFIX + '"clef: cloud_or_data_change p=0.60 risk=0.80"}}'
    assert (clef.hits, jev.hits) == (1, 0)
    assert clef.bodies[0]["model"] == "clef"
    assert "authorization" not in clef.headers[0]  # no GATE_CLEF_API_KEY: no Authorization header


def test_clef_safe_answer_decides(chain, jev, clef):
    r = chain(HookRunner.bash_input("git status", HERE), {"probs": SAFE}, {"probs": RISKY})
    assert (r.status, r.stdout) == (0, "")
    assert (clef.hits, jev.hits) == (1, 0)


def test_clef_down_falls_back_to_jev(chain, jev, clef):
    r = chain(HookRunner.bash_input("terraform apply", HERE), {"status": 500}, {"probs": RISKY})
    assert r.stdout == ASK_PREFIX + '"jev: cloud_or_data_change p=0.60 risk=0.80"}}'  # an answer: no warning
    assert (clef.hits, jev.hits) == (1, 1)
    assert clef.bodies[0] == {**jev.bodies[0], "model": "clef"}  # the same redacted request


def test_both_down_ask_naming_each_backend(chain):
    r = chain(HookRunner.bash_input("git status", HERE), {"status": 503}, {"kind": "hang"}, {"GATE_JEV_TIMEOUT_MS": "200"})
    assert r.stdout == (
        ASK_PREFIX + '"clef-unavailable: http 503; jev-unavailable: timeout after 200 ms"},'
        '"systemMessage":"destructive-guard: Clef returned http 503; Jev did not answer in time (200 ms); '
        'asking you instead. GATE_ENABLED=0 turns the gate off."}'
    )


def test_unreachable_clef_then_jev_down(chain):
    r = chain(HookRunner.bash_input("ls", HERE), {"probs": SAFE}, {"status": 503}, {"GATE_CLEF_URL": "http://127.0.0.1:1/v1"})
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "clef-unavailable: network error; jev-unavailable: http 503"
    assert out["systemMessage"].startswith("destructive-guard: Clef could not be reached; Jev returned http 503; asking you instead.")


def test_misconfigured_clef_is_skipped_silently(chain, clef, jev):
    r = chain(HookRunner.bash_input("git status", HERE), {"probs": RISKY}, {"probs": SAFE}, {"GATE_CLEF_URL": "http://example.com"})
    assert (r.status, r.stdout) == (0, "")
    assert (clef.hits, jev.hits) == (0, 1)


def test_jev_without_key_leaves_clef_alone(chain, clef, jev):
    r = chain(HookRunner.bash_input("ls", HERE), {"status": 500}, {"probs": SAFE}, {"TYPESAFE_API_KEY": ""})
    assert HookRunner.decision(r.stdout)["permissionDecisionReason"] == "clef-unavailable: http 500"
    assert (clef.hits, jev.hits) == (1, 0)


def test_env_call_is_sent_to_neither_backend(chain, clef, jev):
    r = chain(HookRunner.bash_input("cat .env", HERE), {"probs": RISKY}, {"probs": RISKY})
    assert "permissionDecision" not in r.stdout
    assert (clef.hits, jev.hits) == (0, 0)


@pytest.mark.parametrize("stdin", ["not json", "", "null", '"x"', '{"tool_input": {}}', '{"tool_name": 3}'])
def test_unreadable_input_asks_even_without_a_key(stdin):
    r = HookRunner.run(stdin)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "plugin-fault: bad hook input"
    assert "could not read the hook input" in out["systemMessage"]


@pytest.mark.parametrize("stdin", ['{"tool_name":"Read","tool_input":{"file_path":"/x"}}', '{"tool_name":"Bash","tool_input":{}}'])
def test_calls_that_need_no_decision_give_no_output(run, jev, stdin):
    assert run(stdin, RISKY).stdout == ""
    assert jev.hits == 0


def test_interpreter_unavailable_asks():
    r = HookRunner.run(HookRunner.bash_input("git status", HERE), {"GATE_INFRA_PYTHON": "/nonexistent"})
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "plugin-fault: no Python >= 3.14 found (set GATE_INFRA_PYTHON)"
    assert out["systemMessage"].startswith("destructive-guard: no Python >= 3.14 found")


def test_engine_partial_json_then_nonzero_gives_fixed_ask(tmp_path):
    fake = _fake_engine(tmp_path, "partial.sh", """printf '{"hookSpecificOutput":{"permi'; exit 4""")
    r = HookRunner.run(HookRunner.bash_input("git status", HERE), {"GATE_INFRA_PYTHON": fake})
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "plugin-fault: internal error: engine exit 4"


def test_engine_exit_0_without_output_is_no_decision(tmp_path):
    fake = _fake_engine(tmp_path, "noop.sh", "exit 0")
    assert HookRunner.run(HookRunner.bash_input("git status", HERE), {"GATE_INFRA_PYTHON": fake}).stdout == ""


def test_hook_registered_for_bash_and_mcp_only():
    hooks = json.loads((DG / "hooks" / "hooks.json").read_text())["hooks"]
    entries = [e for e in hooks["PreToolUse"] if e["matcher"] == "Bash|mcp__.*"]
    assert len(entries) == 1
    assert re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/gate\.sh", entries[0]["hooks"][0]["command"])
    assert not any(
        e["matcher"] != "Bash|mcp__.*" and any(re.search(r"gate\.sh", h["command"]) for h in e["hooks"])
        for e in hooks["PreToolUse"]
    )


def test_session_start_registered():
    hooks = json.loads((DG / "hooks" / "hooks.json").read_text())["hooks"]
    [entry] = hooks["SessionStart"]
    assert re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/session-start\.sh", entry["hooks"][0]["command"])


def _raw_bash(command: bytes, cwd) -> bytes:
    head = json.dumps({"tool_name": "Bash", "tool_input": {"command": "@@"}, "cwd": str(cwd)}).encode()
    return head.replace(b"@@", command)


def test_non_utf8_stdin_is_decided_normally(run, jev, tmp_path):
    r = run(_raw_bash(b"rm -rf \xff", tmp_path), RISKY)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert jev.bodies[0]["state"]["command"] == "rm -rf �"
    assert run(_raw_bash(b"ls \xff", tmp_path), SAFE).stdout == ""


def test_hook_does_not_depend_on_the_working_directory(run):
    assert run(HookRunner.bash_input("terraform apply", REPO), RISKY, cwd=HERE).stdout.startswith(ASK_PREFIX)
