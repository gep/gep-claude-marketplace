"""Contract: the check CLI (a call check goes through the hook's decision against Jev)."""

import json
import re
import shlex
from pathlib import Path

import pytest

from dg_testkit.check_cli import CheckRunner
from dg_testkit.jev_mock import JevMock

RISKY = JevMock.probs(0.1, 0.1, 0.6, 0.2)
SAFE = JevMock.probs(0.01, 0.97, 0.01, 0.01)


def check_json(*args, env=None, cwd=None):
    proc = CheckRunner.run_check("--json", *args, env=env or {}, cwd=cwd)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def jev_env(jev):
    return {"TYPESAFE_API_KEY": "sekret-123", "GATE_JEV_URL": jev.url, "GATE_JEV_PROMPT": "cloud-change"}


def test_parse_args_command_mcp_and_config(jev, jev_env, tmp_path):
    jev.answer(SAFE)
    assert check_json("git push -f", env=jev_env, cwd=tmp_path)["command"] == "git push -f"
    assert check_json("--", "rm", "-rf", "x", env=jev_env, cwd=tmp_path)["command"] == "rm -rf x"
    m = check_json("--mcp", "mcp__db__q", '{"a":1}', env=jev_env, cwd=tmp_path)
    assert (m["tool"], m["input"]) == ("mcp__db__q", {"a": 1})
    assert check_json("--mcp", "mcp__x__y", env=jev_env, cwd=tmp_path)["input"] == {}
    assert "backends" in check_json(cwd=tmp_path)
    assert "backends" in check_json("--config", cwd=tmp_path)


@pytest.mark.parametrize(
    "argv",
    [["--nope"], ["--mcp"], ["--mcp", "Bash"], ["--mcp", "mcp__x__y", "{"], ["--mcp", "mcp__x__y", "[1]"], ["--"], ["--cwd", "x"]],
    ids=json.dumps,
)
def test_usage_errors_exit_2(argv):
    proc = CheckRunner.run_check(*argv)
    assert proc.returncode == 2
    assert re.match(r"^usage error: ", proc.stdout)


def test_jev_option_is_gone():
    proc = CheckRunner.run_check("--jev", "ls")
    assert proc.returncode == 2
    assert "no longer needed" in proc.stdout


def test_risky_call_asks_and_safe_call_has_no_decision(jev, jev_env, tmp_path):
    jev.answer(RISKY)
    r = check_json("terraform apply", env=jev_env, cwd=tmp_path)
    assert (r["decision"], r["reason"]) == ("ask", "jev: cloud_or_data_change p=0.60 risk=0.80")
    assert r["risk"] == pytest.approx(0.8)
    assert jev.hits == 1
    jev.answer(SAFE)
    r = check_json("--mcp", "mcp__db__execute_sql", '{"query":"SELECT 1"}', env=jev_env, cwd=tmp_path)
    assert (r["decision"], r["reason"]) == ("none", None)


def test_unavailable_jev_asks_with_a_warning(jev, jev_env, tmp_path):
    jev.answer(kind="status", status=503)
    r = check_json("ls", env=jev_env, cwd=tmp_path)
    assert r["decision"] == "ask"
    assert r["reason"] == "clef-unavailable: network error; jev-unavailable: http 503"
    assert [f["backend"] for f in r["fallback"]] == ["clef", "jev"]
    assert r["backend"] is None
    assert "asking you instead" in r["warning"]


def test_env_file_call_is_not_sent(jev, jev_env, tmp_path):
    r = check_json("cat .env", env=jev_env, cwd=tmp_path)
    assert r["decision"] == "none"
    assert ".env" in r["warning"]
    assert jev.hits == 0


def test_disabled_gate_does_not_contact_anything(jev, jev_env, tmp_path):
    off = check_json("git push -f", env={**jev_env, "GATE_ENABLED": "0"}, cwd=tmp_path)
    assert off["decision"] == "none"
    assert "disabled" in off["reason"]
    assert jev.hits == 0


def test_clef_answers_and_jev_is_not_contacted(jev, clef, jev_env, tmp_path):
    clef.answer(RISKY)
    r = check_json("terraform apply", env={**jev_env, "GATE_CLEF_URL": clef.url}, cwd=tmp_path)
    assert (r["decision"], r["backend"], r["fallback"]) == ("ask", "clef", [])
    assert (clef.hits, jev.hits) == (1, 0)


def test_clef_down_falls_back_to_jev(jev, jev_env, tmp_path):
    jev.answer(RISKY)
    r = check_json("terraform apply", env=jev_env, cwd=tmp_path)  # Clef points at a closed port
    assert (r["decision"], r["backend"]) == ("ask", "jev")
    assert [(f["backend"], f["error"]) for f in r["fallback"]] == [("clef", "network")]
    assert jev.hits == 1


def test_both_down_asks_with_both_failures(jev, clef, jev_env, tmp_path):
    clef.answer(kind="status", status=500)
    jev.answer(kind="status", status=503)
    r = check_json("ls", env={**jev_env, "GATE_CLEF_URL": clef.url}, cwd=tmp_path)
    assert r["decision"] == "ask"
    assert [(f["backend"], f["error"]) for f in r["fallback"]] == [("clef", "http 500"), ("jev", "http 503")]
    assert (clef.hits, jev.hits) == (1, 1)


@pytest.mark.parametrize(
    "env",
    [
        {"GATE_CLEF_ENABLED": "0", "GATE_JEV_ENABLED": "0"},
        {"GATE_CLEF_ENABLED": "0", "TYPESAFE_API_KEY": ""},  # Jev without key
        {"GATE_CLEF_ENABLED": "maybe"},  # unreadable flag
    ],
    ids=["both-off", "clef-off-no-key", "bad-flag"],
)
def test_halted_call_contacts_nothing(jev, clef, env, tmp_path):
    full = {"GATE_JEV_URL": jev.url, "GATE_CLEF_URL": clef.url, "TYPESAFE_API_KEY": "k", **env}
    r = check_json("ls", env=full, cwd=tmp_path)
    assert r["decision"] == "deny"
    assert r["halted"].startswith("destructive-guard: no classifier available")
    assert "this session is halted" in r["halted"]
    assert (clef.hits, jev.hits) == (0, 0)
    text = CheckRunner.run_check("ls", env=full, cwd=tmp_path).stdout.splitlines()
    assert text[1] == "decision: deny (halted)" and text[2].startswith("halted:   destructive-guard: no classifier")


def config_json(env, tmp_path):
    return check_json("--config", env=env, cwd=tmp_path)


def test_config_both_usable(jev, clef, jev_env, tmp_path):
    env = {**jev_env, "GATE_CLEF_URL": clef.url, "GATE_CLEF_MODEL": "clef-x", "GATE_CLEF_TIMEOUT_MS": "900"}
    proc = CheckRunner.run_check("--json", "--config", env=env, cwd=tmp_path)
    j = json.loads(proc.stdout)
    assert j["order"] == ["clef", "jev"] and j["halted"] is None
    assert j["backends"]["clef"] == {
        "enabled": True, "state": "usable", "usable": True, "url": clef.url,
        "model": "clef-x", "timeoutMs": 900, "apiKey": "[none]",
    }
    assert (j["backends"]["jev"]["state"], j["backends"]["jev"]["apiKey"]) == ("usable", "[set]")
    assert j["shared"]["escalateThreshold"] == 0.5 and j["shared"]["prompt"] == "cloud-change"
    assert (j["enabled"], j["envGuard"], j["log"]) == (True, True, None)
    assert "sekret-123" not in proc.stdout
    text = CheckRunner.run_check("--config", env=env, cwd=tmp_path).stdout
    assert "order:         clef -> jev" in text
    assert "api key [set]" in text and "api key [none]" in text and "halted" not in text
    assert "sekret-123" not in text


def test_config_clef_key_is_never_printed(clef, jev_env, tmp_path):
    env = {**jev_env, "GATE_CLEF_URL": clef.url, "GATE_CLEF_API_KEY": "clef-sekret"}
    proc = CheckRunner.run_check("--json", "--config", env=env, cwd=tmp_path)
    assert json.loads(proc.stdout)["backends"]["clef"]["apiKey"] == "[set]"
    assert "clef-sekret" not in proc.stdout
    assert "clef-sekret" not in CheckRunner.run_check("--config", env=env, cwd=tmp_path).stdout


def test_config_clef_bad_jev_usable(jev_env, tmp_path):
    j = config_json({**jev_env, "GATE_CLEF_TIMEOUT_MS": "x"}, tmp_path)
    clef_b = j["backends"]["clef"]
    assert (clef_b["usable"], clef_b["enabled"]) == (False, True)
    assert clef_b["state"].startswith("clef: GATE_CLEF_TIMEOUT_MS")
    assert j["backends"]["jev"]["usable"] is True and j["halted"] is None


def test_config_jev_without_key(tmp_path):
    j = config_json({}, tmp_path)
    assert j["backends"]["clef"]["usable"] is True
    jev_b = j["backends"]["jev"]
    assert (jev_b["usable"], jev_b["apiKey"]) == (False, "[none]")
    assert "TYPESAFE_API_KEY" in jev_b["state"]
    assert j["halted"] is None
    assert "TYPESAFE_API_KEY" in j["sessionWarning"]


def test_config_both_off_is_halted(tmp_path):
    env = {"GATE_CLEF_ENABLED": "0", "GATE_JEV_ENABLED": "0"}
    j = config_json(env, tmp_path)
    assert [j["backends"][n]["state"] for n in ("clef", "jev")] == [
        "disabled by GATE_CLEF_ENABLED=0", "disabled by GATE_JEV_ENABLED=0",
    ]
    assert "clef: disabled by GATE_CLEF_ENABLED=0; jev: disabled by GATE_JEV_ENABLED=0" in j["halted"]
    text = CheckRunner.run_check("--config", env=env, cwd=tmp_path).stdout
    assert "halted:        destructive-guard: no classifier available" in text


def test_config_bad_flag_is_halted(tmp_path):
    j = config_json({"GATE_CLEF_ENABLED": "maybe", "TYPESAFE_API_KEY": "k"}, tmp_path)
    assert "GATE_CLEF_ENABLED" in j["error"]
    assert "backends" not in j
    assert "GATE_CLEF_ENABLED" in j["halted"]


def test_config_bad_shared_value_marks_both_bad(jev_env, tmp_path):
    j = config_json({**jev_env, "GATE_JEV_ESCALATE_THRESHOLD": "7"}, tmp_path)
    assert not j["backends"]["clef"]["usable"] and not j["backends"]["jev"]["usable"]
    assert "GATE_JEV_ESCALATE_THRESHOLD" in j["shared"]["error"]
    assert j["halted"] is not None


def test_text_output_never_prints_the_key(jev, jev_env, tmp_path):
    jev.answer(RISKY)
    text = CheckRunner.run_check("git push --force", env=jev_env, cwd=tmp_path)
    assert text.returncode == 0
    assert re.search(r"decision: ask", text.stdout)
    conf = CheckRunner.run_check(env=jev_env, cwd=tmp_path)
    assert re.search(r"api key \[set\]", conf.stdout)
    assert "sekret-123" not in text.stdout + conf.stdout


def test_text_report_shapes(jev, jev_env, tmp_path):
    def text(*args, env=jev_env):
        return CheckRunner.run_check(*args, env=env, cwd=tmp_path).stdout.splitlines()

    jev.answer(RISKY)
    out = text("git push --force origin main")
    assert out[:4] == [
        "Bash: git push --force origin main",
        "decision: ask",
        "reason:   jev: cloud_or_data_change p=0.60 risk=0.80",
        "answered: jev",
    ]
    assert re.fullmatch(r"fallback: clef: network \(\d+ ms\)", out[4]) and len(out) == 5
    jev.answer(SAFE)
    assert text("ls")[1] == "decision: none (risk 0.02 \u2264 0.5)"
    jev.answer(kind="status", status=503)
    out = text("ls")
    assert out[1].startswith("decision: ask (clef-unavailable: network error; jev-unavailable: http 503")
    assert out[2].startswith("fallback: clef: network") and out[3].startswith("fallback: jev: http 503")
    assert out[4].startswith("warning:  ")
    out = text("cat .env")
    assert out[1].startswith("decision: none \u2014 warning: ") and ".env" in out[1]
    assert len(out) == 2


SKILL = Path(__file__).resolve().parents[1] / "skills" / "check" / "SKILL.md"


def documented_invocations() -> list[list[str]]:
    """The argv after check.py of every example in SKILL.md's bash blocks."""
    blocks = re.findall(r"```bash\n(.*?)```", SKILL.read_text(), re.DOTALL)
    found = []
    for line in "\n".join(blocks).splitlines():
        if "check.py" in line and not line.lstrip().startswith("#"):
            found.append(shlex.split(line.split("check.py", 1)[1].lstrip('" ')))
    return found


def test_documented_invocations_pass_the_gate(jev, jev_env, tmp_path):
    """The example command lines themselves mention no .env and parse; each call goes to Jev."""
    jev.answer(SAFE)
    examples = documented_invocations()
    assert len(examples) >= 3
    for argv in examples:
        assert ".env" not in " ".join(argv)
        proc = CheckRunner.run_check("--json", *argv, env=jev_env, cwd=tmp_path)
        assert proc.returncode == 0, (argv, proc.stdout)
        assert json.loads(proc.stdout)["backends"] if "--config" in argv else True
    # The invocation lines are Bash calls too: the hook would send them to Jev, not skip them.
    before = jev.hits
    for argv in examples:
        line = 'python3 "${CLAUDE_PLUGIN_ROOT}/lib/check.py" ' + shlex.join(argv)
        r = check_json(line, env=jev_env, cwd=tmp_path)
        assert r["decision"] == "none" and r["risk"] is not None
    assert jev.hits - before == len(examples)
