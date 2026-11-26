"""HookOut: the only writer of PreToolUse output; it cannot say allow, denies only when halted (S1) and keeps S3."""

import json

import pytest

from destructive_guard.down import Down
from destructive_guard.outcome import Failure, HookOut

OFF = "GATE_ENABLED=0 turns the gate off."


def test_none_is_no_output():
    assert HookOut("none").to_json() is None


def test_ask_bytes():
    assert HookOut("ask", "jev: destructive p=0.90 risk=0.95").to_json() == (
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask",'
        '"permissionDecisionReason":"jev: destructive p=0.90 risk=0.95"}}'
    )


def test_ask_with_warning_bytes():
    out = HookOut.unavailable(Failure("jev", Down.HTTP, "http 503"))
    assert out.to_json() == (
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask",'
        '"permissionDecisionReason":"jev-unavailable: http 503"},'
        f'"systemMessage":"destructive-guard: Jev returned http 503; asking you instead. {OFF}"}}'
    )


def test_warning_only_bytes():
    out = HookOut("none", warning=Down.ENV_REFERENCE)
    assert out.to_json() == (
        '{"systemMessage":"destructive-guard: call mentions a .env file and was not sent to a classifier; '
        "Claude Code's permissions apply\"}"
    )


@pytest.mark.parametrize(
    ("kind", "detail", "reason", "message"),
    [
        (Down.TIMEOUT, "timeout after 1500 ms", "jev-unavailable: timeout after 1500 ms", f"Jev did not answer in time (1500 ms); asking you instead. {OFF}"),
        (Down.NETWORK, "network error", "jev-unavailable: network error", f"Jev could not be reached; asking you instead. {OFF}"),
        (Down.MALFORMED, "malformed answer", "jev-unavailable: malformed answer", f"Jev's answer was malformed; asking you instead. {OFF}"),
    ],
)
def test_jev_unavailable_texts(kind, detail, reason, message):
    out = HookOut.unavailable(Failure("jev", kind, detail))
    assert (out.decision, out.reason, out.message()) == ("ask", reason, f"destructive-guard: {message}")


@pytest.mark.parametrize(
    ("kind", "detail", "reason", "message"),
    [
        (Down.BAD_INPUT, None, "plugin-fault: bad hook input", f"could not read the hook input; asking you instead. {OFF}"),
        (
            Down.PYTHON_MISSING,
            None,
            "plugin-fault: no Python >= 3.14 found (set GATE_INFRA_PYTHON)",
            f"no Python >= 3.14 found (set GATE_INFRA_PYTHON); asking you instead. {OFF}",
        ),
        (Down.INTERNAL_ERROR, "KeyError", "plugin-fault: internal error: KeyError", f"internal error (KeyError); asking you instead. {OFF}"),
    ],
)
def test_plugin_fault_texts(kind, detail, reason, message):
    out = HookOut.fault(kind, detail)
    assert (out.decision, out.reason, out.message()) == ("ask", reason, f"destructive-guard: {message}")


def test_output_never_says_allow_or_deny_unless_halted():
    for out in [
        HookOut("none"),
        HookOut("ask", "r"),
        HookOut.fault(Down.BAD_INPUT),
        HookOut("none", warning=Down.ENV_REFERENCE),
        HookOut.unavailable(Failure("clef", Down.NETWORK, "network error"), Failure("jev", Down.HTTP, "http 503")),
    ]:
        text = out.to_json()
        assert text is None or ('"allow"' not in text and '"deny"' not in text and "continue" not in text)
    with pytest.raises(TypeError):
        HookOut("allow", "r", None, None, extra=1)  # type: ignore[call-arg]


def test_chain_unavailable_names_each_backend():
    out = HookOut.unavailable(Failure("clef", Down.NETWORK, "network error"), Failure("jev", Down.TIMEOUT, "timeout after 1500 ms"))
    assert (out.decision, out.warning) == ("ask", Down.TIMEOUT)  # the model: the last backend's reason
    assert out.reason == "clef-unavailable: network error; jev-unavailable: timeout after 1500 ms"
    assert out.message() == (
        f"destructive-guard: Clef could not be reached; Jev did not answer in time (1500 ms); asking you instead. {OFF}"
    )


@pytest.mark.parametrize(
    ("kind", "detail", "text"),
    [
        (Down.TIMEOUT, "timeout after 3000 ms", "Clef did not answer in time (3000 ms)"),
        (Down.HTTP, "http 404", "Clef returned http 404"),
        (Down.NETWORK, "network error", "Clef could not be reached"),
        (Down.MALFORMED, "malformed answer", "Clef's answer was malformed"),
    ],
)
def test_clef_alone_unavailable_texts(kind, detail, text):
    out = HookOut.unavailable(Failure("clef", kind, detail))
    assert out.reason == f"clef-unavailable: {detail}"
    assert out.message() == f"destructive-guard: {text}; asking you instead. {OFF}"


def test_unavailable_needs_a_backend_failure():
    with pytest.raises(ValueError):
        HookOut.unavailable()
    with pytest.raises(ValueError):
        Failure("jev", Down.BAD_INPUT, "x")


HALT_MESSAGE = (
    "destructive-guard: no classifier available (clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set): "
    "this session is halted. Fix the setting or set GATE_ENABLED=0."
)


def test_halt_bytes():
    out = HookOut.halt(["clef: disabled by GATE_CLEF_ENABLED=0", "jev: TYPESAFE_API_KEY is not set"])
    assert (out.decision, out.warning) == ("deny", Down.NO_CLASSIFIER)
    assert out.message() == HALT_MESSAGE
    assert json.loads(out.to_json()) == {
        "continue": False,
        "stopReason": HALT_MESSAGE,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "no-classifier: clef: disabled by GATE_CLEF_ENABLED=0; jev: TYPESAFE_API_KEY is not set",
        },
        "systemMessage": HALT_MESSAGE,
    }


def test_deny_only_with_no_classifier():
    with pytest.raises(ValueError):
        HookOut("deny", "r")
    with pytest.raises(ValueError):
        HookOut("deny", "r", Down.NETWORK)
    with pytest.raises(ValueError):
        HookOut("deny", None, Down.NO_CLASSIFIER)
    for decision, reason in (("ask", "r"), ("none", None)):
        with pytest.raises(ValueError):
            HookOut(decision, reason, Down.NO_CLASSIFIER)  # type: ignore[arg-type]


def test_ask_needs_a_reason_and_none_cannot_have_one():
    with pytest.raises(ValueError):
        HookOut("ask")
    with pytest.raises(ValueError):
        HookOut("none", "reason")


def test_ask_with_a_warning_that_does_not_ask_raises():
    with pytest.raises(ValueError):
        HookOut("ask", "r", Down.ENV_REFERENCE)


@pytest.mark.parametrize("kind", [d for d in Down if d.asks])
def test_warning_that_asks_needs_ask(kind):
    with pytest.raises(ValueError):
        HookOut("none", warning=kind)


def test_down_classification_matches_the_model():
    assert {d for d in Down if d.jev_unavailable} == {Down.TIMEOUT, Down.HTTP, Down.NETWORK, Down.MALFORMED}
    assert {d for d in Down if d.plugin_fault} == {Down.BAD_INPUT, Down.PYTHON_MISSING, Down.INTERNAL_ERROR}
    assert [d for d in Down if not d.asks] == [Down.ENV_REFERENCE, Down.NO_CLASSIFIER]
    assert json.loads(HookOut.fault(Down.BAD_INPUT).to_json())["hookSpecificOutput"]["permissionDecision"] == "ask"
