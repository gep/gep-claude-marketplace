"""Clef and Jev settings: defaults, flags, URL rule, timeouts, shared values, issues (Lean `Setting`)."""

import pytest

from destructive_guard.errors import ConfigError, JevConfigError
from destructive_guard.jev.backends import Backends
from destructive_guard.jev.client import JevClient

KEY = {"TYPESAFE_API_KEY": "k-fictional"}


def read(env):
    return Backends.read(env)


def names(b):
    return [c["backend"] for c in b.usable]


def test_defaults_both_usable_clef_first():
    b = read(KEY)
    assert names(b) == ["clef", "jev"]
    assert b.issues == []
    clef, jev = b.usable
    assert clef["url"] == "http://localhost:11434/v1/systemone"
    assert (clef["model"], clef["timeoutMs"], clef["apiKey"]) == ("clef", 3000, None)
    assert (jev["url"], jev["model"], jev["timeoutMs"], jev["apiKey"]) == (
        "https://api.typesafe.ai/v1/systemone",
        "jev-latest",
        1500,
        "k-fictional",
    )
    assert clef["escalateThreshold"] == jev["escalateThreshold"] == 0.5
    assert clef["prompt"] == jev["prompt"]


def test_clef_key_is_optional_and_read():
    assert read({}).clef.kind == "ok"
    assert read({"GATE_CLEF_API_KEY": " tok "}).clef.config["apiKey"] == "tok"


def test_jev_without_key_only_clef_usable():
    b = read({})
    assert names(b) == ["clef"]
    assert b.jev.kind == "bad"
    assert [(n, e.kind) for n, e in b.issues] == [("jev", "noKey")]
    assert str(b.issues[0][1]) == "jev: TYPESAFE_API_KEY is not set"


def test_flags():
    b = read({**KEY, "GATE_CLEF_ENABLED": "0"})
    assert (b.clef.kind, names(b)) == ("off", ["jev"])
    b = read({**KEY, "GATE_JEV_ENABLED": "no"})
    assert (b.jev.kind, names(b)) == ("off", ["clef"])
    with pytest.raises(ConfigError, match="GATE_CLEF_ENABLED"):
        read({"GATE_CLEF_ENABLED": "maybe"})


def test_both_disabled_nothing_usable_no_issues():
    b = read({"GATE_CLEF_ENABLED": "0", "GATE_JEV_ENABLED": "0"})
    assert b.usable == [] and b.issues == []
    assert (b.clef.kind, b.jev.kind) == ("off", "off")


def test_disabled_jev_without_key_is_not_an_issue():
    b = read({"GATE_JEV_ENABLED": "0"})
    assert names(b) == ["clef"] and b.issues == []


@pytest.mark.parametrize(
    ("backend", "var"), [("clef", "GATE_CLEF_URL"), ("jev", "GATE_JEV_URL")]
)
def test_url_rule_per_backend(backend, var):
    base = {**KEY}
    state = lambda url: read({**base, var: url}).state(backend)
    for ok in [
        "https://classifier.example.test/v1",
        "http://localhost:9/x",
        "http://127.0.0.1/x",
        "http://[::1]:5/x",
    ]:
        assert state(ok).kind == "ok", ok
    for url, text in [
        ("http://classifier.example.test/v1", "must use https"),
        ("ftp://x.example.test", "must use https"),
        ("http://", "not a valid URL"),
        ("http://h:99999/", "not a valid URL"),
    ]:
        st = state(url)
        assert st.kind == "bad", url
        assert text in str(st.error) and str(st.error).startswith(f"{backend}: {var} ")
        assert st.error.kind == "badUrl"


def test_bad_url_of_one_backend_leaves_the_other():
    b = read({**KEY, "GATE_CLEF_URL": "http://remote.example.test/"})
    assert names(b) == ["jev"]
    assert str(b.issues[0][1]) == "clef: GATE_CLEF_URL must use https"


@pytest.mark.parametrize(
    ("var", "backend"),
    [("GATE_CLEF_TIMEOUT_MS", "clef"), ("GATE_JEV_TIMEOUT_MS", "jev")],
)
def test_timeout_range(var, backend):
    for v in ["1", "30000", "250"]:
        assert read({**KEY, var: v}).state(backend).config["timeoutMs"] == int(v)
    for v in ["0", "30001", "abc", "-5", "1.5"]:
        st = read({**KEY, var: v}).state(backend)
        assert st.kind == "bad", v
        assert str(st.error) == f"{backend}: {var} must be an integer from 1 to 30000"


def test_model_variables():
    b = read({**KEY, "GATE_CLEF_MODEL": "clef-x", "GATE_JEV_MODEL": "jev-x"})
    assert [c["model"] for c in b.usable] == ["clef-x", "jev-x"]


@pytest.mark.parametrize(
    ("env", "kind"),
    [
        ({"GATE_JEV_ESCALATE_THRESHOLD": "2"}, "badValue"),
        ({"GATE_JEV_PROMPT": "no-such-prompt"}, "badPrompt"),
    ],
)
def test_shared_bad_value_makes_both_unusable(env, kind):
    b = read({**KEY, **env})
    assert b.usable == []
    assert [(n, e.kind) for n, e in b.issues] == [("clef", kind), ("jev", kind)]
    assert all(str(e).startswith(f"{n}: ") for n, e in b.issues)


def test_shared_bad_value_does_not_touch_a_disabled_backend():
    b = read({**KEY, "GATE_CLEF_ENABLED": "0", "GATE_JEV_ESCALATE_THRESHOLD": "2"})
    assert b.clef.kind == "off" and [n for n, _ in b.issues] == ["jev"]


def test_backend_specific_error_comes_before_the_shared_one():
    b = read({"GATE_JEV_ESCALATE_THRESHOLD": "2"})
    assert [(n, e.kind) for n, e in b.issues] == [
        ("clef", "badValue"),
        ("jev", "noKey"),
    ]


def test_state_mirrors_lean_bstate():
    b = read({"GATE_CLEF_ENABLED": "0"})
    assert (b.clef.kind, b.clef.config, b.clef.error) == ("off", None, None)
    assert (
        b.jev.kind == "bad"
        and b.jev.config is None
        and isinstance(b.jev.error, JevConfigError)
    )
    assert read(KEY).jev.kind == "ok"


def test_jev_client_builds_a_request_from_a_clef_config():
    cfg = read({}).clef.config
    seen = []

    class Res:
        status = 500
        headers = None

        def close(self):
            pass

    def opener(request, timeout):
        seen.append((request.full_url, request.data, timeout))
        return Res()

    out = JevClient().ask_jev({"command": "ls"}, cfg, opener=opener)
    assert out["error"] == "http 500"
    url, body, timeout = seen[0]
    assert url == "http://localhost:11434/v1/systemone"
    assert b'"model": "clef"' in body or b'"model":"clef"' in body
    assert timeout <= 3.0
