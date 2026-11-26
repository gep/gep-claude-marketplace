"""The real gate.sh against the Lean model (formal/SimpleGate.lean): theses S1-S12 and one test per `example`.

`model` is the Lean `hook` in Python, per mille like the model. Every run goes through the real hook
with `JevMock`, and each output is checked against the model and against the theses.

What the code cannot do yet is listed in `PENDING` (name -> work package of the Clef plan); those
tests check the Python model and then skip the real hook. C4 (session start halt, prompt gate) empties it.
"""

import json
import re
import stat
import sys
from dataclasses import dataclass
from itertools import product

import pytest

from dg_testkit import DG
from dg_testkit.hook import HookRunner
from dg_testkit.jev_mock import JevMock

ESCALATE = 500
DOWN_URL = "http://127.0.0.1:1/v1/systemone"
LIB = DG / "lib"
SECRETS = ["abc123xyz", "hunter2", "abcdefSECRET", "pw9"]
SECRET_BASH = "curl -H 'Authorization: Bearer abcdefSECRET' https://user:pw9@host/x TOKEN=abc123xyz ls"
SECRET_MCP = {"query": "SELECT 1", "password": "hunter2"}

# Lean examples and test groups the code does not implement yet: name -> work package. A pending
# test still checks the Python model, then skips the real hook. Remove an entry when its package lands.
PENDING = {
    "prompt_gate_block": "C4",
    "prompt_gate_pass": "C4",
    "session_start_halt": "C4",  # replaces session_start_misconfigured_no_key (meaning changed: halt)
    "session_start_clef_bad": "C4",
}


def pending(name: str) -> None:
    if name in PENDING:
        pytest.skip(f"real hook: implemented in {PENDING[name]} (Lean model ahead of the code)")


# Perm order of the model: allow < ask < deny.
RANK = {"allow": 0, "ask": 1, "deny": 2}


def effective(decision: str, claude: str) -> str:
    """The model's `effective`: a hook decision on top of Claude Code's own permission."""
    if decision == "none":
        return claude
    if decision == "deny":
        return "deny"
    return "deny" if claude == "deny" else "ask"


ASKS = {"timeout", "http", "network", "malformed", "badInput", "pythonMissing", "internalError"}

# A setting is (clef, jev), each "off" | "bad" | "ok" (the model's `BState`); order is Clef, Jev.
BACKENDS = ("clef", "jev")
JEV_ONLY = ("off", "ok")  # the 1.x code: Jev alone
JEV_NO_KEY = ("off", "bad")  # halted since the Clef model
Result = tuple[str, object]


def usable(setting: tuple[str, str]) -> list[str]:
    return [b for b, state in zip(BACKENDS, setting, strict=True) if state == "ok"]


def consult(q: dict[str, Result], backends: list[str]) -> Result:
    """The Lean `consult`: the first answer, else the last backend's `down`."""
    result: Result = ("down", "noClassifier")
    for b in backends:
        result = q[b]
        if result[0] == "answer":
            return result
    return result


def model(setting: tuple[str, str], call: str, q: Result | dict[str, Result]) -> tuple[str, str | None]:
    """The Lean `hook`: (decision, warning). `call` is tool | env | badInput | pythonMissing | crash.

    `q` is one result for every backend, or {"clef": ..., "jev": ...}."""
    if call in ("badInput", "pythonMissing"):
        return "ask", call
    if call == "crash":
        return "ask", "internalError"
    backends = usable(setting)
    if not backends:
        return "deny", "noClassifier"
    if call == "env":
        return "none", "envReference"
    qs = q if isinstance(q, dict) else dict.fromkeys(BACKENDS, q)
    kind, value = consult(qs, backends)
    if kind == "down":
        return ("ask", str(value)) if value in ASKS else ("none", str(value))
    return ("ask", None) if int(value) > ESCALATE else ("none", None)  # type: ignore[call-overload]


def session_model(setting: tuple[str, str]) -> tuple[list[str], bool] | None:
    """The Lean `sessionStart`: (misconfigured backends, halt), or None when silent."""
    bad = [b for b, state in zip(BACKENDS, setting, strict=True) if state == "bad"]
    halt = not usable(setting)
    return None if not bad and not halt else (bad, halt)


def prompt_gate_model(setting: tuple[str, str]) -> bool:
    """The Lean `promptGate`: True blocks the prompt."""
    return not usable(setting)


# Warning texts -> the model's `Down`. A backend failure names the backend (`Clef ...`, `Jev ...`); with
# several, the last one is the model's warning (`consult` keeps the last `down`).
BACKEND_WARNING_TEXT = [
    ("did not answer in time", "timeout"),
    ("returned http", "http"),
    ("could not be reached", "network"),
    ("'s answer was malformed", "malformed"),
]
WARNING_TEXT = [
    ("mentions a .env file", "envReference"),
    ("could not read the hook input", "badInput"),
    ("no Python >= 3.14 found", "pythonMissing"),
    ("internal error", "internalError"),
    ("no classifier available", "noClassifier"),
]
BACKEND_FAILURE = re.compile(r"(Clef|Jev)(?: did not answer in time| returned http|'s answer was malformed| could not be reached)")


@dataclass
class Out:
    stdout: str
    decision: str  # none | ask | deny
    warning: str | None
    reason: str | None


def warning_of(message: str) -> str:
    """The model's `Down` for one systemMessage."""
    failures = list(BACKEND_FAILURE.finditer(message))
    if failures:
        last = failures[-1].group(0)
        return next(kind for text, kind in BACKEND_WARNING_TEXT if text in last)
    return next(kind for text, kind in WARNING_TEXT if text in message)


def read(stdout: str) -> Out:
    """Parses one raw stdout and checks S1 and the raw-stdout scan on the way.

    `deny` comes only with the halt (S1, S12): then `continue: false` and `stopReason` too (A10)."""
    if stdout == "":
        return Out(stdout, "none", None, None)
    # S5/S1: the raw bytes never contain a way to allow anything, or to change the call.
    assert '"allow"' not in stdout, stdout
    for forbidden in ("updatedInput", "additionalContext", '"decision"', "suppressOutput"):
        assert forbidden not in stdout, (forbidden, stdout)
    data = json.loads(stdout)
    halted = '"deny"' in stdout
    if halted:
        assert set(data) == {"hookSpecificOutput", "systemMessage", "continue", "stopReason"}, data
        assert data["continue"] is False
        assert data["stopReason"] == data["systemMessage"]
    else:
        assert "continue" not in stdout and "stopReason" not in stdout, stdout
        assert set(data) <= {"hookSpecificOutput", "systemMessage"}, data
    specific = data.get("hookSpecificOutput")
    decision, reason = "none", None
    if specific is not None:
        assert set(specific) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}, specific
        assert specific["hookEventName"] == "PreToolUse"
        assert specific["permissionDecision"] == ("deny" if halted else "ask")  # S1
        decision, reason = specific["permissionDecision"], specific["permissionDecisionReason"]
        assert reason
    else:
        assert not halted
    warning = None
    message = data.get("systemMessage")
    if message is not None:
        assert message.startswith("destructive-guard: ")
        warning = warning_of(message)
    return Out(stdout, decision, warning, reason)


def check_theses(out: Out, expected: tuple[str, str | None], sent: bool, answered: bool) -> None:
    decision, warning = expected
    assert (out.decision, out.warning) == (decision, warning)
    # S3: a warning comes with ask exactly for the backend and plugin faults; deny only with noClassifier;
    # the .env warning has no decision.
    if out.warning is not None:
        assert (out.decision == "ask") == (out.warning in ASKS)
        if out.decision == "deny":
            assert out.warning == "noClassifier"
        if out.warning == "envReference":
            assert out.decision == "none"
    # S1, S12: deny comes only with the halt warning.
    assert (out.decision == "deny") == (out.warning == "noClassifier")
    # S5: the plugin only adds prompts (or, halted, denies), whatever Claude Code would have done.
    for claude, rank in RANK.items():
        assert RANK[effective(out.decision, claude)] >= rank
    # S4: with no decision the final permission is Claude Code's own.
    if out.decision == "none":
        assert all(effective("none", c) == c for c in RANK)
    # S8: an answered call never warns.
    if answered:
        assert out.warning is None
    del sent


@pytest.fixture
def hook(jev):
    """run(setting, stdin, answer, env) -> Out; `answer` is mock kwargs."""

    def run(setting, stdin, answer=None, env=None):
        if answer is not None:
            jev.answer(**answer)
        # Jev alone (`JEV_ONLY`): Clef is switched off so these runs keep meaning the same after C3.
        base = {"GATE_JEV_PROMPT": "cloud-change", "GATE_JEV_TIMEOUT_MS": "300", "GATE_CLEF_ENABLED": "0"}
        if setting == "on":
            base |= {"TYPESAFE_API_KEY": "test-key", "GATE_JEV_URL": jev.url}
        elif setting == "noKey":
            base |= {"GATE_JEV_URL": jev.url}
        elif setting == "badUrl":
            base |= {"TYPESAFE_API_KEY": "test-key", "GATE_JEV_URL": "http://example.com"}
        r = HookRunner.run(stdin, base, env or {})
        assert r.status == 0
        return read(r.stdout)

    return run


@pytest.fixture
def chain(jev, clef):
    """run(setting, stdin, clef_case, jev_case) -> Out, both backends configured per `setting`."""

    def run(setting, stdin, clef_case, jev_case):
        (clef_answer, clef_extra, _), (jev_answer, jev_extra, _) = JEV[clef_case], JEV[jev_case]
        clef.answer(**clef_answer)
        jev.answer(**jev_answer)
        env = {"GATE_JEV_PROMPT": "cloud-change", "GATE_JEV_TIMEOUT_MS": "300", "GATE_CLEF_TIMEOUT_MS": "300"}
        env |= {
            "off": {"GATE_CLEF_ENABLED": "0"},
            "bad": {"GATE_CLEF_URL": "http://example.com"},
            "ok": {"GATE_CLEF_URL": clef.url} | {k.replace("JEV", "CLEF"): v for k, v in clef_extra.items()},
        }[setting[0]]
        env |= {
            "off": {"GATE_JEV_ENABLED": "0"},
            "bad": {"GATE_JEV_URL": jev.url},
            "ok": {"TYPESAFE_API_KEY": "test-key", "GATE_JEV_URL": jev.url} | jev_extra,
        }[setting[1]]
        r = HookRunner.run(stdin, env)
        assert r.status == 0
        return read(r.stdout)

    return run


# Jev behaviours: name -> (mock kwargs, extra env, the model's q).
JEV = {
    "timeout": ({"kind": "hang"}, {}, ("down", "timeout")),
    "http500": ({"status": 500}, {}, ("down", "http")),
    "http429-over-budget": ({"status": 429, "retry_after": "10"}, {}, ("down", "http")),
    "network": ({"probs": JevMock.probs(1, 0, 0, 0)}, {"GATE_JEV_URL": DOWN_URL}, ("down", "network")),
    "malformed": ({"kind": "malformed"}, {}, ("down", "malformed")),
    "risk-0": ({"probs": JevMock.probs(1, 0, 0, 0)}, {}, ("answer", 0)),
    "risk-eq-escalate": ({"probs": JevMock.probs(0.25, 0.25, 0.25, 0.25)}, {}, ("answer", 500)),
    "risk-gt-escalate": ({"probs": JevMock.probs(0.2, 0.2, 0.3, 0.3)}, {}, ("answer", 600)),
}
ANSWERING = ("risk-0", "risk-eq-escalate", "risk-gt-escalate")
TOOLS = {
    "bash": HookRunner.bash_input(SECRET_BASH, "/tmp"),
    "mcp": HookRunner.mcp_input("mcp__db__execute_sql", SECRET_MCP, "/tmp"),
}
ENV_CALLS = {
    "bash-env": HookRunner.bash_input("cat apps/web/.env.local", "/tmp"),
    "mcp-env": HookRunner.mcp_input("mcp__db__execute_sql", {"query": "select 1", "file": ".env"}, "/tmp"),
}


def bodies_are_clean(jev) -> None:
    """S7: the mock never saw `.env` or a secret."""
    sent = json.dumps(jev.bodies)
    assert ".env" not in sent
    for secret in SECRETS:
        assert secret not in sent, secret


@pytest.mark.parametrize("setting", ["on", "noKey", "badUrl"])
@pytest.mark.parametrize("jev_case", list(JEV))
@pytest.mark.parametrize("tool", list(TOOLS))
def test_gated_calls(hook, jev, setting, jev_case, tool):
    answer, extra, q = JEV[jev_case]
    expected = model(JEV_ONLY if setting == "on" else JEV_NO_KEY, "tool", q)
    if setting != "on":
        assert expected == ("deny", "noClassifier")  # S12: no usable backend halts
    out = hook(setting, TOOLS[tool], answer, extra if setting == "on" else {})
    check_theses(out, expected, sent=setting == "on", answered=setting == "on" and q[0] == "answer")
    if setting == "on":
        bodies_are_clean(jev)
        # S10: Jev on, call sent, Jev unavailable -> never silent.
        if q[0] == "down":
            assert out.decision == "ask"
            assert out.reason.startswith("jev-unavailable: ")
        if q == ("answer", 600):
            assert out.reason.startswith("jev: ")
    else:
        # S12, S7: halted, the call is denied and nothing is sent.
        assert out.reason.startswith("no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: ")
        assert jev.hits == 0


@pytest.mark.parametrize("setting", ["on", "noKey", "badUrl"])
@pytest.mark.parametrize("call", list(ENV_CALLS))
def test_env_calls_are_never_sent(hook, jev, setting, call):
    expected = model(JEV_ONLY if setting == "on" else JEV_NO_KEY, "env", ("answer", 600))
    if setting != "on":
        assert expected == ("deny", "noClassifier")  # S12: the halt comes before the .env skip
    out = hook(setting, ENV_CALLS[call], JEV["risk-gt-escalate"][0])
    check_theses(out, expected, False, False)
    assert jev.hits == 0
    if setting == "on":
        assert out.decision == "none" and out.warning == "envReference"


@pytest.mark.parametrize("setting", ["on", "noKey", "badUrl"])
def test_non_gated_tool_needs_nothing(hook, jev, setting):
    out = hook(setting, json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}}), JEV["risk-gt-escalate"][0])
    assert out.stdout == ""
    assert jev.hits == 0


@pytest.mark.parametrize("setting", ["on", "noKey", "badUrl"])
@pytest.mark.parametrize("stdin", ["not json", "", "[1]"])
def test_bad_stdin_asks_whatever_the_setting(hook, jev, setting, stdin):
    out = hook(setting, stdin, JEV["risk-0"][0])
    check_theses(out, model(JEV_ONLY if setting == "on" else JEV_NO_KEY, "badInput", ("answer", 0)), False, False)
    assert out.reason == "plugin-fault: bad hook input"
    assert jev.hits == 0


@pytest.mark.parametrize("setting", ["on", "noKey"])
def test_no_python_asks_whatever_the_setting(hook, setting):
    out = hook(setting, TOOLS["bash"], None, {"GATE_INFRA_PYTHON": "/nonexistent"})
    check_theses(out, model(JEV_ONLY, "pythonMissing", ("answer", 0)), False, False)
    assert out.reason == "plugin-fault: no Python >= 3.14 found (set GATE_INFRA_PYTHON)"


@pytest.mark.parametrize("setting", ["on", "noKey"])
def test_python_below_3_14_asks(hook, old_python, setting):
    out = hook(setting, TOOLS["bash"], None, {"GATE_INFRA_PYTHON": old_python})
    assert (out.decision, out.warning) == ("ask", "pythonMissing")


@pytest.mark.parametrize("setting", ["on", "noKey"])
def test_engine_exit_1_with_partial_stdout_asks_and_drops_the_partial(hook, tmp_path, setting):
    fake = tmp_path / "partial.sh"
    fake.write_text("#!/bin/sh\nprintf '{\"hookSpecificOutput\":{\"permissionDecision\":\"all'\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    out = hook(setting, TOOLS["bash"], None, {"GATE_INFRA_PYTHON": str(fake)})
    check_theses(out, model(JEV_ONLY, "crash", ("answer", 0)), False, False)
    assert out.reason == "plugin-fault: internal error: engine exit 1"
    assert "allow" not in out.stdout


@pytest.mark.parametrize("setting", ["on", "noKey"])
def test_engine_exception_asks(hook, tmp_path, setting):
    """An exception inside the hook (here: while reading the call) is an internal-error ask."""
    fake = tmp_path / "boom.sh"
    code = (
        f"import sys; sys.path.insert(0, {str(LIB)!r}); from destructive_guard.hook import GateHook, main\n"
        "def boom(data):\n    raise RuntimeError('boom')\n"
        "GateHook.call_of = staticmethod(boom); sys.exit(main())"
    )
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} -I -c \"$CODE\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    out = hook(setting, TOOLS["bash"], None, {"GATE_INFRA_PYTHON": str(fake), "CODE": code})
    check_theses(out, model(JEV_ONLY, "crash", ("answer", 0)), False, False)
    assert out.reason == "plugin-fault: internal error: RuntimeError"


def test_s6_lowering_the_threshold_never_removes_an_ask(hook):
    answer = {"probs": JevMock.probs(0.4, 0.2, 0.2, 0.2)}  # risk 0.4
    assert hook("on", TOOLS["bash"], answer).decision == "none"
    for threshold in ("0.4", "0.3", "0.1"):
        assert hook("on", TOOLS["bash"], answer, {"GATE_JEV_ESCALATE_THRESHOLD": threshold}).decision == ("none" if threshold == "0.4" else "ask")


STATES = ("off", "bad", "ok")
RESULTS: list[Result] = [("answer", 0), ("answer", 500), ("answer", 501), *[("down", d) for d in ("timeout", "http", "network", "malformed")]]


def test_model_matches_its_own_matrix_exhaustively():
    """The Python model is total over the Lean types and obeys S1, S2, S11 and S12."""
    for setting, call, q_clef, q_jev in product(
        product(STATES, STATES), ["tool", "env", "badInput", "pythonMissing", "crash"], RESULTS, RESULTS
    ):
        q = {"clef": q_clef, "jev": q_jev}
        decision, warning = model(setting, call, q)
        backends = usable(setting)
        sent = call == "tool" and bool(backends)
        chained = consult(q, backends)
        # S1: never allow; deny iff a tool call (Bash/MCP, .env or not) with no usable backend.
        assert decision != "allow"
        assert (decision == "deny") == (call in ("tool", "env") and not backends)
        # S2: the only ways to a prompt.
        by_s2 = call in ("badInput", "pythonMissing", "crash") or (
            sent and (chained[0] == "down" or int(chained[1]) > ESCALATE)  # type: ignore[call-overload]
        )
        assert (decision == "ask") == by_s2
        # S11: Clef first when usable; Clef down falls back to Jev; an unusable Clef is skipped.
        if sent:
            if setting[0] == "ok" and q_clef[0] == "answer":
                assert chained == q_clef
            elif setting[1] == "ok":
                assert chained == q_jev
            else:
                assert chained == q_clef
        # S12: halt iff no usable backend, the same for session start, prompt gate and hook.
        halted = not backends
        assert prompt_gate_model(setting) == halted
        session = session_model(setting)
        assert (session is not None and session[1]) == halted
        if halted and call in ("tool", "env"):
            assert (decision, warning) == ("deny", "noClassifier")


# One test per `example` of SimpleGate.lean (same names); the model is checked, and where the hook can
# be driven the real hook too. `setting` is (clef, jev); `q` is one result for both or a dict.
EXAMPLES = {
    "answer_0": (JEV_ONLY, "tool", ("answer", 0), ("none", None)),
    "answer_500": (JEV_ONLY, "tool", ("answer", 500), ("none", None)),
    "answer_501": (JEV_ONLY, "tool", ("answer", 501), ("ask", None)),
    "down_timeout": (JEV_ONLY, "tool", ("down", "timeout"), ("ask", "timeout")),
    "down_http": (JEV_ONLY, "tool", ("down", "http"), ("ask", "http")),
    "down_network": (JEV_ONLY, "tool", ("down", "network"), ("ask", "network")),
    "down_malformed": (JEV_ONLY, "tool", ("down", "malformed"), ("ask", "malformed")),
    "clef_answers": (("ok", "ok"), "tool", {"clef": ("answer", 900), "jev": ("answer", 0)}, ("ask", None)),
    "clef_down_jev_answers": (("ok", "ok"), "tool", {"clef": ("down", "network"), "jev": ("answer", 900)}, ("ask", None)),
    "both_down_last_warning": (("ok", "ok"), "tool", {"clef": ("down", "network"), "jev": ("down", "timeout")}, ("ask", "timeout")),
    "clef_off_jev_ok": (("off", "ok"), "tool", {"clef": ("answer", 900), "jev": ("answer", 0)}, ("none", None)),
    "clef_bad_jev_ok": (("bad", "ok"), "tool", {"clef": ("answer", 900), "jev": ("answer", 0)}, ("none", None)),
    "both_off_deny": (("off", "off"), "tool", ("answer", 0), ("deny", "noClassifier")),
    "clef_off_jev_no_key_deny": (JEV_NO_KEY, "tool", ("answer", 0), ("deny", "noClassifier")),
    "halt_env_mention_denies": (("off", "off"), "env", ("answer", 0), ("deny", "noClassifier")),
    "env_mention_not_sent": (JEV_ONLY, "env", ("answer", 900), ("none", "envReference")),
    "bad_input_on": (JEV_ONLY, "badInput", ("answer", 0), ("ask", "badInput")),
    "bad_input_misconfigured": (JEV_NO_KEY, "badInput", ("answer", 0), ("ask", "badInput")),
    "python_missing": (JEV_ONLY, "pythonMissing", ("answer", 0), ("ask", "pythonMissing")),
    "crash": (JEV_ONLY, "crash", ("answer", 0), ("ask", "internalError")),
}
LIVE_CASE = {
    "answer_0": ("on", "bash", "risk-0"),
    "answer_500": ("on", "bash", "risk-eq-escalate"),
    "answer_501": ("on", "bash", "risk-gt-escalate"),
    "down_timeout": ("on", "bash", "timeout"),
    "down_http": ("on", "bash", "http500"),
    "down_network": ("on", "bash", "network"),
    "down_malformed": ("on", "bash", "malformed"),
}
# Driven through both mocks: name -> (stdin, clef case, jev case); the setting comes from EXAMPLES.
CHAIN_CASE = {
    "clef_answers": (TOOLS["bash"], "risk-gt-escalate", "risk-0"),
    "clef_down_jev_answers": (TOOLS["bash"], "network", "risk-gt-escalate"),
    "both_down_last_warning": (TOOLS["bash"], "network", "timeout"),
    "clef_off_jev_ok": (TOOLS["bash"], "risk-gt-escalate", "risk-0"),
    "clef_bad_jev_ok": (TOOLS["bash"], "risk-gt-escalate", "risk-0"),
    "both_off_deny": (TOOLS["bash"], "risk-0", "risk-0"),
    "clef_off_jev_no_key_deny": (TOOLS["bash"], "risk-0", "risk-0"),
    "halt_env_mention_denies": (ENV_CALLS["bash-env"], "risk-0", "risk-0"),
}


CHAIN_REASON = {
    "clef_answers": "clef: destructive p=0.30 risk=0.60",
    "clef_down_jev_answers": "jev: destructive p=0.30 risk=0.60",
    "both_down_last_warning": "clef-unavailable: network error; jev-unavailable: timeout after 300 ms",
    "both_off_deny": "no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: disabled by GATE_JEV_ENABLED=0",
    "clef_off_jev_no_key_deny": "no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set",
}


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_example(name, request):
    setting, call, q, expected = EXAMPLES[name]
    assert model(setting, call, q) == expected
    pending(name)
    hook = request.getfixturevalue("hook")
    if name in LIVE_CASE:
        s, tool, jev_case = LIVE_CASE[name]
        answer, extra, _ = JEV[jev_case]
        out = hook(s, TOOLS[tool], answer, extra)
        assert (out.decision, out.warning) == expected
    elif name in CHAIN_CASE:
        stdin, clef_case, jev_case = CHAIN_CASE[name]
        out = request.getfixturevalue("chain")(setting, stdin, clef_case, jev_case)
        assert (out.decision, out.warning) == expected
        # S11: the reason names the backend that decided (or each one that failed).
        assert out.reason == CHAIN_REASON.get(name, out.reason)
    elif name == "env_mention_not_sent":
        out = hook("on", ENV_CALLS["bash-env"], JEV["risk-gt-escalate"][0])
        assert (out.decision, out.warning) == expected
    elif name in ("bad_input_on", "bad_input_misconfigured"):
        out = hook("on" if name.endswith("on") else "noKey", "not json", None)
        assert (out.decision, out.warning) == expected


def test_example_session_start_halt():
    assert session_model(JEV_NO_KEY) == (["jev"], True)
    pending("session_start_halt")
    message = json.loads(HookRunner.run_session({"GATE_CLEF_ENABLED": "0"}).stdout)["systemMessage"]
    assert "TYPESAFE_API_KEY is not set" in message
    assert "this session is halted" in message


def test_example_session_start_clef_bad():
    assert session_model(("bad", "ok")) == (["clef"], False)
    pending("session_start_clef_bad")
    r = HookRunner.run_session({"GATE_CLEF_URL": "http://example.com", "TYPESAFE_API_KEY": "k"})
    message = json.loads(r.stdout)["systemMessage"]
    assert "GATE_CLEF_URL" in message
    assert "halted" not in message


def test_example_session_start_on():
    assert session_model(JEV_ONLY) is None
    assert HookRunner.run_session({"TYPESAFE_API_KEY": "k", "GATE_CLEF_ENABLED": "0"}).stdout == ""


def test_example_prompt_gate_block():
    assert prompt_gate_model(JEV_NO_KEY) is True
    pending("prompt_gate_block")
    r = HookRunner.run_prompt({"GATE_CLEF_ENABLED": "0"})  # C4 adds the UserPromptSubmit runner
    assert json.loads(r.stdout)["decision"] == "block"


def test_example_prompt_gate_pass():
    assert prompt_gate_model(("ok", "ok")) is False
    pending("prompt_gate_pass")
    assert HookRunner.run_prompt({"TYPESAFE_API_KEY": "k"}).stdout == ""


EFFECTIVE_IDS = ["effective_ask_allow", "effective_ask_deny", "effective_none_allow", "effective_deny_allow"]


@pytest.mark.parametrize(
    ("decision", "claude", "expected"),
    [("ask", "allow", "ask"), ("ask", "deny", "deny"), ("none", "allow", "allow"), ("deny", "allow", "deny")],
    ids=EFFECTIVE_IDS,
)
def test_example_effective(decision, claude, expected):
    assert effective(decision, claude) == expected


# Traceability: every `example` of the Lean model carries a `-- example: NAME` line, and each NAME has a
# test of the same name here (a key of EXAMPLES, a `test_example_<NAME>` function or a parametrize id).
LEAN = DG / "formal" / "SimpleGate.lean"
NAMED = re.compile(r"^-- example: (\w+)\n(example\b.*)$", re.MULTILINE)


def lean_examples() -> dict[str, str]:
    return {m[1]: m[2] for m in NAMED.finditer(LEAN.read_text(encoding="utf-8"))}


def tested_names() -> set[str]:
    own = {n.removeprefix("test_example_") for n in globals() if n.startswith("test_example_") and n != "test_example_effective"}
    return own | set(EXAMPLES) | set(EFFECTIVE_IDS)


def test_every_lean_example_is_named():
    text = LEAN.read_text(encoding="utf-8")
    assert len(lean_examples()) == len(re.findall(r"^example\b", text, re.MULTILINE)) > 0


def test_every_lean_example_has_a_test_of_the_same_name():
    missing = sorted(set(lean_examples()) - tested_names())
    assert not missing, f"Lean examples without a test of the same name: {missing}"


def test_every_named_test_has_a_lean_example():
    stale = sorted(tested_names() - set(lean_examples()))
    assert not stale, f"tests named like Lean examples that no longer exist: {stale}"
