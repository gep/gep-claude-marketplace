"""Units of the launcher (gated_dev.launch)."""

import json
import re
from pathlib import Path

import pytest

from gated_dev.errors import LaunchError
from gated_dev.launch.cli import LaunchCli
from gated_dev.launch.naming import LaunchNaming
from gated_dev.launch.orca import OrcaWorktrees
from gated_dev.launch.terminal import TerminalCommands
from gated_dev.launch.ticket_project import TicketProject

FIXTURES = Path(__file__).parent / "fixtures"


def project(tmp_path, *, config=None, tickets=None):
    """The JS fixture `repo({config, tickets})` without git: .claude/gated-dev-simple-workflow/config.json and ticket files."""
    if config is not None:
        (tmp_path / ".claude" / "gated-dev-simple-workflow").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".claude" / "gated-dev-simple-workflow" / "config.json").write_text(config if isinstance(config, str) else json.dumps(config))
    for key, text in (tickets or {}).items():
        (tmp_path / "docs" / "tickets" / key).mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "tickets" / key / "ticket.md").write_text(text)
    return tmp_path


# --- names and commands -------------------------------------------------------


def test_build_shell_command_quotes_cwd_sets_key_and_names_the_session():
    assert (
        TerminalCommands.build_shell_command(cwd="/a b/c", key="PROJ-1", args=["--resume"])
        == """cd '/a b/c' && TICKET_FLOW_KEY=PROJ-1 claude --name "PROJ-1 resume" "/ticket PROJ-1 --resume\""""
    )
    assert (
        TerminalCommands.build_shell_command(cwd="/x", key="PROJ-1", summary="Fix it", remote_control=True)
        == """cd '/x' && TICKET_FLOW_KEY=PROJ-1 claude --name "PROJ-1 · Fix it" --remote-control "PROJ-1 · Fix it" "/ticket PROJ-1\""""
    )


def test_build_shell_command_escapes_single_quotes_in_cwd():
    command = TerminalCommands.build_shell_command(cwd="/it's/a\"b\\c", key="K-1")
    assert re.match(r"^cd '/it'\\''s/a\"b\\c' &&", command)


@pytest.mark.parametrize("key", ["", "a b", "K;rm", "K/1", "a..b", "$(x)", "-K"])
def test_build_shell_command_rejects_unsafe_keys(key):
    with pytest.raises(LaunchError, match="Invalid ticket key"):
        TerminalCommands.build_shell_command(cwd="/x", key=key)


@pytest.mark.parametrize(
    ("opts", "message"),
    [
        ({"cwd": "x", "key": "K-1"}, "absolute"),
        ({"cwd": "/x\n", "key": "K-1"}, "absolute"),
        ({"cwd": "/x", "key": "K-1", "args": ["--yolo"]}, "not allowed"),
        ({"cwd": "/x", "key": "K-1", "remote_control": "yes"}, "boolean"),
    ],
)
def test_build_shell_command_rejects_other_args_relative_cwd_bad_remote_control(opts, message):
    with pytest.raises(LaunchError, match=message):
        TerminalCommands.build_shell_command(**opts)


@pytest.mark.parametrize(
    ("model", "flag"),
    [("opus", "--model opus "), ("haiku", "--model haiku "), ("claude-sonnet-4-5", "--model claude-sonnet-4-5 "), ("claude-opus-4.1-x", "--model claude-opus-4.1-x "), (None, "")],
)
def test_build_shell_command_puts_the_model_before_the_name(model, flag):
    assert (
        TerminalCommands.build_shell_command(cwd="/x", key="K-1", model=model)
        == f"""cd '/x' && TICKET_FLOW_KEY=K-1 claude {flag}--name "K-1" "/ticket K-1\""""
    )


@pytest.mark.parametrize(
    "model",
    ["", "gpt-4", "Opus", "claude-", "claude-Opus", "opus ", "opus --dangerously-skip-permissions", "claude-x y", "'opus'", '"opus"', "claude-x'", "$(whoami)", "claude-$(id)", "opus;rm", "claude-x;rm -rf /", "opus\\n", "opus\nx", "claude-x\n", "`id`", 1, True],
)
def test_build_shell_command_rejects_unsafe_models(model):
    with pytest.raises(LaunchError, match="Invalid model"):
        TerminalCommands.build_shell_command(cwd="/x", key="K-1", model=model)


def test_build_shell_command_passes_the_mode_in_the_ticket_prompt():
    assert (
        TerminalCommands.build_shell_command(cwd="/x", key="K-1", args=["--mode", "heavy"], model="opus")
        == """cd '/x' && TICKET_FLOW_KEY=K-1 claude --model opus --name "K-1" "/ticket K-1 --mode heavy\""""
    )


@pytest.mark.parametrize(
    "args",
    [["--mode"], ["--mode", "auto"], ["--mode", "heavy now"], ["--mode", "$(id)"], ["--mode", '"x"'], ["heavy"], ["--mode", "light", "--mode"]],
)
def test_build_shell_command_rejects_a_mode_without_a_mode_name(args):
    with pytest.raises(LaunchError, match="not allowed|--mode needs"):
        TerminalCommands.build_shell_command(cwd="/x", key="K-1", args=args)


def test_short_summary_strips_unsafe_characters_and_cuts_at_a_word_boundary():
    assert LaunchNaming.short_summary('Say "hi" $(rm -rf /) `x` \\ !') == "Say hi (rm -rf /) x"
    assert LaunchNaming.short_summary(None) == ""
    assert LaunchNaming.short_summary("Export reports as CSV with filters and pagination support") == "Export reports as CSV with filters and"
    assert LaunchNaming.short_summary("a" * 50) == "a" * 40


def test_session_name_key_resume_and_summary():
    assert LaunchNaming.session_name(key="K-1") == "K-1"
    assert LaunchNaming.session_name(key="K-1", args=["--resume"]) == "K-1 resume"
    assert LaunchNaming.session_name(key="K-1", summary="Do it!") == "K-1 · Do it"


def test_a_hostile_title_cannot_reach_the_shell_or_applescript():
    summary = "\"; do shell script \"x\" `whoami` $HOME '"
    command = TerminalCommands.build_shell_command(cwd="/x", key="K-1", summary=summary)
    assert command.count('"') == 4
    assert not re.search(r"[`$']", command[len("cd '/x' ") :])
    script = TerminalCommands.build_apple_script(command=command, key="K-1", summary=summary)
    name_lines = [line for line in script.split("\n") if "set name to" in line]
    assert name_lines
    for line in name_lines:
        assert re.match(r'^set name to "[^"\\]*"$', line.strip())


def test_slugify_follows_the_skill_rule():
    assert LaunchNaming.slugify("Add slugify helper, and drop the legacy folder") == "add-slugify-helper-and-drop-the-legacy-f"
    assert LaunchNaming.slugify("  --Hello  World--  ") == "hello-world"
    assert LaunchNaming.slugify("!!!") == "work"
    assert LaunchNaming.slugify("a" * 39 + " b") == "a" * 39


@pytest.mark.parametrize(
    ("env", "host"),
    [
        ({"TERM_PROGRAM": "Orca"}, "orca"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iterm"),
        ({"ORCA_TERMINAL_HANDLE": "t1"}, "orca"),
        ({"ITERM_SESSION_ID": "w0t0p0:X"}, "iterm"),
        ({"TERM_PROGRAM": "iTerm.app", "ORCA_TERMINAL_HANDLE": "t1"}, "iterm"),
        ({"TERM_PROGRAM": "Orca", "ITERM_SESSION_ID": "w0t0p0:X"}, "orca"),
        ({"TERM_PROGRAM": "Apple_Terminal"}, None),
        ({}, None),
    ],
)
def test_detect_host_term_program_first_then_orca_handle_then_iterm_session(env, host):
    assert TerminalCommands.detect_host(env) == host


@pytest.mark.parametrize(
    ("env", "guid"),
    [
        ({"ITERM_SESSION_ID": "w0t1p0:AB-12"}, "AB-12"),
        ({"ITERM_SESSION_ID": "garbage"}, None),
        ({"ITERM_SESSION_ID": 'w0:a"b'}, None),
        ({}, None),
    ],
)
def test_iterm_session_id_takes_the_guid_and_ignores_unusable_values(env, guid):
    assert TerminalCommands.iterm_session_id(env) == guid


def test_build_applescript_escapes_names_the_tab_and_targets_the_launchers_window():
    assert LaunchNaming.apple_script_string('a"b\\c') == '"a\\"b\\\\c"'
    script = TerminalCommands.build_apple_script(command="cd '/x' && echo \"hi\"", key="K-1", session_id="S-1")
    assert re.search(r'set name to "K-1"', script)
    assert re.search(r"write text \"cd '/x' && echo \\\"hi\\\"\"", script)
    assert re.search(r'if id of s is "S-1" then', script)
    assert not re.search(r"repeat with w", TerminalCommands.build_apple_script(command="c", key="K-1"))


def test_build_orca_args_selector_then_launchers_workspace_then_path():
    base = {"cwd": "/r", "command": "c", "key": "K-1"}

    def at(**o):
        return TerminalCommands.build_orca_args(**{**base, **o})[3]

    assert at(selector="id:w1", workspace_id="ws") == "id:w1"
    assert at(workspace_id="ws") == "id:ws"
    assert at() == "path:/r"
    assert TerminalCommands.build_orca_args(**base, summary="T")[4:] == ["--title", "K-1 · T", "--command", "c", "--json"]


def test_orca_worktree_args_name_the_worktree_and_branch_after_key_and_title():
    assert OrcaWorktrees("/r").orca_worktree_args(key="K-1", title="Do it", base="main") == [
        "worktree", "create", "--repo", "path:/r", "--name", "k-1-do-it",
        "--branch", "K-1/do-it", "--base-branch", "origin/main", "--setup", "skip", "--json",
    ]  # fmt: skip


# --- config and tickets -------------------------------------------------------


def test_read_config_defaults_overrides_and_invalid_content(tmp_path):
    def cfg(n, **kw):
        return TicketProject(project(tmp_path / str(n), **kw)).read_config()

    d = cfg("d")
    assert d["ticketsDir"] == "docs/tickets"
    assert d["keyPattern"].search("proj-1")
    assert d["baseBranch"] is None
    assert d["orchestratorRules"] is None
    assert d["tracker"] is None
    assert cfg("r", config={"orchestratorRules": ".claude/rules.md"})["orchestratorRules"] == ".claude/rules.md"
    c = cfg("c", config={"ticketsDir": "t", "keyPattern": "^[A-Z]+-\\d+$", "baseBranch": "develop", "models": {}})
    assert c["ticketsDir"] == "t"
    assert c["baseBranch"] == "develop"
    assert not c["keyPattern"].search("proj-1")
    bad_cases = [
        ("{", "invalid JSON"),
        ("[]", "JSON object"),
        ({"nope": 1}, 'unknown key "nope"'),
        ({"ticketsDir": "../x"}, "ticketsDir"),
        ({"ticketsDir": "/abs"}, "ticketsDir"),
        ({"keyPattern": "("}, "keyPattern"),
        ({"baseBranch": "a b"}, "baseBranch"),
        ({"orchestratorRules": "../rules.md"}, "orchestratorRules"),
        ({"orchestratorRules": "/abs/rules.md"}, "orchestratorRules"),
        ({"orchestratorRules": 3}, "orchestratorRules"),
    ]
    for n, (bad, message) in enumerate(bad_cases):
        with pytest.raises(LaunchError, match=message):
            cfg(f"bad{n}", config=bad)


MODE_DEFAULTS = {"heavy": {"session": "opus"}, "medium": {"session": "sonnet"}, "light": {"session": "sonnet"}}


def test_read_config_location_new_path_legacy_fallback_and_precedence(tmp_path):
    def legacy(root, config):
        (root / ".claude").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "gated-dev.json").write_text(json.dumps(config))
        return root

    fresh = TicketProject(project(tmp_path / "new", config={"ticketsDir": "t"}))
    assert (fresh.read_config()["ticketsDir"], fresh.config_notice) == ("t", None)
    assert fresh.config_file()[0] == tmp_path / "new" / ".claude" / "gated-dev-simple-workflow" / "config.json"

    old = TicketProject(legacy(tmp_path / "old", {"ticketsDir": "o"}))
    assert old.read_config()["ticketsDir"] == "o"
    assert re.search(r"gated-dev\.json is deprecated: it is moved to .*gated-dev-simple-workflow/config\.json", old.config_notice)

    both = TicketProject(legacy(project(tmp_path / "both", config={"ticketsDir": "n"}), {"ticketsDir": "o"}))
    assert both.read_config()["ticketsDir"] == "n"
    assert re.search(r"gated-dev\.json is ignored", both.config_notice)

    none = TicketProject(project(tmp_path / "none"))
    assert (none.read_config()["ticketsDir"], none.config_notice) == ("docs/tickets", None)


def test_config_template_lists_every_key_with_the_defaults(tmp_path):
    template = json.loads(TicketProject.CONFIG_TEMPLATE.read_text())
    assert set(template) == TicketProject.CONFIG_KEYS
    assert TicketProject(project(tmp_path / "t", config=template)).read_config() == TicketProject(project(tmp_path / "e", config={})).read_config()
    assert template["models"] == TicketProject.MODEL_DEFAULTS
    assert template["modes"] == TicketProject.MODE_DEFAULTS
    skill = (Path(__file__).parents[1] / "skills" / "ticket" / "SKILL.md").read_text()
    [line] = [x for x in skill.splitlines() if "Default `riskPaths`:" in x]
    listed = line.split("Default `riskPaths`:")[1].split(". Any output")[0]
    assert template["riskPaths"] == re.findall(r"`([^`]+)`", listed)


def test_read_config_mode_defaults_and_overrides(tmp_path):
    d = TicketProject(project(tmp_path / "d")).read_config()
    assert (d["mode"], d["modes"], d["modeRules"]) == ("auto", MODE_DEFAULTS, [])
    for n, mode in enumerate(("auto", "heavy", "medium", "light")):
        assert TicketProject(project(tmp_path / f"m{n}", config={"mode": mode})).read_config()["mode"] == mode
    config = {"modes": {"light": {"session": "opus"}, "heavy": {"session": "claude-opus-4-5"}}, "modeRules": ["billing is medium"]}
    c = TicketProject(project(tmp_path / "c", config=config)).read_config()
    assert c["modes"] == {"heavy": {"session": "claude-opus-4-5"}, "medium": {"session": "sonnet"}, "light": {"session": "opus"}}
    assert c["modeRules"] == ["billing is medium"]
    assert TicketProject(project(tmp_path / "e", config={"modes": {"light": {}}})).read_config()["modes"] == MODE_DEFAULTS
    assert MODE_DEFAULTS == TicketProject.MODE_DEFAULTS


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"mode": "huge"}, "mode must be"),
        ({"mode": None}, "mode must be"),
        ({"modes": []}, "modes must be an object"),
        ({"modes": {"huge": {"session": "opus"}}}, 'unknown mode "huge"'),
        ({"modes": {"light": "haiku"}}, "modes.light must be an object"),
        ({"modes": {"light": {"model": "haiku"}}}, 'unknown key "model"'),
        ({"modes": {"light": {"session": 3}}}, "session must be"),
        ({"modes": {"light": {"session": "opus 4"}}}, "session must be"),
        ({"modes": {"light": {"session": "opus;ls"}}}, "session must be"),
        ({"modes": {"light": {"session": "claude-x'y"}}}, "session must be"),
        ({"modes": {"light": {"session": "gpt"}}}, "session must be"),
        ({"modeRules": "billing"}, "modeRules must be"),
        ({"modeRules": ["ok", ""]}, "modeRules must be"),
        ({"modeRules": [1]}, "modeRules must be"),
    ],
)
def test_read_config_mode_invalid(tmp_path, bad, message):
    with pytest.raises(LaunchError, match=message):
        TicketProject(project(tmp_path, config=bad)).read_config()


def test_is_model_accepts_aliases_and_ids_only():
    assert all(LaunchNaming.is_model(m) for m in ("opus", "sonnet", "haiku", "claude-opus-4-5", "claude-sonnet-4.5"))
    assert not any(LaunchNaming.is_model(m) for m in ("", "gpt", "Opus", "claude-", "claude-A", "a b", "opus;x", 'x"', None, 3))


TRACKER_DEFAULTS = {
    "type": "jira",
    "site": None,
    "statuses": {"inProgress": "In Progress", "inReview": "In Review", "done": "Done"},
    "startFrom": None,
    "assign": True,
    "comments": True,
    "baseFromParent": None,
}


def test_read_config_tracker_defaults(tmp_path):
    config = TicketProject(project(tmp_path, config={"tracker": {"type": "jira"}})).read_config()
    assert config["tracker"] == TRACKER_DEFAULTS
    assert config["keyPattern"].pattern == r"^[A-Z][A-Z0-9]*-\d+$"


def test_read_config_tracker_overrides(tmp_path):
    tracker = {
        "type": "jira",
        "site": "acme.example.net",
        "statuses": {"inProgress": "Doing", "inReview": None},
        "startFrom": ["To Do", "Backlog"],
        "assign": False,
        "comments": False,
        "baseFromParent": "feature/{parent}/*",
    }
    config = TicketProject(project(tmp_path / "a", config={"tracker": tracker})).read_config()
    assert config["tracker"] == {
        **tracker,
        "statuses": {"inProgress": "Doing", "inReview": None, "done": "Done"},
    }
    explicit = TicketProject(project(tmp_path / "b", config={"tracker": {"type": "jira"}, "keyPattern": "^[A-Z]+-\\d+$"})).read_config()
    assert explicit["keyPattern"].pattern == "^[A-Z]+-\\d+$"


@pytest.mark.parametrize(
    ("tracker", "message"),
    [
        ("jira", "tracker.*object"),
        ({}, "type"),
        ({"type": "linear"}, "linear"),
        ({"type": "jira", "nope": 1}, "nope"),
        ({"type": "jira", "statuses": {"nope": "X"}}, "nope"),
        ({"type": "jira", "statuses": {"inProgress": None}}, "inProgress"),
        ({"type": "jira", "statuses": {"inProgress": ""}}, "inProgress"),
        ({"type": "jira", "statuses": {"done": None}}, "done"),
        ({"type": "jira", "statuses": {"done": ""}}, "done"),
        ({"type": "jira", "startFrom": "To Do"}, "startFrom"),
        ({"type": "jira", "startFrom": ["To Do", ""]}, "startFrom"),
        ({"type": "jira", "startFrom": [1]}, "startFrom"),
        ({"type": "jira", "assign": "yes"}, "assign"),
        ({"type": "jira", "comments": 1}, "comments"),
        ({"type": "jira", "site": "https://x"}, "site"),
        ({"type": "jira", "site": "x/y"}, "site"),
        ({"type": "jira", "baseFromParent": "feature/*"}, "baseFromParent"),
        ({"type": "jira", "baseFromParent": "feature/{parent}/a b"}, "baseFromParent"),
        ({"type": "jira", "baseFromParent": "feature/{parent}/$x"}, "baseFromParent"),
    ],
)
def test_read_config_tracker_invalid(tmp_path, tracker, message):
    with pytest.raises(LaunchError, match=message):
        TicketProject(project(tmp_path, config={"tracker": tracker})).read_config()


def test_read_config_without_tracker_is_unchanged(tmp_path):
    config = TicketProject(project(tmp_path, config={"ticketsDir": "t"})).read_config()
    assert config["tracker"] is None
    assert config["keyPattern"].pattern == TicketProject.DEFAULT_KEY_PATTERN
    assert TicketProject(project(tmp_path / "none")).read_config()["tracker"] is None


def test_launch_ticket_with_tracker_and_no_ticket_md_starts_in_the_main_checkout(tmp_path):
    from gated_dev.completed_run import CompletedRun
    from gated_dev.launch.launcher import TicketLauncher

    def fake_run(file, argv):
        return CompletedRun("", "")

    env = {"TERM_PROGRAM": "iTerm.app"}
    config = TicketProject(project(tmp_path / "a", config={"tracker": {"type": "jira"}})).read_config()
    plan = TicketLauncher.launch_ticket(root=tmp_path / "a", key="PROJ-1", config=config, dry_run=True, env=env, run=fake_run)
    assert plan["mode"] == "main-checkout"
    assert plan["summary"] is None
    assert plan["note"] == "no ticket.md yet; the session fetches it from Jira"

    root = project(tmp_path / "b", config={"tracker": {"type": "jira"}}, tickets={"PROJ-1": "# Fix it\n"})
    config = TicketProject(root).read_config()
    plan = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, env=env, run=fake_run)
    assert plan["mode"] == "main-checkout"
    assert plan["summary"] == "Fix it"
    assert plan["note"] is None



@pytest.mark.parametrize("mode", ["heavy", "medium", "light"])
@pytest.mark.parametrize("session", ["haiku", "claude-haiku-5-5"])
def test_read_config_rejects_haiku_as_a_session_model(tmp_path, mode, session):
    root = project(tmp_path, config={"modes": {mode: {"session": session}}})
    with pytest.raises(LaunchError, match=rf"modes\.{mode}\.session must not be haiku.*sonnet or opus"):
        TicketProject(root).read_config()


@pytest.mark.parametrize("mode", ["heavy", "medium", "light"])
@pytest.mark.parametrize("session", ["sonnet", "opus", "claude-opus-5-5"])
def test_read_config_accepts_sonnet_and_opus_as_a_session_model(tmp_path, mode, session):
    root = project(tmp_path, config={"modes": {mode: {"session": session}}})
    assert TicketProject(root).read_config()["modes"][mode] == {"session": session}


def test_launch_ticket_with_a_ticket_mode_starts_the_modes_session_model(tmp_path):
    from gated_dev.completed_run import CompletedRun
    from gated_dev.launch.launcher import TicketLauncher

    def fake_run(file, argv):
        return CompletedRun("", "")

    env = {"TERM_PROGRAM": "iTerm.app"}
    root = project(tmp_path, config={"modes": {"light": {"session": "claude-sonnet-5-5"}}}, tickets={"PROJ-1": "# Fix it\n"})
    config = TicketProject(root).read_config()
    for mode, model in [("heavy", "opus"), ("medium", "sonnet"), ("light", "claude-sonnet-5-5")]:
        plan = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, ticket_mode=mode, env=env, run=fake_run)
        assert (plan["ticketMode"], plan["model"]) == (mode, model)
        assert plan["command"].endswith(f'claude --model {model} --name "PROJ-1 · Fix it" "/ticket PROJ-1 --mode {mode}"')
    plain = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, env=env, run=fake_run)
    assert (plain["ticketMode"], plain["model"]) == (None, None)
    assert "--model" not in plain["command"] and "--mode" not in plain["command"]
    for bad in ("auto", "heavy; rm", ""):
        with pytest.raises(LaunchError, match="mode must be"):
            TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, ticket_mode=bad, env=env, run=fake_run)
    # A config that slipped past validation still cannot put its session value on the shell line.
    hostile = {**config, "modes": {**config["modes"], "light": {"session": "opus; rm -rf ~"}}}
    with pytest.raises(LaunchError, match="Invalid model"):
        TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=hostile, dry_run=True, ticket_mode="light", env=env, run=fake_run)


FLOW_DEFAULTS = {
    "max": 1,
    "jql": "parent = {parent} ORDER BY Rank ASC",
    "pickRules": [],
    "pollSeconds": 60,
    "staleHours": 3,
    "lostWatchMinutes": 10,
    "remoteControl": False,
}


def test_read_config_flow_defaults_and_overrides(tmp_path):
    assert TicketProject(project(tmp_path / "none", config={"tracker": {"type": "jira"}})).read_config()["flow"] is None
    defaults = TicketProject(project(tmp_path / "empty", config={"tracker": {"type": "jira"}, "flow": {}})).read_config()
    assert defaults["flow"] == FLOW_DEFAULTS
    flow = {
        "max": 10,
        "jql": "parent = {parent} AND status = 'To Do'",
        "pickRules": ["prefer the earliest phase"],
        "pollSeconds": 15,
        "staleHours": 0.5,
        "lostWatchMinutes": 2.5,
        "remoteControl": True,
    }
    config = TicketProject(project(tmp_path / "set", config={"tracker": {"type": "jira"}, "flow": flow})).read_config()
    assert config["flow"] == flow


@pytest.mark.parametrize(
    ("flow", "message"),
    [
        ("yes", "flow must be an object"),
        ({"nope": 1}, 'flow has unknown key "nope"'),
        ({"max": 0}, "flow.max"),
        ({"max": 11}, "flow.max"),
        ({"max": 1.5}, "flow.max"),
        ({"max": True}, "flow.max"),
        ({"max": "2"}, "flow.max"),
        ({"jql": "status = 'To Do'"}, "flow.jql.*{parent}"),
        ({"jql": 5}, "flow.jql"),
        ({"pickRules": "earliest first"}, "flow.pickRules"),
        ({"pickRules": ["ok", 3]}, "flow.pickRules"),
        ({"pollSeconds": 14}, "flow.pollSeconds"),
        ({"pollSeconds": 3601}, "flow.pollSeconds"),
        ({"pollSeconds": 60.5}, "flow.pollSeconds"),
        ({"pollSeconds": True}, "flow.pollSeconds"),
        ({"staleHours": 0}, "flow.staleHours"),
        ({"staleHours": -1}, "flow.staleHours"),
        ({"staleHours": "3"}, "flow.staleHours"),
        ({"lostWatchMinutes": 0}, "flow.lostWatchMinutes"),
        ({"lostWatchMinutes": False}, "flow.lostWatchMinutes"),
        ({"remoteControl": "no"}, "flow.remoteControl"),
        ({"remoteControl": 0}, "flow.remoteControl"),
    ],
)
def test_read_config_flow_invalid(tmp_path, flow, message):
    with pytest.raises(LaunchError, match=message):
        TicketProject(project(tmp_path, config={"tracker": {"type": "jira"}, "flow": flow})).read_config()


def test_read_config_flow_without_tracker_is_a_config_error(tmp_path):
    with pytest.raises(LaunchError, match="flow requires tracker"):
        TicketProject(project(tmp_path, config={"flow": {"max": 2}})).read_config()


def test_launch_ticket_summary_titles_a_tracker_ticket_without_ticket_md(tmp_path):
    from gated_dev.completed_run import CompletedRun
    from gated_dev.launch.launcher import TicketLauncher

    def fake_run(file, argv):
        return CompletedRun("", "")

    env = {"TERM_PROGRAM": "iTerm.app"}
    root = project(tmp_path, config={"tracker": {"type": "jira"}})
    config = TicketProject(root).read_config()
    titled = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, summary="Add export", env=env, run=fake_run)
    assert titled["summary"] == "Add export"
    assert titled["title"] == LaunchNaming.session_name(key="PROJ-1", summary="Add export")
    assert titled["title"] != "PROJ-1"
    plain = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, env=env, run=fake_run)
    assert plain["summary"] is None
    assert plain["title"] == LaunchNaming.session_name(key="PROJ-1", summary=None) == "PROJ-1"


def test_check_key_applies_key_pattern_and_the_safe_character_set(tmp_path):
    strict = TicketProject(project(tmp_path / "strict", config={"keyPattern": "^[A-Z]+-\\d+$"})).read_config()
    assert TicketProject.check_key("PROJ-1", strict) is None
    with pytest.raises(LaunchError, match="not a valid ticket key"):
        TicketProject.check_key("proj-1", strict)
    loose = TicketProject(project(tmp_path / "loose", config={"keyPattern": "."})).read_config()
    for key in ("a;b", "a..b"):
        with pytest.raises(LaunchError, match="not a valid"):
            TicketProject.check_key(key, loose)


def test_read_ticket_title_front_matter_base_missing_file_and_missing_title(tmp_path):
    root = project(
        tmp_path,
        tickets={
            "K-1": "---\nbase: release/2.x\nowner: me\n---\n\n# Export CSV\n\nText",
            "K-2": "# Plain\n",
            "K-3": "no heading\n",
            "K-4": "---\nbase: 'a b'\n---\n# T\n",
            "K-5": '---\nbase: "dev"\n---\n# T\n',
        },
    )
    config = TicketProject(root).read_config()

    def read(key):
        return TicketProject(root).read_ticket(key=key, config=config)

    t = read("K-1")
    assert t["title"] == "Export CSV"
    assert t["base"] == "release/2.x"
    assert read("K-2")["base"] is None
    with pytest.raises(LaunchError, match='no "# " title'):
        read("K-3")
    with pytest.raises(LaunchError, match="no ticket file"):
        read("K-9")
    with pytest.raises(LaunchError, match="invalid base"):
        read("K-4")
    assert read("K-5")["base"] == "dev"


def test_list_ticket_keys_lists_ticket_dirs_whose_key_matches(tmp_path):
    root = project(
        tmp_path / "a",
        config={"keyPattern": "^[A-Z]+-\\d+$"},
        tickets={"K-2": "# a", "K-1": "# b", "notes": "# c"},
    )
    (root / "docs/tickets/K-3").mkdir()
    assert TicketProject(root).list_ticket_keys(config=TicketProject(root).read_config()) == ["K-1", "K-2"]
    empty = project(tmp_path / "b")
    assert TicketProject(empty).list_ticket_keys(config=TicketProject(empty).read_config()) == []


# --- CLI arguments --------------------------------------------------------------


def test_parse_args_keys_or_all_flags_errors():
    assert LaunchCli.parse_args(["-n", "--remote-control", "A-1", "B-2"]) == {
        "keys": ["A-1", "B-2"], "all": False, "dryRun": True, "remoteControl": True, "cwd": None, "mode": None, "help": False,
    }  # fmt: skip
    assert LaunchCli.parse_args(["--all", "--cwd", "/r"])["cwd"] == "/r"
    assert LaunchCli.parse_args(["--mode", "light", "A-1"])["mode"] == "light"
    assert LaunchCli.parse_args(["--help"])["help"]
    for argv, message in [
        ([], "keys or --all"),
        (["--all", "A-1"], "keys or --all"),
        (["--force", "A-1"], "unknown option"),
        (["--cwd"], "needs a directory"),
        (["--mode"], "--mode needs"),
        (["--mode", "auto", "A-1"], "--mode needs"),
        (["--mode", "Heavy", "A-1"], "--mode needs"),
        (["--mode", "heavy;rm", "A-1"], "--mode needs"),
    ]:
        with pytest.raises(LaunchError, match=message):
            LaunchCli.parse_args(argv)



# --- helpers pinned against the JS implementation (generated once, incl. non-ASCII) ---------

HELPERS = json.loads((FIXTURES / "launch_helpers.json").read_text(encoding="utf-8"))
HELPER_FUNCS = {
    "slugify": LaunchNaming.slugify,
    "shortSummary": LaunchNaming.short_summary,
    "shellQuote": LaunchNaming.shell_quote,
    "appleScriptString": LaunchNaming.apple_script_string,
    "sessionName": lambda value: LaunchNaming.session_name(**value),
}


@pytest.mark.parametrize("row", HELPERS, ids=lambda r: f"{r['fn']}-{json.dumps(r['input'], ensure_ascii=True)[:30]}")
def test_helpers_match_the_js_implementation(row):
    assert HELPER_FUNCS[row["fn"]](row["input"]) == row["expected"]


def test_read_config_dry_run_gate(tmp_path):
    assert TicketProject(project(tmp_path / "none")).read_config()["dryRunGate"] is False
    assert TicketProject(project(tmp_path / "on", config={"dryRunGate": True})).read_config()["dryRunGate"] is True
    with pytest.raises(LaunchError, match="dryRunGate must be true or false"):
        TicketProject(project(tmp_path / "bad", config={"dryRunGate": "yes"})).read_config()


def test_read_config_dev_tier(tmp_path):
    assert TicketProject(project(tmp_path / "none")).read_config()["devTier"] == "auto"
    for tier in ("auto", "godzilla", "workhorse", "pawn"):
        assert TicketProject(project(tmp_path / tier, config={"devTier": tier})).read_config()["devTier"] == tier
    with pytest.raises(LaunchError, match="devTier must be one of"):
        TicketProject(project(tmp_path / "bad", config={"devTier": "giant"})).read_config()


def test_read_config_models_defaults_and_overrides(tmp_path):
    defaults = TicketProject(project(tmp_path / "none")).read_config()["models"]
    assert defaults["devGodzilla"] == "opus"
    assert defaults["devWorkhorse"] == "sonnet"
    assert defaults["devPawn"] == "haiku"
    assert defaults["reviewRisk"] == "opus"
    given = {"devPawn": "claude-haiku-4-5", "plan": "sonnet"}
    models = TicketProject(project(tmp_path / "set", config={"models": given})).read_config()["models"]
    assert models == {**defaults, **given}


@pytest.mark.parametrize(
    ("models", "message"),
    [
        ("opus", "models must be an object"),
        ({"bogus": "opus"}, 'models has unknown key "bogus"'),
        ({"devPawn": 3}, "models.devPawn must be opus, sonnet, haiku or a claude-"),
        ({"devPawn": "haiku; rm -rf /"}, "models.devPawn must be opus, sonnet, haiku or a claude-"),
        ({"plan": "claude-"}, "models.plan must be opus, sonnet, haiku or a claude-"),
    ],
)
def test_read_config_models_invalid(tmp_path, models, message):
    with pytest.raises(LaunchError, match=message):
        TicketProject(project(tmp_path, config={"models": models})).read_config()


def test_read_config_models_dev_is_replaced(tmp_path):
    with pytest.raises(LaunchError) as error:
        TicketProject(project(tmp_path, config={"models": {"dev": "sonnet"}})).read_config()
    assert str(error.value).endswith("models.dev was replaced by models.devWorkhorse (and devGodzilla, devPawn)")


def test_orca_worktree_args_make_the_worktree_a_child_of_the_launchers_workspace():
    argv = OrcaWorktrees("/r").orca_worktree_args(key="K-1", title="Do it", base="main", parent="repo::/r")
    assert argv[-3:] == ["--parent-worktree", "worktree:repo::/r", "--json"]


def _tracker_launch(tmp_path, *, tracker=None, summary="Add export", parent=None, heads="", env=None):
    from gated_dev.completed_run import CompletedRun
    from gated_dev.launch.launcher import TicketLauncher

    calls = []

    def fake_run(file, argv):
        calls.append([file, *argv])
        if argv[2:4] == ["ls-remote", "--heads"]:
            return CompletedRun(heads, "")
        return CompletedRun("", "")

    root = project(tmp_path, config={"tracker": {"type": "jira", **(tracker or {})}, "baseBranch": "main"})
    config = TicketProject(root).read_config()
    env = {"TERM_PROGRAM": "Orca", "ORCA_WORKTREE_ID": "repo::/main"} if env is None else env
    plan = TicketLauncher.launch_ticket(root=root, key="PROJ-1", config=config, dry_run=True, summary=summary, parent=parent, env=env, run=fake_run)
    return plan, calls


def test_tracker_ticket_with_summary_gets_a_child_orca_worktree_before_the_session(tmp_path):
    plan, _ = _tracker_launch(tmp_path)
    assert plan["mode"] == "orca-worktree"
    assert plan["base"] == "main"
    assert "--name proj-1-add-export --branch PROJ-1/add-export --base-branch origin/main" in plan["command"]
    assert "--parent-worktree worktree:repo::/main" in plan["command"]


def test_tracker_ticket_base_from_parent(tmp_path):
    one, calls = _tracker_launch(tmp_path / "one", tracker={"baseFromParent": "feature/{parent}/*"}, parent="PROJ-0", heads="abc\trefs/heads/feature/PROJ-0/x\n")
    assert one["mode"] == "orca-worktree" and one["base"] == "feature/PROJ-0/x"
    assert ["git", "-C", str(tmp_path / "one"), "ls-remote", "--heads", "origin", "feature/PROJ-0/*"] in calls
    none, _ = _tracker_launch(tmp_path / "none", tracker={"baseFromParent": "feature/{parent}/*"}, parent="PROJ-0")
    assert none["mode"] == "orca-worktree" and none["base"] == "main"
    several, _ = _tracker_launch(tmp_path / "several", tracker={"baseFromParent": "feature/{parent}/*"}, parent="PROJ-0", heads="a\trefs/heads/feature/PROJ-0/x\nb\trefs/heads/feature/PROJ-0/y\n")
    assert several["mode"] == "main-checkout"  # H3 is asked in the session
    unknown, _ = _tracker_launch(tmp_path / "unknown", tracker={"baseFromParent": "feature/{parent}/*"})
    assert unknown["mode"] == "main-checkout"


def test_tracker_ticket_without_summary_or_outside_orca_starts_in_the_main_checkout(tmp_path):
    assert _tracker_launch(tmp_path / "a", summary=None)[0]["mode"] == "main-checkout"
    assert _tracker_launch(tmp_path / "b", env={"TERM_PROGRAM": "iTerm.app"})[0]["mode"] == "main-checkout"
