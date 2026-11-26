"""Backend requests through hooks/gate.sh against local mocks: request shape, auth header, retries, timeouts,
the audit log (GATE_LOG). Jev alone (Clef off) unless a test says otherwise."""

import json
import time
from dataclasses import dataclass

import pytest

from dg_testkit import DG, REPO
from dg_testkit.hook import HookRunner
from dg_testkit.jev_mock import JevMock

HERE = DG / "tests"
DOWN = "http://127.0.0.1:1/v1/systemone"
DBHOST_SQL = "mcp__plugin_dbhost_dbhost__execute_sql"

SAFE = JevMock.probs(0, 1, 0, 0)
RISKY = JevMock.probs(0, 0, 0, 1)
READ_ONLY = JevMock.probs(0.01, 0.97, 0.01, 0.01)


def bash(command, cwd=HERE):
    return HookRunner.bash_input(command, cwd)


def mcp(tool, tool_input=None, cwd=REPO):
    return HookRunner.mcp_input(tool, tool_input or {}, cwd)


@dataclass
class JevRun:
    status: int
    stdout: str
    seconds: float
    hits: int
    bodies: list[dict]
    headers: list[dict]
    log: list[dict]


@pytest.fixture
def run_jev(jev, tmp_path):
    """Run the hook against the mock; the caller sets the mock answers. Requests and log lines are this run's only."""
    log_path = tmp_path / "gate.log"

    def go(stdin, env=None, cwd=None):
        log_path.unlink(missing_ok=True)
        jev_env = {
            "TYPESAFE_API_KEY": "key-123",
            "GATE_JEV_URL": jev.url,
            "GATE_JEV_PROMPT": "cloud-change",
            "GATE_LOG": str(log_path),
            "GATE_JEV_TIMEOUT_MS": "5000",
            "GATE_CLEF_ENABLED": "0",
        }
        before = jev.hits
        started = time.monotonic()
        r = HookRunner.run(stdin, jev_env, env or {}, cwd=cwd)
        seconds = time.monotonic() - started
        log = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
        return JevRun(
            r.status,
            r.stdout,
            seconds,
            jev.hits - before,
            [b for b in jev.bodies[before:] if b is not None],
            jev.headers[before:],
            log,
        )

    return go


def reason(stdout):
    return HookRunner.decision(stdout)["permissionDecisionReason"]


def test_request_shape(jev, run_jev):
    """The request carries the model, the redacted command, the prompt's question and criteria."""
    jev.answer(READ_ONLY, usage={"input_tokens": 900, "output_tokens": 12})
    r = run_jev(bash("git status"))
    [record] = r.log
    assert record["probabilities"] == READ_ONLY
    assert record["usage"] == {"input_tokens": 900, "output_tokens": 12}
    assert isinstance(record["latencyMs"], (int, float))
    assert r.hits == 1  # one POST to GATE_JEV_URL (the mock serves POST only)
    assert r.headers[0]["authorization"] == "Bearer key-123"
    body = r.bodies[0]
    assert body["model"] == "jev-latest"
    assert body["state"]["tool"] == "Bash"
    assert body["state"]["command"] == "git status"
    assert body["state"]["notes"] == []
    assert "scripts" not in body["state"]
    assert sorted(body["state"]["context"]) == ["environment", "inputs", "policy", "project"]
    assert list(body["questions"]) == ["cloud_change"]
    q = body["questions"]["cloud_change"]
    assert q["type"] == "choice"
    assert list(q["criteria"]) == ["unrelated", "read_only", "cloud_or_data_change", "destructive"]
    for option in q["criteria"].values():
        assert isinstance(option["description"], str)
        assert len(option["examples"]) > 0
    assert "local_write" not in json.dumps(body)


def test_script_text_is_not_read_or_sent(jev, run_jev, tmp_path):
    """D4: `bash cleanup.sh` is judged by its text only, nothing is parsed or followed."""
    (tmp_path / "cleanup.sh").write_text("rm -rf /\n")
    jev.answer(READ_ONLY)
    r = run_jev(bash("bash cleanup.sh", tmp_path), cwd=tmp_path)
    assert r.bodies[0]["state"] == {
        "tool": "Bash",
        "command": "bash cleanup.sh",
        "notes": [],
        "context": r.bodies[0]["state"]["context"],
    }
    assert "rm -rf" not in json.dumps(r.bodies[0])


def test_unparsable_looking_command_is_sent_as_text(jev, run_jev):
    jev.answer(RISKY)
    r = run_jev(bash('eval "$CMD" | bash'))
    assert r.hits == 1
    assert reason(r.stdout).startswith("jev: ")


def test_error_has_latency(jev, run_jev):
    jev.answer(status=500)
    [record] = run_jev(bash("ls")).log
    assert record["warning"] == "http"
    assert record["reason"] == "jev-unavailable: http 500"
    assert isinstance(record["latencyMs"], (int, float))


def test_usage_null_when_absent(jev, run_jev):
    jev.answer(kind="raw", body={"answers": {"cloud_change": {"type": "choice", "probabilities": READ_ONLY}}})
    [record] = run_jev(bash("ls")).log
    assert record["probabilities"] == READ_ONLY
    assert record["usage"] is None


def test_non_retryable_http_error(jev, run_jev):
    jev.answer(status=401)
    r = run_jev(bash("ls"))
    assert reason(r.stdout) == "jev-unavailable: http 401"
    assert r.hits == 1


def test_retries_429_then_succeeds(jev, run_jev):
    jev.sequence({"status": 429, "retry_after": "0"}, {"probs": SAFE})
    r = run_jev(bash("terraform apply"))
    assert r.hits == 2
    assert r.log[0]["probabilities"] == SAFE
    assert r.log[0]["decision"] == "none"
    assert r.stdout == ""


def test_retry_after_over_budget_gives_up(jev, run_jev):
    jev.answer(status=529, retry_after="10")
    r = run_jev(bash("ls"), {"GATE_JEV_TIMEOUT_MS": "200"})
    assert reason(r.stdout) == "jev-unavailable: http 529"
    assert r.hits == 1


def test_hang_times_out(jev, run_jev):
    jev.answer(kind="hang")
    r = run_jev(bash("ls"), {"GATE_JEV_TIMEOUT_MS": "50"})
    assert reason(r.stdout) == "jev-unavailable: timeout after 50 ms"
    assert r.seconds < 5, r.seconds


def test_network_failure(run_jev):
    r = run_jev(bash("ls"), {"GATE_JEV_URL": DOWN})
    assert reason(r.stdout) == "jev-unavailable: network error"


def test_malformed_bodies(jev, run_jev):
    jev.answer(kind="malformed")
    assert reason(run_jev(bash("ls")).stdout) == "jev-unavailable: malformed answer"
    jev.answer(kind="raw", body={"answers": {}})
    assert reason(run_jev(bash("ls")).stdout) == "jev-unavailable: malformed answer"


def test_failures_ask_for_bash_and_mcp(jev, run_jev):
    jev.answer(SAFE, status=500)
    for stdin in [bash("git status"), mcp(DBHOST_SQL, {"query": "SELECT 1"})]:
        assert reason(run_jev(stdin).stdout) == "jev-unavailable: http 500", stdin


def test_env_reference_never_sent(jev, run_jev):
    jev.answer(RISKY)
    for stdin in [bash("cat .env"), mcp(DBHOST_SQL, {"query": "select 1", "file": "apps/web/.env.local"})]:
        r = run_jev(stdin)
        assert r.hits == 0, stdin
        assert "permissionDecision" not in r.stdout
        assert json.loads(r.stdout)["systemMessage"].startswith("destructive-guard: call mentions a .env file")


def test_risk_above_threshold_asks_for_bash_and_mcp(jev, run_jev):
    jev.answer(JevMock.probs(0, 0.05, 0.05, 0.9))
    for stdin in [bash("rm -rf x"), mcp("mcp__x__y")]:
        o = HookRunner.decision(run_jev(stdin).stdout)
        assert o["permissionDecision"] == "ask"
        assert o["permissionDecisionReason"] == "jev: destructive p=0.90 risk=0.95"


def test_uncertain_answer_gives_no_decision(jev, run_jev):
    jev.answer(JevMock.probs(0.4, 0.2, 0.2, 0.2))
    r = run_jev(bash("terraform apply"))
    assert r.hits == 1
    assert r.stdout == ""


def test_mcp_payload_input_and_cut_note(jev, run_jev):
    jev.answer(READ_ONLY)
    state = run_jev(mcp(DBHOST_SQL, {"query": "SELECT 1"})).bodies[0]["state"]
    assert state["tool"] == DBHOST_SQL
    assert state["input"] == '{"query":"SELECT 1"}'
    assert state["notes"] == []
    assert "command" not in state
    text = {"text": "lorem ipsum " * 750}
    state = run_jev(mcp("mcp__x__y", text)).bodies[0]["state"]
    assert len(state["input"]) == 8000
    full = len(json.dumps(text, separators=(",", ":")))
    assert state["notes"] == [f"input cut to 8000 of {full} characters"]


def test_bash_command_cut_note(jev, run_jev):
    jev.answer(READ_ONLY)
    command = "echo " + "lorem ipsum " * 900
    state = run_jev(bash(command)).bodies[0]["state"]
    assert len(state["command"]) == 8000
    assert state["notes"][0].startswith("command cut to 8000 of ")


def test_secrets_are_redacted_before_sending(jev, run_jev):
    jev.answer(READ_ONLY)
    r = run_jev(bash("curl -H 'Authorization: Bearer abcdef123456' https://user:hunter2@host/x TOKEN=supersecret123"))
    sent = json.dumps(r.bodies[0])
    for secret in ("abcdef123456", "hunter2", "supersecret123"):
        assert secret not in sent


def test_audit_log_redacted_records(jev, tmp_path):
    """A Bash none, a skipped .env call, an MCP none with a secret, and an unavailable Jev, in one log."""
    log = tmp_path / "audit.jsonl"
    env = {
        "TYPESAFE_API_KEY": "key-123",
        "GATE_JEV_URL": jev.url,
        "GATE_JEV_PROMPT": "cloud-change",
        "GATE_LOG": str(log),
        "GATE_CLEF_ENABLED": "0",
    }

    def go(stdin):
        return HookRunner.run(stdin, env)

    jev.answer(JevMock.probs(0.01, 0.98, 0.005, 0.005), usage={"input_tokens": 1000})
    go(bash("TOPSECRET_X=supersecret123 node a.mjs"))
    go(bash("cat .env"))
    go(mcp(DBHOST_SQL, {"password": "supersecret123", "q": 1}))
    jev.answer(status=401)
    go(bash("terraform apply"))
    text = log.read_text()
    assert "supersecret123" not in text, text
    assert "key-123" not in text
    lines = [json.loads(line) for line in text.strip().split("\n")]
    assert len(lines) == 4
    assert lines[0]["tool"] == "Bash"
    assert lines[0]["command"] == "TOPSECRET_X=[REDACTED] node a.mjs"
    assert lines[0]["decision"] == "none"
    assert lines[0]["warning"] is None
    assert lines[0]["usage"] == {"input_tokens": 1000}
    assert isinstance(lines[0]["latencyMs"], (int, float))
    assert lines[0]["probabilities"]["read_only"] == 0.98
    assert lines[0]["ts"]
    assert lines[1]["command"] is None
    assert lines[1]["warning"] == "envReference"
    assert lines[2]["tool"] == DBHOST_SQL
    assert lines[2]["input"] == '{"password":"[REDACTED]","q":1}'
    assert lines[2]["decision"] == "none"
    assert lines[3]["decision"] == "ask"
    assert lines[3]["reason"] == "jev-unavailable: http 401"
    assert lines[3]["warning"] == "http"


def test_halted_calls_are_logged_as_such(tmp_path):
    log = tmp_path / "gate.log"
    r = HookRunner.run(bash("ls"), {"GATE_LOG": str(log), "GATE_CLEF_ENABLED": "0"})
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    [line] = [json.loads(x) for x in log.read_text().splitlines()]
    assert line["tool"] == "Bash"
    assert line["decision"] == "deny"
    assert line["warning"] == "noClassifier"
    assert line["reason"] == "no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set"
    assert "command" not in line  # nothing was prepared for sending


def test_log_is_off_by_default_and_a_bad_path_does_not_fail_the_hook(jev, run_jev, tmp_path):
    jev.answer(RISKY)
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    r = run_jev(bash("ls"), {"GATE_LOG": str(blocker / "gate.log")})
    assert reason(r.stdout).startswith("jev: ")
    assert r.log == []


def test_log_creates_missing_directories(jev, run_jev, tmp_path):
    jev.answer(RISKY)
    log = tmp_path / "no" / "such" / "dir" / "gate.log"
    r = run_jev(bash("ls"), {"GATE_LOG": str(log)})
    assert reason(r.stdout).startswith("jev: ")
    [line] = [json.loads(x) for x in log.read_text().splitlines()]
    assert line["decision"] == "ask"


@pytest.mark.parametrize("flag", ["GATE_ENV_GUARD", "GATE_ENABLED", "GATE_JEV_ENABLED"])
def test_bad_gate_flag_halts_and_is_logged(tmp_path, flag):
    log = tmp_path / "gate.log"
    r = HookRunner.run(bash("ls"), {"TYPESAFE_API_KEY": "k", "GATE_LOG": str(log), flag: "maybe"})
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == f'no-classifier: {flag} must be 0 or 1, got "maybe"'
    [line] = [json.loads(x) for x in log.read_text().splitlines()]
    assert line["tool"] == "Bash"
    assert line["decision"] == "deny"
    assert line["warning"] == "noClassifier"


# ---------------------------------------------------------------------------
# Clef and the chain
# ---------------------------------------------------------------------------


@pytest.fixture
def run_chain(jev, clef, tmp_path):
    """Both backends at their mocks, with GATE_LOG; returns (stdout, log lines)."""
    log_path = tmp_path / "chain.log"

    def go(stdin, env=None):
        log_path.unlink(missing_ok=True)
        base = {
            "GATE_CLEF_URL": clef.url,
            "TYPESAFE_API_KEY": "key-123",
            "GATE_JEV_URL": jev.url,
            "GATE_JEV_PROMPT": "cloud-change",
            "GATE_LOG": str(log_path),
        }
        r = HookRunner.run(stdin, base, env or {})
        return r.stdout, [json.loads(x) for x in log_path.read_text().splitlines()]

    return go


def test_clef_without_key_sends_no_authorization_header(clef, run_chain):
    clef.answer(READ_ONLY)
    run_chain(bash("git status"))
    assert clef.hits == 1
    assert "authorization" not in clef.headers[0]


def test_clef_key_is_sent_as_bearer(clef, run_chain):
    clef.answer(READ_ONLY)
    run_chain(bash("git status"), {"GATE_CLEF_API_KEY": "clef-key"})
    assert clef.headers[0]["authorization"] == "Bearer clef-key"


def test_clef_request_uses_clef_model_and_settings(clef, run_chain):
    clef.answer(READ_ONLY)
    run_chain(bash("git status"), {"GATE_CLEF_MODEL": "clef-small"})
    assert clef.bodies[0]["model"] == "clef-small"
    assert list(clef.bodies[0]["questions"]) == ["cloud_change"]  # the shared prompt


def test_clef_has_its_own_timeout(clef, jev, run_chain):
    clef.answer(kind="hang")
    jev.answer(RISKY)
    started = time.monotonic()
    stdout, [line] = run_chain(bash("ls"), {"GATE_CLEF_TIMEOUT_MS": "100"})
    assert time.monotonic() - started < 5
    assert reason(stdout) == "jev: destructive p=1.00 risk=1.00"
    assert line["fallback"][0]["backend"] == "clef"
    assert line["fallback"][0]["error"] == "timeout"


def test_log_names_who_answered(clef, jev, run_chain):
    clef.answer(READ_ONLY, usage={"input_tokens": 7})
    _, [line] = run_chain(bash("git status"))
    assert line["backend"] == "clef"
    assert line["fallback"] == []
    assert line["probabilities"] == READ_ONLY
    assert line["usage"] == {"input_tokens": 7}
    assert jev.hits == 0


def test_log_lists_the_fallback(clef, jev, run_chain):
    clef.answer(status=503)
    jev.answer(READ_ONLY)
    _, [line] = run_chain(bash("git status"))
    assert line["backend"] == "jev"
    [fallback] = line["fallback"]
    assert fallback["backend"] == "clef"
    assert fallback["error"] == "http 503"
    assert isinstance(fallback["latencyMs"], int)
    assert line["probabilities"] == READ_ONLY
    assert isinstance(line["latencyMs"], int)


def test_log_when_every_backend_is_down(clef, jev, run_chain):
    clef.answer(status=503)
    jev.answer(kind="malformed")
    stdout, [line] = run_chain(bash("git status"))
    assert reason(stdout) == "clef-unavailable: http 503; jev-unavailable: malformed answer"
    assert line["backend"] is None
    assert [(f["backend"], f["error"]) for f in line["fallback"]] == [("clef", "http 503"), ("jev", "malformed")]
    assert line["warning"] == "malformed"
    assert line["probabilities"] is None
