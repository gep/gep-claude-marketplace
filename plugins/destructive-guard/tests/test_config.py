"""Configuration from env variables: GATE_ENABLED, GATE_ENV_GUARD and GATE_LOG, and env-guard.sh.

The Jev variables are tested with the Jev units (test_jev_units.py)."""

import json
import subprocess

import pytest

from destructive_guard.config import GateConfig
from destructive_guard.errors import ConfigError
from dg_testkit.entrypoints import Entrypoints
from dg_testkit.envs import IsolatedEnv

CLEAN = {"GATE_ENABLED": "", "GATE_ENV_GUARD": "", "GATE_LOG": "", "CLAUDE_PROJECT_DIR": ""}


def read_config(env, **opts):
    return GateConfig.read(env, **opts)


def test_read_defaults():
    defaults = {"enabled": True, "envGuard": True, "log": None, "clefEnabled": True, "jevEnabled": True}
    assert read_config({}) == defaults
    # empty values mean "unset"
    assert read_config(CLEAN) == defaults


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("GATE_ENABLED", "enabled"),
        ("GATE_ENV_GUARD", "envGuard"),
        ("GATE_CLEF_ENABLED", "clefEnabled"),
        ("GATE_JEV_ENABLED", "jevEnabled"),
    ],
)
def test_boolean_variables(name, key):
    for v in ["1", "true", "YES", "on", " 1 "]:
        assert read_config({name: v})[key] is True, f"{name}={v}"
    for v in ["0", "false", "No", "OFF"]:
        assert read_config({name: v})[key] is False, f"{name}={v}"
    for v in ["2", "maybe", "-1"]:
        with pytest.raises(ConfigError, match=name):
            read_config({name: v})


def test_log_absolute_path_as_given():
    assert read_config({"GATE_LOG": "/tmp/g.log"})["log"] == "/tmp/g.log"


def test_relative_log_resolves_against_project_dir():
    c = read_config({"CLAUDE_PROJECT_DIR": "/proj", "GATE_LOG": "logs/g.jsonl"}, cwd="/elsewhere")
    assert c["log"] == "/proj/logs/g.jsonl"


def test_relative_log_resolves_against_git_root_else_cwd(tmp_path):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "apps" / "web").mkdir(parents=True)
    root = root.resolve()
    assert read_config({"GATE_LOG": "g.jsonl"}, cwd=str(root / "apps" / "web"))["log"] == str(root / "g.jsonl")
    bare = tmp_path / "gate-nogit"
    bare.mkdir()
    assert read_config({"GATE_LOG": "g.jsonl"}, cwd=str(bare))["log"] == str(bare / "g.jsonl")


# ---------------------------------------------------------------------------
# env-guard.sh
# ---------------------------------------------------------------------------


def guard(file_path, env=None):
    return subprocess.run(
        Entrypoints.env_guard(),
        input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": file_path}}),
        capture_output=True,
        text=True,
        env=IsolatedEnv.clean(env or {}),
        check=False,
    ).returncode


def test_env_guard_matches_env_file_names_only():
    for f in ["/p/.env", "/p/.env.local", "/p/web/.env.production", "/p/prod.env", ".env"]:
        assert guard(f) == 2, f
    for f in [
        "/p/.env.example",
        "/p/src/config.environment.ts",
        "/p/.envrc.md",
        "/p/docs/env.md",
        "/p/.env/notes.txt",
        "/p/src/dotenv.ts",
    ]:
        assert guard(f) == 0, f


def test_env_guard_switches():
    assert guard("/p/.env", {"GATE_ENV_GUARD": "0"}) == 0
    assert guard("/p/.env", {"GATE_ENABLED": "false"}) == 0
    assert guard("/p/.env", {"GATE_ENV_GUARD": "1"}) == 2
    assert guard("/p/.env", {"GATE_ENV_GUARD": "maybe"}) == 2


def test_log_path_survives_a_bad_flag():
    env = {**CLEAN, "GATE_LOG": "/tmp/g.log", "GATE_ENV_GUARD": "maybe"}
    assert GateConfig.log_path(env, cwd="/") == "/tmp/g.log"
