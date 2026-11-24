"""Units of the flow waiter and CLI (gated_dev.flow.waiter, gated_dev.flow.cli): fake git, gh, launcher and clock."""

import json
import os
import re
from datetime import UTC, datetime, timedelta

import pytest

from gated_dev.completed_run import CompletedRun
from gated_dev.errors import LaunchError
from gated_dev.flow.cli import FlowCli
from gated_dev.flow.observer import FlowObserver
from gated_dev.flow.state import FlowState
from gated_dev.flow.waiter import FlowWaiter
from gated_dev.launch.launcher import TicketLauncher
from gated_dev.launch.ticket_project import TicketProject

T0 = datetime(2026, 11, 22, 9, 0, 0, tzinfo=UTC)
PR = "https://github.com/o/r/pull/7"
CONFIG = {"ticketsDir": "docs/tickets"}


class Stop(Exception):
    """Raised by the fake sleep when the scripted polls are used up: the waiter found nothing."""


class Clock:
    def __init__(self):
        self.t = T0
        self.sleeps: list[float] = []
        self.events: list = []  # one callable per sleep, run after the clock moved

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)
        if not self.events:
            raise Stop
        self.events.pop(0)()


class Repo:
    """Fake git worktrees with `state.json` files, and a fake `gh`."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.clock = Clock()
        self.branches: dict[str, str] = {}
        self.prs: dict[str, str] = {}
        self.gh_fail = False

    def set_run(self, key, stage="dev", *, pr=None, heartbeat=None, age_hours=0.0):
        wt = self.tmp / f"wt-{key}"
        (wt / "docs" / "tickets" / key).mkdir(parents=True, exist_ok=True)
        path = wt / "docs" / "tickets" / key / "state.json"
        body = {"stage": stage, **({"prUrl": pr} if pr else {}), **({"watch": {"heartbeatAt": heartbeat}} if heartbeat else {})}
        self.branches[key] = f"{key}/work"
        if stage is None:
            return wt  # just launched: no state.json yet
        path.write_text(json.dumps(body))
        mtime = (self.clock.t - timedelta(hours=age_hours)).timestamp()
        os.utime(path, (mtime, mtime))
        self.branches[key] = f"{key}/work"
        return wt

    def remove_run(self, key):
        del self.branches[key]

    def git(self, file, argv):
        if argv[-1] == "--git-common-dir":
            return CompletedRun(".git\n", "")
        blocks = [f"worktree {self.tmp}\nbranch refs/heads/main"]
        blocks += [f"worktree {self.tmp / f'wt-{k}'}\nbranch refs/heads/{b}" for k, b in self.branches.items()]
        return CompletedRun("\n\n".join(blocks) + "\n", "")

    def gh(self, cmd, args, *, cwd=None):
        if self.gh_fail:
            raise RuntimeError("gh failed")
        return json.dumps({"state": self.prs.get(args[2], "OPEN")})

    def heartbeat(self, minutes_ago):
        return FlowState.iso(self.clock.t - timedelta(minutes=minutes_ago))

    def state(self):
        return FlowState(directory=self.tmp / ".git" / "gated-dev" / "flow", parent="PROJ-100", now=self.clock.now, is_alive=lambda pid: False)

    def waiter(self):
        observer = FlowObserver(TicketProject(self.tmp, self.git), run=self.gh)
        return FlowWaiter(observer, sleep=self.clock.sleep, log=lambda line: None)

    def launched(self, state, key, **fields):
        ticket = state.add_launched(key, summary="s", reason="r", worktree=str(self.tmp / f"wt-{key}"))
        path = self.tmp / f"wt-{key}" / "docs" / "tickets" / key / "state.json"
        if path.exists():  # the run as an earlier observation saw it
            run = json.loads(path.read_text())
            ticket.update(stage=run["stage"], prUrl=run.get("prUrl"))
        ticket.update(fields)
        return ticket

    def wait(self, state, **kwargs):
        state.save()
        return self.waiter().wait(state=state, config=CONFIG, **kwargs)

    def saved(self):
        reloaded = self.state()
        reloaded.load()
        return reloaded


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def keys(result):
    return [t["key"] for t in result["tickets"]]


# --- outcomes -------------------------------------------------------------------


def test_constants():
    assert FlowWaiter.MAX_FAILURES == 5
    assert FlowWaiter.RELAUNCH_CAP == 3
    assert FlowCli.EXIT == {"slot-free": 0, "lost-watch": 0, "merged-orphan": 0, "stale": 0, "error": 1}


@pytest.mark.parametrize("change", ["done", "merged", "aborted", "gone"])
def test_slot_free_when_a_slot_holder_finishes(repo, change):
    state = repo.state()
    repo.set_run("PROJ-1", "dev")
    repo.launched(state, "PROJ-1")

    def finish():
        if change == "gone":
            repo.remove_run("PROJ-1")
        else:
            repo.set_run("PROJ-1", change, pr=PR if change == "done" else None)

    repo.clock.events = [finish]
    result = repo.wait(state, poll_seconds=30)
    assert result["outcome"] == "slot-free"
    assert keys(result) == ["PROJ-1"]
    assert repo.clock.sleeps == [30]
    expected = {"done": "launched", "merged": "merged", "aborted": "aborted", "gone": "aborted"}[change]
    assert repo.saved().tickets["PROJ-1"]["status"] == expected


def test_a_run_already_in_review_does_not_free_a_slot_again(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(1))
    repo.launched(state, "PROJ-1", stage="done", prUrl=PR)
    repo.set_run("PROJ-2", "dev")
    repo.launched(state, "PROJ-2")
    repo.clock.events = [lambda: repo.set_run("PROJ-2", "review"), lambda: repo.set_run("PROJ-2", "done", pr=PR + "8")]
    result = repo.wait(state)
    assert (result["outcome"], keys(result), len(repo.clock.sleeps)) == ("slot-free", ["PROJ-2"], 2)


def test_lost_watch_and_relaunch_count(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(11))
    repo.launched(state, "PROJ-1")
    result = repo.wait(state, lost_watch_minutes=10)
    assert result["outcome"] == "lost-watch"
    assert result["tickets"] == [{"key": "PROJ-1", "prUrl": PR, "stage": "done", "capped": False, "relaunches": 1}]
    assert repo.clock.sleeps == []
    saved = repo.saved().tickets["PROJ-1"]
    assert (saved["relaunches"], saved["lastRelaunchAt"]) == (1, FlowState.iso(repo.clock.t))
    # The relaunched session needs time to start its watch: no new report inside the window.
    with pytest.raises(Stop):
        repo.waiter().wait(state=repo.saved(), config=CONFIG, lost_watch_minutes=10, poll_seconds=60)
    assert repo.saved().tickets["PROJ-1"]["relaunches"] == 1


def test_fresh_heartbeat_is_not_lost(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(9))
    repo.launched(state, "PROJ-1")
    with pytest.raises(Stop):
        repo.wait(state, lost_watch_minutes=10)


def test_done_run_without_any_heartbeat_falls_back_to_the_state_file_age(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, age_hours=1)
    repo.launched(state, "PROJ-1")
    assert repo.wait(state)["outcome"] == "lost-watch"


def test_relaunch_cap_reports_once_and_leaves_the_ticket(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(60))
    repo.launched(state, "PROJ-1", relaunches=FlowWaiter.RELAUNCH_CAP)
    result = repo.wait(state)
    assert result["outcome"] == "lost-watch"
    assert result["tickets"][0]["capped"] is True
    assert repo.saved().tickets["PROJ-1"]["relaunches"] == FlowWaiter.RELAUNCH_CAP
    with pytest.raises(Stop):
        repo.waiter().wait(state=repo.saved(), config=CONFIG)


def test_merged_orphan_when_the_watch_is_gone(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(30))
    repo.prs[PR] = "MERGED"
    repo.launched(state, "PROJ-1")
    result = repo.wait(state)
    assert result["outcome"] == "merged-orphan"
    assert result["tickets"] == [{"key": "PROJ-1", "prUrl": PR, "stage": "done", "capped": False, "relaunches": 1}]
    saved = repo.saved().tickets["PROJ-1"]
    assert (saved["status"], "orphan" in saved) == ("merged", False)


def test_merged_with_a_live_watch_is_left_to_the_ticket_session(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(1))
    repo.prs[PR] = "MERGED"
    repo.launched(state, "PROJ-1")
    with pytest.raises(Stop):
        repo.wait(state)
    assert repo.saved().tickets["PROJ-1"]["status"] == "merged"


def test_orphan_found_together_with_slot_free_is_not_lost(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "addressing_review", pr=PR, heartbeat=repo.heartbeat(60))
    repo.prs[PR] = "MERGED"
    repo.launched(state, "PROJ-1")
    first = repo.wait(state)
    assert (first["outcome"], keys(first)) == ("slot-free", ["PROJ-1"])
    second = repo.waiter().wait(state=repo.saved(), config=CONFIG)
    assert (second["outcome"], keys(second)) == ("merged-orphan", ["PROJ-1"])


def test_stale_is_reported_once_per_run(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "dev", age_hours=4)
    repo.launched(state, "PROJ-1")
    result = repo.wait(state, stale_hours=3)
    assert result["outcome"] == "stale"
    assert result["tickets"] == [{"key": "PROJ-1", "stage": "dev", "idleHours": 4.0}]
    assert repo.saved().tickets["PROJ-1"]["staleWarnedAt"] == FlowState.iso(repo.clock.t)
    with pytest.raises(Stop):
        repo.waiter().wait(state=repo.saved(), config=CONFIG, stale_hours=3)


def test_run_without_state_json_is_stale_from_its_launch_time(repo):
    state = repo.state()
    repo.set_run("PROJ-1", None)
    repo.launched(state, "PROJ-1", launchedAt=FlowState.iso(T0 - timedelta(hours=5)))
    assert repo.wait(state, stale_hours=3)["outcome"] == "stale"


def test_fresh_run_is_not_stale_and_plan_approval_never_is(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "dev", age_hours=1)
    repo.launched(state, "PROJ-1")
    repo.set_run("PROJ-2", "awaiting_plan_approval", age_hours=50)
    repo.launched(state, "PROJ-2")
    with pytest.raises(Stop):
        repo.wait(state, stale_hours=3)
    assert repo.saved().tickets["PROJ-2"]["staleWarnedAt"] is None


def test_first_match_wins_slot_free_then_lost_watch_then_orphan_then_stale(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "dev")
    repo.launched(state, "PROJ-1")  # becomes done while we look: slot-free
    repo.set_run("PROJ-2", "done", pr=PR + "2", heartbeat=repo.heartbeat(60))
    repo.launched(state, "PROJ-2", stage="done", prUrl=PR + "2")  # lost watch
    repo.set_run("PROJ-3", "dev", age_hours=9)
    repo.launched(state, "PROJ-3")  # stale
    repo.set_run("PROJ-1", "done", pr=PR + "1")
    result = repo.wait(state, stale_hours=3)
    assert (result["outcome"], keys(result)) == ("slot-free", ["PROJ-1"])
    assert repo.saved().tickets["PROJ-3"]["staleWarnedAt"] is None  # not consumed by a lower match
    assert repo.saved().tickets["PROJ-2"]["relaunches"] == 0
    second = repo.waiter().wait(state=repo.saved(), config=CONFIG, stale_hours=3)
    assert (second["outcome"], keys(second)) == ("lost-watch", ["PROJ-2"])
    third = repo.waiter().wait(state=repo.saved(), config=CONFIG, stale_hours=3)
    assert (third["outcome"], keys(third)) == ("stale", ["PROJ-3"])


# --- errors ---------------------------------------------------------------------


def test_five_failed_polls_in_a_row_are_an_error(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(1))
    repo.launched(state, "PROJ-1")
    repo.gh_fail = True
    repo.clock.events = [lambda: None] * 10
    result = repo.wait(state, poll_seconds=15)
    assert result["outcome"] == "error"
    assert "gh failed" in result["message"]
    assert len(repo.clock.sleeps) == 4  # five polls, four sleeps between


def test_a_good_poll_resets_the_failure_count(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(1))
    repo.launched(state, "PROJ-1")
    repo.gh_fail = True

    def heal():
        repo.gh_fail = False

    def break_again():
        repo.gh_fail = True

    # 3 failures, 1 good, then failures again: never 5 in a row, so the script runs out instead.
    repo.clock.events = [lambda: None, lambda: None, heal, break_again, lambda: None, lambda: None, lambda: None]
    with pytest.raises(Stop):
        repo.wait(state)
    assert len(repo.clock.sleeps) == 8


def test_unreadable_flow_state_is_an_immediate_error(repo):
    state = repo.state()
    state.save()
    state.path.write_text("not json")
    result = repo.waiter().wait(state=state, config=CONFIG)
    assert result["outcome"] == "error"
    assert "cannot read" in result["message"]
    assert repo.clock.sleeps == []


def test_lock_held_by_a_live_process_counts_as_failed_polls(repo):
    state = repo.state()
    state.save()
    state.lock_path.write_text("999\n")
    state.is_alive = lambda pid: True
    repo.clock.events = [lambda: None] * 10
    result = repo.waiter().wait(state=state, config=CONFIG)
    assert result["outcome"] == "error"
    assert "another /ticket-flow driver" in result["message"]


def test_waiter_leaves_the_lock_released(repo):
    state = repo.state()
    repo.set_run("PROJ-1", "done", pr=PR, heartbeat=repo.heartbeat(60))
    repo.launched(state, "PROJ-1")
    repo.wait(state)
    assert not state.lock_path.exists()


# --- cli ------------------------------------------------------------------------


class Cli:
    def __init__(self, repo, *, launcher=None, monkeypatch=None):
        self.repo = repo
        self.out: list[str] = []
        self.err: list[str] = []
        self.launches: list[dict] = []
        if launcher is not False:
            monkeypatch.setattr(TicketLauncher, "launch_ticket", staticmethod(launcher or self.launch))

    def launch(self, *, root, key, config, remote_control=False, dry_run=False, summary=None, parent=None, ticket_mode=None, env=None, run=None, log=None):
        self.launches.append({"key": key, "dry_run": dry_run, "remote_control": remote_control, "ticket_mode": ticket_mode})
        worktree = None if dry_run else {"path": str(self.repo.tmp / f"wt-{key}")}
        return {"key": key, "mode": "orca-worktree", "cwd": "/x", "title": f"{key}: s", "launched": not dry_run, "worktree": worktree}

    def run(self, *argv):
        code = FlowCli.main(
            list(argv),
            env={},
            run=self.repo.git,
            gh_run=self.repo.gh,
            sleep=self.repo.clock.sleep,
            now=self.repo.clock.now,
            is_alive=lambda pid: False,
            out=self.out.append,
            err=self.err.append,
        )
        return code, [json.loads(line) for line in self.out if line.startswith("{")]


@pytest.fixture
def cli(repo, monkeypatch):
    return Cli(repo, monkeypatch=monkeypatch)


def flow_file(repo):
    return repo.tmp / ".git" / "gated-dev" / "flow" / "PROJ-100.json"


def test_launch_records_the_ticket_after_the_launcher_succeeded(repo, cli):
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--summary", "Add export", "--reason", "first ready", "--max", "2")
    assert code == 0
    assert lines[0]["launched"] is True
    saved = repo.saved()
    ticket = saved.tickets["PROJ-1"]
    assert (ticket["status"], ticket["summary"], ticket["reason"], ticket["worktree"]) == ("launched", "Add export", "first ready", str(repo.tmp / "wt-PROJ-1"))
    assert saved.max == 2
    assert saved.picks[-1]["key"] == "PROJ-1"
    assert not saved.lock_path.exists()


def test_failed_launch_saves_nothing(repo, monkeypatch):
    def broken(**kwargs):
        raise LaunchError("orca failed")

    cli = Cli(repo, launcher=broken, monkeypatch=monkeypatch)
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1")
    assert code == 1
    assert lines == [{"outcome": "error", "message": "orca failed"}]
    assert not flow_file(repo).exists()


def test_launch_refuses_a_key_already_in_the_state(repo, cli):
    state = repo.state()
    state.skip("PROJ-1", "later")
    state.save()
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1")
    assert code == 1
    assert "already in the flow" in lines[0]["message"]
    assert cli.launches == []


def test_launch_refuses_a_key_that_already_has_a_run(repo, cli):
    repo.set_run("PROJ-1", "dev")
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1")
    assert code == 1
    assert "already has a run" in lines[0]["message"]
    assert cli.launches == [] and not flow_file(repo).exists()


def test_launch_dry_run_writes_nothing(repo, cli):
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--dry-run", "--remote-control")
    assert code == 0
    assert (lines[0]["dryRun"], lines[0]["launched"]) == (True, False)
    assert cli.launches == [{"key": "PROJ-1", "dry_run": True, "remote_control": True, "ticket_mode": None}]
    assert not (repo.tmp / ".git" / "gated-dev").exists()


def test_launch_passes_the_summary_and_the_parent_to_the_launcher(repo, monkeypatch):
    seen = []

    def with_summary(*, root, key, config, summary=None, parent=None, ticket_mode=None, remote_control=False, dry_run=False, env=None, run=None, log=None):
        seen.append((summary, parent))
        return {"key": key, "mode": "main-checkout", "launched": True, "worktree": None}

    cli = Cli(repo, launcher=with_summary, monkeypatch=monkeypatch)
    assert cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--summary", "Add export")[0] == 0
    assert seen == [("Add export", "PROJ-100")]
    assert repo.saved().tickets["PROJ-1"]["worktree"] is None


def test_launch_mode_is_passed_to_the_launcher_and_stored(repo, cli):
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--mode", "light")
    assert code == 0
    assert cli.launches[0]["ticket_mode"] == "light"
    assert repo.saved().tickets["PROJ-1"]["mode"] == "light"
    assert lines[0]["mode"] == "orca-worktree"  # the launch path, not the pipeline mode


def test_launch_without_mode_stores_none(repo, cli):
    assert cli.run("launch", "--parent", "PROJ-100", "PROJ-1")[0] == 0
    assert cli.launches[0]["ticket_mode"] is None
    assert repo.saved().tickets["PROJ-1"]["mode"] is None


def test_launch_rejects_an_invalid_mode(repo, cli):
    code, lines = cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--mode", "huge")
    assert code == 2
    assert "mode must be one of" in lines[0]["message"]
    assert cli.launches == []


def test_mode_does_not_apply_to_other_commands(repo, cli):
    assert cli.run("status", "--parent", "PROJ-100", "--mode", "light")[0] == 2


def test_status_shows_the_mode_and_a_dash_for_unknown(repo, cli):
    assert cli.run("launch", "--parent", "PROJ-100", "PROJ-1", "--mode", "heavy")[0] == 0
    assert cli.run("launch", "--parent", "PROJ-100", "PROJ-2")[0] == 0
    state = repo.state()
    state.load()
    del state.tickets["PROJ-2"]["mode"]  # a state file written before modes existed
    state.save()
    cli.out.clear()
    code, lines = cli.run("status", "--parent", "PROJ-100")
    assert code == 0
    assert (lines[0]["tickets"]["PROJ-1"]["mode"], lines[0]["tickets"]["PROJ-2"]["mode"]) == ("heavy", "-")


def test_status_of_a_new_flow_writes_nothing(repo, cli):
    code, lines = cli.run("status", "--parent", "PROJ-100", "--candidate", "PROJ-9")
    assert code == 0
    assert (lines[0]["new"], lines[0]["saved"], lines[0]["freeSlots"], lines[0]["foreign"]) == (True, False, 1, [])
    assert not flow_file(repo).exists()


def test_status_observes_saves_and_reports_foreign_runs(repo, cli):
    state = repo.state()
    repo.set_run("PROJ-1", "review")
    repo.launched(state, "PROJ-1")
    state.save()
    repo.set_run("PROJ-9", "dev")  # started by hand
    code, lines = cli.run("status", "--parent", "PROJ-100", "--candidate", "PROJ-9", "--candidate", "PROJ-10")
    assert code == 0
    status = lines[0]
    assert (status["saved"], status["slotsUsed"], status["freeSlots"], status["foreign"]) == (True, 1, 0, ["PROJ-9"])
    assert status["tickets"]["PROJ-1"]["stage"] == "review"
    assert repo.saved().tickets["PROJ-1"]["stage"] == "review"


def test_status_with_max_creates_the_flow_and_a_held_lock_only_stops_saving(repo, cli):
    assert cli.run("status", "--parent", "PROJ-100", "--max", "3")[1][0]["max"] == 3
    assert repo.saved().max == 3
    lock = repo.saved().lock_path
    lock.write_text(f"{os.getpid()}\n")
    cli_alive = FlowCli.main(
        ["status", "--parent", "PROJ-100", "--max", "5"],
        env={}, run=repo.git, gh_run=repo.gh, is_alive=lambda pid: True, out=cli.out.append, err=cli.err.append, now=repo.clock.now,
    )
    assert cli_alive == 0
    assert json.loads(cli.out[-1])["saved"] is False
    assert repo.saved().max == 3
    assert lock.exists()


def test_skip_and_retry(repo, cli):
    assert cli.run("skip", "--parent", "PROJ-100", "PROJ-4", "--reason", "needs a vendor contract")[0] == 0
    assert repo.saved().tickets["PROJ-4"]["reason"] == "needs a vendor contract"
    assert cli.run("retry", "--parent", "PROJ-100", "PROJ-4")[0] == 0
    assert "PROJ-4" not in repo.saved().tickets
    code, lines = cli.run("retry", "--parent", "PROJ-100", "PROJ-4")
    assert code == 1
    assert "not skipped or aborted" in lines[-1]["message"]


def test_wait_through_the_cli_exits_by_outcome(repo, cli):
    state = repo.state()
    repo.set_run("PROJ-1", "dev")
    repo.launched(state, "PROJ-1")
    state.save()
    repo.clock.events = [lambda: repo.set_run("PROJ-1", "done", pr=PR)]
    code, lines = cli.run("wait", "--parent", "PROJ-100", "--poll-seconds", "20")
    assert (code, lines[0]["outcome"], repo.clock.sleeps) == (0, "slot-free", [20])


def test_wait_error_exits_1_and_needs_a_flow(repo, cli):
    code, lines = cli.run("wait", "--parent", "PROJ-100")
    assert code == 1
    assert "no flow state" in lines[0]["message"]
    state = repo.state()
    state.save()
    state.path.write_text("garbage")
    code, lines = cli.run("wait", "--parent", "PROJ-100")
    assert (code, lines[-1]["outcome"]) == (1, "error")


def test_cli_moves_a_legacy_config_file_and_reports_it(repo, cli):
    (repo.tmp / ".claude").mkdir()
    (repo.tmp / ".claude" / "gated-dev.json").write_text('{"ticketsDir": "t"}')
    cli.run("wait", "--parent", "PROJ-100")
    assert any(re.search(r"moved .*gated-dev\.json to .*config\.json", line) for line in cli.err)
    assert not (repo.tmp / ".claude" / "gated-dev.json").exists()
    assert (repo.tmp / ".claude" / "gated-dev-simple-workflow" / "config.json").read_text() == '{"ticketsDir": "t"}'


def test_flow_settings_come_from_the_config_and_flags_win(repo, cli):
    (repo.tmp / ".claude" / "gated-dev-simple-workflow").mkdir(parents=True)
    (repo.tmp / ".claude" / "gated-dev-simple-workflow" / "config.json").write_text("{}")
    settings = FlowCli(env={}, run=repo.git, gh_run=repo.gh, sleep=repo.clock.sleep, now=repo.clock.now, is_alive=bool, out=print, err=print)._settings
    assert settings({}, {}) == {"max": 1, "pollSeconds": 60, "staleHours": 3, "lostWatchMinutes": 10}
    assert settings({"flow": {"pollSeconds": 30, "max": 2}}, {"max": 4}) == {"max": 4, "pollSeconds": 30, "staleHours": 3, "lostWatchMinutes": 10}


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "give a command"),
        (["bogus", "--parent", "PROJ-100"], "unknown command"),
        (["status"], "--parent is required"),
        (["status", "--parent"], "--parent needs a value"),
        (["status", "--parent", "PROJ-100", "--nope"], "unknown option --nope"),
        (["launch", "--parent", "PROJ-100"], "exactly one ticket key"),
        (["launch", "--parent", "PROJ-100", "A-1", "B-2"], "exactly one ticket key"),
        (["status", "--parent", "PROJ-100", "PROJ-1"], "takes no ticket key"),
        (["wait", "--parent", "PROJ-100", "--summary", "x"], "does not apply"),
        (["skip", "--parent", "PROJ-100", "PROJ-1", "--dry-run"], "does not apply"),
        (["launch", "--parent", "PROJ-100", "PROJ-1", "--max", "0"], "max must be"),
        (["launch", "--parent", "PROJ-100", "PROJ-1", "--max", "1.5"], "max must be"),
        (["wait", "--parent", "PROJ-100", "--poll-seconds", "5"], "pollSeconds must be"),
        (["wait", "--parent", "PROJ-100", "--stale-hours", "abc"], "must be a number"),
    ],
)
def test_usage_errors_exit_2_with_a_clear_message(repo, cli, argv, message):
    code, lines = cli.run(*argv)
    assert code == 2
    assert lines[0]["outcome"] == "usage"
    assert message in lines[0]["message"]
    assert any("Usage: flow.py" in line for line in cli.err)
    assert not (repo.tmp / ".git" / "gated-dev").exists()


def test_invalid_keys_and_help(repo, cli):
    code, lines = cli.run("launch", "--parent", "PROJ-100", "bad key")
    assert (code, lines[0]["outcome"]) == (1, "error")
    assert "not a valid ticket key" in lines[0]["message"]
    code, lines = cli.run("status", "--parent", "../x")
    assert code == 1
    cli.out.clear()
    assert cli.run("--help")[0] == 0
    assert cli.out[0].startswith("Usage: flow.py ")


def test_transaction_holds_the_lock_while_loading_and_releases_it(repo):
    state = repo.state()
    state.add_launched("PROJ-1", summary="s", reason="r")
    state.save()
    fresh = repo.state()
    with fresh.transaction() as loaded:
        assert loaded is True
        assert fresh.lock_path.exists()
        assert "PROJ-1" in fresh.tickets
    assert not fresh.lock_path.exists()
    with pytest.raises(RuntimeError), fresh.transaction():
        raise RuntimeError
    assert not fresh.lock_path.exists()
