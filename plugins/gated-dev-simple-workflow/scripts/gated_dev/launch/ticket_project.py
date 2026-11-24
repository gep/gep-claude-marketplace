"""The ticket project: its config, tickets and runs."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from gated_dev.errors import LaunchError
from gated_dev.launch.naming import LaunchNaming
from gated_dev.process import ProcessRunner


class TicketProject:
    """Config, ticket files and runs of the repository at `root`."""

    DEFAULT_KEY_PATTERN = "^[A-Za-z][A-Za-z0-9._-]*$"
    CONFIG_KEYS = frozenset(
        (
            "ticketsDir", "keyPattern", "baseBranch", "setupCommand", "formatCommand", "riskPaths", "escalationExtra", "models",
            "orchestratorRules", "tracker", "flow", "dryRunGate", "mode", "modes", "modeRules", "devTier",
        )
    )
    DEV_TIERS = frozenset(("auto", "godzilla", "workhorse", "pawn"))
    MODEL_DEFAULTS: ClassVar[dict[str, str]] = {
        "spec": "sonnet",
        "plan": "opus",
        "review": "sonnet",
        "reviewRisk": "opus",
        "devGodzilla": "opus",
        "devWorkhorse": "sonnet",
        "devPawn": "haiku",
    }
    TRACKER_KEY_PATTERN = r"^[A-Z][A-Z0-9]*-\d+$"
    TRACKER_KEYS = frozenset(("type", "site", "statuses", "startFrom", "assign", "comments", "baseFromParent"))
    TRACKER_TYPES = frozenset(("jira",))
    FLOW_KEYS = frozenset(("max", "jql", "pickRules", "pollSeconds", "staleHours", "lostWatchMinutes", "remoteControl"))
    FLOW_DEFAULTS: ClassVar[dict[str, Any]] = {
        "max": 1,
        "jql": "parent = {parent} ORDER BY Rank ASC",
        "pickRules": [],
        "pollSeconds": 60,
        "staleHours": 3,
        "lostWatchMinutes": 10,
        "remoteControl": False,
    }
    MODE_NAMES = ("heavy", "medium", "light")
    MODE_DEFAULTS: ClassVar[dict[str, dict[str, str]]] = {
        "heavy": {"session": "opus"},
        "medium": {"session": "sonnet"},
        "light": {"session": "sonnet"},
    }
    DEFAULT_STATUSES: ClassVar[dict[str, str]] = {"inProgress": "In Progress", "inReview": "In Review", "done": "Done"}
    _SITE = re.compile(r"[A-Za-z0-9.-]+")
    _FRONT_MATTER = re.compile(r"---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

    def __init__(self, root: str | Path, run: ProcessRunner.Run = ProcessRunner.default_run) -> None:
        self.root = root
        self.run = run
        self.config_notice: str | None = None

    @staticmethod
    def find_root(*, cwd: str, run: ProcessRunner.Run = ProcessRunner.default_run) -> str:
        """The main checkout of the repository `cwd` belongs to."""
        out = run("git", ["-C", cwd, "worktree", "list", "--porcelain"]).stdout
        first = re.search(r"^worktree (.+)$", str(out), re.MULTILINE)
        if not first:
            raise LaunchError(f"{cwd} is not inside a git repository")
        return first[1]

    @staticmethod
    def _outside_repo(path: str) -> bool:
        return os.path.isabs(path) or ".." in re.split(r"[\\/]", path)

    CONFIG_PATH = ".claude/gated-dev-simple-workflow/config.json"
    LEGACY_CONFIG_PATH = ".claude/gated-dev.json"

    def config_file(self) -> tuple[Path, str | None]:
        """The config file of `root` (the main checkout) and a notice about the legacy location, if any.

        `CONFIG_PATH` wins; without it a `LEGACY_CONFIG_PATH` file is still read, with a notice to move it.
        """
        current = Path(self.root) / TicketProject.CONFIG_PATH
        legacy = Path(self.root) / TicketProject.LEGACY_CONFIG_PATH
        if current.exists():
            if legacy.exists():
                return current, f"{legacy} is ignored: {current} takes precedence; delete the old file"
            return current, None
        if legacy.exists():
            return legacy, f"{legacy} is deprecated: it is moved to {current} (git-ignored) on the next run that is not a dry run"
        return current, None

    CONFIG_EXCLUDE = "/.claude/gated-dev-simple-workflow/"
    CONFIG_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "config.json"

    def ensure_config(self) -> str | None:
        """Makes sure `CONFIG_PATH` exists in `root` and is git-ignored; returns a notice when it changed something.

        No file: a `LEGACY_CONFIG_PATH` file is moved there, otherwise `CONFIG_TEMPLATE` (every key with its default) is copied. Either way
        `CONFIG_EXCLUDE` goes into the repository's `info/exclude` (shared by all worktrees, never committed).
        """
        current = Path(self.root) / TicketProject.CONFIG_PATH
        if current.exists():
            return None
        legacy = Path(self.root) / TicketProject.LEGACY_CONFIG_PATH
        self._exclude(TicketProject.CONFIG_EXCLUDE)
        current.parent.mkdir(parents=True, exist_ok=True)
        if legacy.exists():
            legacy.replace(current)
            return f"moved {legacy} to {current} (git-ignored); commit the deletion of {TicketProject.LEGACY_CONFIG_PATH}"
        current.write_text(TicketProject.CONFIG_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        return f"created {current} from the template (git-ignored, every key at its default)"

    def _exclude(self, line: str) -> None:
        """Appends `line` to the repository's `info/exclude` unless it is there."""
        common = str(self.run("git", ["-C", str(self.root), "rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout).strip()
        exclude = Path(common) / "info" / "exclude"
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if line in text.splitlines():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(f"{text}{'' if not text or text.endswith(chr(10)) else chr(10)}{line}\n", encoding="utf-8")

    def read_config(self) -> dict:
        """The config file of `root` (`config_file`), with defaults. Raises on invalid content.

        A notice about the legacy location is kept in `config_notice`.
        """
        file, self.config_notice = self.config_file()
        raw: dict = {}
        if file.exists():
            try:
                parsed = json.loads(file.read_text(encoding="utf-8"))
            except ValueError as error:
                raise LaunchError(f"{file}: invalid JSON ({error})") from error
            if not isinstance(parsed, dict):
                raise LaunchError(f"{file}: must contain a JSON object")
            for k in parsed:
                if k not in TicketProject.CONFIG_KEYS:
                    raise LaunchError(f'{file}: unknown key "{k}"')
            raw = parsed
        tickets_dir = LaunchNaming._first(raw.get("ticketsDir"), "docs/tickets")
        if not isinstance(tickets_dir, str) or not tickets_dir or TicketProject._outside_repo(tickets_dir):
            raise LaunchError(f"{file}: ticketsDir must be a relative path inside the repo")
        tracker = TicketProject._read_tracker(raw.get("tracker"), file)
        flow = TicketProject._read_flow(raw.get("flow"), file, tracker)
        default_pattern = TicketProject.TRACKER_KEY_PATTERN if tracker else TicketProject.DEFAULT_KEY_PATTERN
        try:
            key_pattern = re.compile(LaunchNaming._first(raw.get("keyPattern"), default_pattern))
        except (re.error, TypeError) as error:
            raise LaunchError(f"{file}: invalid keyPattern ({error})") from error
        base_branch = raw.get("baseBranch")
        if base_branch is not None and not LaunchNaming._SAFE_REF.fullmatch(LaunchNaming._js_string(base_branch)):
            raise LaunchError(f"{file}: invalid baseBranch")
        rules = raw.get("orchestratorRules")
        if rules is not None and (not isinstance(rules, str) or not rules or TicketProject._outside_repo(rules)):
            raise LaunchError(f"{file}: orchestratorRules must be a relative path inside the repo")
        dry_run_gate = raw.get("dryRunGate", False)
        if not isinstance(dry_run_gate, bool):
            raise LaunchError(f"{file}: dryRunGate must be true or false")
        mode = raw.get("mode", "auto")
        if mode not in ("auto", *TicketProject.MODE_NAMES):
            raise LaunchError(f"{file}: mode must be one of auto, heavy, medium, light")
        modes = TicketProject._read_modes(raw.get("modes"), file)
        mode_rules = raw.get("modeRules", [])
        if not (isinstance(mode_rules, list) and all(isinstance(x, str) and x for x in mode_rules)):
            raise LaunchError(f"{file}: modeRules must be a list of non-empty strings")
        dev_tier = raw.get("devTier", "auto")
        if dev_tier not in TicketProject.DEV_TIERS:
            raise LaunchError(f"{file}: devTier must be one of {', '.join(sorted(TicketProject.DEV_TIERS))}")
        models = TicketProject._read_models(raw.get("models"), file)
        return {
            "ticketsDir": tickets_dir,
            "keyPattern": key_pattern,
            "baseBranch": base_branch,
            "orchestratorRules": rules,
            "tracker": tracker,
            "flow": flow,
            "dryRunGate": dry_run_gate,
            "mode": mode,
            "modes": modes,
            "modeRules": mode_rules,
            "devTier": dev_tier,
            "models": models,
        }

    @staticmethod
    def _read_modes(raw: Any, file: Path) -> dict[str, dict[str, str]]:
        """The checked `modes` config merged over the defaults."""
        modes = {name: dict(value) for name, value in TicketProject.MODE_DEFAULTS.items()}
        if raw is None:
            return modes
        if not isinstance(raw, dict):
            raise LaunchError(f"{file}: modes must be an object")
        for name, value in raw.items():
            if name not in modes:
                raise LaunchError(f'{file}: modes has unknown mode "{name}" (use heavy, medium or light)')
            if not isinstance(value, dict):
                raise LaunchError(f"{file}: modes.{name} must be an object")
            for k in value:
                if k != "session":
                    raise LaunchError(f'{file}: modes.{name} has unknown key "{k}"')
            if "session" in value:
                if not LaunchNaming.is_model(value["session"]):
                    raise LaunchError(f"{file}: modes.{name}.session must be opus, sonnet or a claude-... model id")
                if "haiku" in value["session"].lower():
                    raise LaunchError(
                        f"{file}: modes.{name}.session must not be haiku: the session is the orchestrator and needs sonnet or opus"
                    )
                modes[name]["session"] = value["session"]
        return modes

    @staticmethod
    def _read_models(raw: Any, file: Path) -> dict[str, str]:
        """The checked `models` with the defaults filled in."""
        if raw is None:
            return dict(TicketProject.MODEL_DEFAULTS)
        if not isinstance(raw, dict):
            raise LaunchError(f"{file}: models must be an object")
        if "dev" in raw:
            raise LaunchError(f"{file}: models.dev was replaced by models.devWorkhorse (and devGodzilla, devPawn)")
        for k, v in raw.items():
            if k not in TicketProject.MODEL_DEFAULTS:
                raise LaunchError(f'{file}: models has unknown key "{k}"')
            if not LaunchNaming.is_model(v):
                raise LaunchError(f"{file}: models.{k} must be opus, sonnet, haiku or a claude-... model id")
        return {**TicketProject.MODEL_DEFAULTS, **raw}

    @staticmethod
    def _read_tracker(raw: Any, file: Path) -> dict | None:
        """The checked, normalised `tracker` config, or None when it is not set."""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise LaunchError(f"{file}: tracker must be an object")
        for k in raw:
            if k not in TicketProject.TRACKER_KEYS:
                raise LaunchError(f'{file}: tracker has unknown key "{k}"')
        if "type" not in raw:
            raise LaunchError(f"{file}: tracker.type is required")
        if raw["type"] not in TicketProject.TRACKER_TYPES:
            raise LaunchError(f"{file}: tracker.type {LaunchNaming._json(raw['type'])} is not supported (only jira)")
        site = raw.get("site")
        if site is not None and not (isinstance(site, str) and TicketProject._SITE.fullmatch(site)):
            raise LaunchError(f"{file}: tracker.site must be a host name without scheme or path")
        given = raw.get("statuses", {})
        if not isinstance(given, dict):
            raise LaunchError(f"{file}: tracker.statuses must be an object")
        for k in given:
            if k not in TicketProject.DEFAULT_STATUSES:
                raise LaunchError(f'{file}: tracker.statuses has unknown key "{k}"')
        statuses = {**TicketProject.DEFAULT_STATUSES, **given}
        for k, v in statuses.items():
            if not (v is None and k == "inReview") and not (isinstance(v, str) and v):
                raise LaunchError(f"{file}: tracker.statuses.{k} must be a non-empty string")
        start_from = raw.get("startFrom")
        if start_from is not None and not (isinstance(start_from, list) and all(isinstance(x, str) and x for x in start_from)):
            raise LaunchError(f"{file}: tracker.startFrom must be a list of non-empty strings")
        for flag in ("assign", "comments"):
            if not isinstance(raw.get(flag, True), bool):
                raise LaunchError(f"{file}: tracker.{flag} must be true or false")
        base = raw.get("baseFromParent")
        if base is not None and not (
            isinstance(base, str) and "{parent}" in base and LaunchNaming._SAFE_REF.fullmatch(base.replace("{parent}", "PROJ-1").replace("*", "x"))
        ):
            raise LaunchError(f"{file}: tracker.baseFromParent must contain {{parent}} and only safe branch characters and *")
        return {
            "type": raw["type"],
            "site": site,
            "statuses": statuses,
            "startFrom": start_from,
            "assign": raw.get("assign", True),
            "comments": raw.get("comments", True),
            "baseFromParent": base,
        }

    @staticmethod
    def _read_flow(raw: Any, file: Path, tracker: dict | None) -> dict | None:
        """The checked `flow` config with defaults filled in, or None when it is not set."""
        if raw is None:
            return None
        if tracker is None:
            raise LaunchError(f"{file}: flow requires tracker (flow searches the tracker for a parent's subtasks)")
        if not isinstance(raw, dict):
            raise LaunchError(f"{file}: flow must be an object")
        for k in raw:
            if k not in TicketProject.FLOW_KEYS:
                raise LaunchError(f'{file}: flow has unknown key "{k}"')
        flow = {**TicketProject.FLOW_DEFAULTS, "pickRules": [], **raw}
        if not isinstance(flow["max"], int) or isinstance(flow["max"], bool) or not 1 <= flow["max"] <= 10:
            raise LaunchError(f"{file}: flow.max must be an integer from 1 to 10")
        jql = flow["jql"]
        if not (isinstance(jql, str) and "{parent}" in jql):
            raise LaunchError(f"{file}: flow.jql must be a string containing {{parent}}")
        rules = flow["pickRules"]
        if not (isinstance(rules, list) and all(isinstance(x, str) for x in rules)):
            raise LaunchError(f"{file}: flow.pickRules must be a list of strings")
        poll = flow["pollSeconds"]
        if not isinstance(poll, int) or isinstance(poll, bool) or not 15 <= poll <= 3600:
            raise LaunchError(f"{file}: flow.pollSeconds must be an integer from 15 to 3600")
        for hours in ("staleHours", "lostWatchMinutes"):
            if not TicketProject._is_number(flow[hours]) or flow[hours] <= 0:
                raise LaunchError(f"{file}: flow.{hours} must be a positive number")
        if not isinstance(flow["remoteControl"], bool):
            raise LaunchError(f"{file}: flow.remoteControl must be true or false")
        return flow

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def check_key(key: Any, config: Mapping) -> None:
        """Checks a key against the safe set and the configured pattern."""
        if not LaunchNaming.is_safe_key(key) or not config["keyPattern"].search(key):
            raise LaunchError(f"{LaunchNaming._json(key)} is not a valid ticket key (keyPattern {config['keyPattern'].pattern})")


    def read_ticket(self, *, key: str, config: Mapping) -> dict:
        """`{file, title, base}` of `<root>/<ticketsDir>/<KEY>/ticket.md`.

        `base` is the front matter `base:` or None. Raises when the file is missing or has no title.
        """
        file = Path(self.root) / config["ticketsDir"] / key / "ticket.md"
        if not file.exists():
            raise LaunchError(f"{key}: no ticket file at {os.path.relpath(file, self.root)}")
        text = file.read_text(encoding="utf-8")
        body = text
        base = None
        front = TicketProject._FRONT_MATTER.match(text)
        if front:
            body = text[front.end() :]
            found = re.search(r"^base:(.*)$", front[1], re.MULTILINE)
            if found:
                value = re.sub(r"""^(["'])(.*)\1$""", r"\2", found[1].strip())
                if not LaunchNaming._SAFE_REF.fullmatch(value):
                    raise LaunchError(f'{key}: invalid base "{value}"')
                base = value
        title = re.search(r"^# +(.+?)\s*$", body, re.MULTILINE)
        if not title:
            raise LaunchError(f'{key}: ticket.md has no "# " title')
        return {"file": str(file), "title": title[1], "base": base}


    def list_ticket_keys(self, *, config: Mapping) -> list[str]:
        """Keys of every `<ticketsDir>/<KEY>/ticket.md` in the main checkout."""
        directory = Path(self.root) / config["ticketsDir"]
        if not directory.exists():
            return []
        return sorted(
            d.name
            for d in directory.iterdir()
            if d.is_dir() and LaunchNaming.is_safe_key(d.name) and config["keyPattern"].search(d.name) and (d / "ticket.md").exists()
        )


    def find_run(self, *, key: str, config: Mapping) -> dict | None:
        """The ticket's run: the worktree on a `<KEY>/*` branch, with the `stage` and `mode` of its `state.json`.

        The stage is None without one; the mode is None unless it is heavy, medium or light (a run
        from before modes has none). Aborted runs are ignored. Returns None when there is no run;
        raises when several are left.
        """
        out = self.run("git", ["-C", str(self.root), "worktree", "list", "--porcelain"]).stdout
        runs = []
        for block in re.split(r"\n\n+", str(out)):
            wt = re.search(r"^worktree (.+)$", block, re.MULTILINE)
            branch = re.search(r"^branch refs/heads/(.+)$", block, re.MULTILINE)
            if not wt or not branch or not branch[1].startswith(f"{key}/"):
                continue
            stage = mode = None
            try:
                state = json.loads((Path(wt[1]) / config["ticketsDir"] / key / "state.json").read_text(encoding="utf-8"))
                stage = LaunchNaming._get(state, "stage")
                mode = LaunchNaming._get(state, "mode")
            except (OSError, ValueError):
                pass
            if mode not in TicketProject.MODE_NAMES:
                mode = None
            if stage != "aborted":
                runs.append({"path": wt[1], "branch": branch[1], "stage": stage, "mode": mode})
        if len(runs) > 1:
            raise LaunchError(f"{key}: several runs ({', '.join(r['branch'] for r in runs)}); open the one you want with /ticket {key} --resume")
        return runs[0] if runs else None


    def base_from_parent(self, *, parent: str | None, config: Mapping) -> str | None | bool:
        """The base `tracker.baseFromParent` gives a tracker ticket, as the session's intake finds it.

        None when the setting is unset or no branch matches (the normal base rules apply); False
        when it cannot be decided here (no parent known, several matches: question H3, or the
        lookup failed), so the session has to decide.
        """
        pattern = (config.get("tracker") or {}).get("baseFromParent")
        if not pattern:
            return None
        if not parent or not LaunchNaming.is_safe_key(parent):
            return False
        try:
            out = self.run("git", ["-C", str(self.root), "ls-remote", "--heads", "origin", pattern.replace("{parent}", parent)]).stdout
        except LaunchError:
            return False
        heads = re.findall(r"\srefs/heads/(\S+)$", str(out), re.MULTILINE)
        if not heads:
            return None
        if len(heads) > 1 or not LaunchNaming._SAFE_REF.fullmatch(heads[0]):
            return False
        return heads[0]

    def resolve_base(self, *, ticket: Mapping, config: Mapping) -> str:
        """The branch a new ticket starts from.

        Front matter `base:`, config `baseBranch`, the remote default branch, `main` (section 3
        step 2 of the skill).
        """
        if ticket.get("base"):
            return ticket["base"]
        if config.get("baseBranch"):
            return config["baseBranch"]
        try:
            out = self.run("git", ["-C", str(self.root), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]).stdout
            ref = re.sub(r"^origin/", "", str(out).strip())
            if LaunchNaming._SAFE_REF.fullmatch(ref):
                return ref
        except LaunchError:
            pass
        return "main"
