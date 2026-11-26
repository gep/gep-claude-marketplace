"""LJ1: the real gate.sh against the real Jev. Marked `live_jev`: excluded from the default run."""

import json

import pytest

from dg_testkit.hook import HookRunner
from dg_testkit.live_jev import LiveJev

pytestmark = pytest.mark.live_jev

ASK_CALL = "rm -rf ~/projects"
NONE_CALL = "git status"


@pytest.fixture
def run(live_env, tmp_path):
    def go(command, env=None, key=True, sends=True):
        if sends:
            LiveJev.count_request()
        base = {k: v for k, v in live_env.items() if key or k != "TYPESAFE_API_KEY"}
        return HookRunner.run(HookRunner.bash_input(command, tmp_path), base, env or {}, cwd=tmp_path)

    return go


def test_clear_cut_ask(run):
    r = run(ASK_CALL)
    assert r.status == 0
    d = HookRunner.decision(r.stdout)
    assert d["permissionDecision"] == "ask"
    assert d["permissionDecisionReason"].startswith("jev: ")
    assert "systemMessage" not in json.loads(r.stdout)


def test_clear_cut_none(run):
    r = run(NONE_CALL)
    assert (r.status, r.stdout) == (0, "")


def test_wrong_key_asks_with_http_401_or_403(run):
    out = json.loads(run(NONE_CALL, {"TYPESAFE_API_KEY": "invalid"}).stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] in ("jev-unavailable: http 401", "jev-unavailable: http 403")
    assert out["systemMessage"].startswith("destructive-guard: Jev returned http 40")


def test_tiny_timeout_asks_with_timeout(run):
    out = json.loads(run(NONE_CALL, {"GATE_JEV_TIMEOUT_MS": "1"}).stdout)
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "jev-unavailable: timeout after 1 ms"
    assert "did not answer in time" in out["systemMessage"]


def test_no_key_halts_and_sends_nothing(live_env, run):
    before = LiveJev.requests
    r = run(ASK_CALL, key=False, sends=False)
    assert r.status == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["continue"] is False
    assert LiveJev.requests == before
    session = HookRunner.run_session({k: v for k, v in live_env.items() if k != "TYPESAFE_API_KEY"})
    assert "TYPESAFE_API_KEY is not set" in json.loads(session.stdout)["systemMessage"]


def test_env_call_warns_and_is_not_sent(run):
    out = json.loads(run("cat .env", sends=False).stdout)
    assert "hookSpecificOutput" not in out
    assert "mentions a .env file" in out["systemMessage"]


def test_log_has_the_redacted_command_probabilities_decision_and_never_the_key(run, live_env, tmp_path):
    run("TOKEN=supersecret123 " + ASK_CALL)
    text = (tmp_path / "gate.log").read_text()
    assert live_env["TYPESAFE_API_KEY"] not in text
    assert "supersecret123" not in text
    [line] = [json.loads(x) for x in text.splitlines()]
    assert line["command"].startswith("TOKEN=[REDACTED] ")
    assert set(line["probabilities"])
    assert line["decision"] in ("ask", "none")
