---
name: ticket-flow
description: Run the subtasks of one Jira parent (an epic or a story) through /ticket without choosing the next ticket yourself. Use when the user invokes /ticket-flow <PARENT> (optionally with --max N, --once, --dry-run, --status, --skip KEY [reason] or --retry KEY), or says "work through the subtasks of <PARENT>", "run the epic <PARENT>" or "pick the next ticket of <PARENT>". Needs a Jira tracker in .claude/gated-dev-simple-workflow/config.json.
---

# /ticket-flow: driver for the subtasks of one parent

You are the driver. You run in the main session, never write product code, and never run the pipeline yourself: you choose the next ready subtask, open its own `/ticket` session through the launcher, wait, and repeat. Each launched session does its own Jira assignment, transitions and comments (`references/jira.md`).

Usage: `/ticket-flow PARENT [--max N] [--once] [--dry-run] [--status] [--skip KEY [reason]] [--retry KEY]`. `--max` and `--once` override the config for this run; `--max` is stored in the flow state, so a continued flow keeps it.

The pick rules and the flow state are in `references/flow.md` (next to the `ticket` skill, `${CLAUDE_PLUGIN_ROOT}/skills/ticket/references/flow.md`); the settings (`flow` key) are in `references/config.md`. Read the section you are acting on instead of working from memory.

Rules that hold everywhere:

- **Authorisation.** `/ticket-flow` authorises Jira reads (read issue, search by JQL) and launching sessions, nothing else. Never create, edit, assign, comment on or transition an issue; the launched `/ticket` session does that. Never merge a PR or request a review.
- **Gates are checkpoints.** A permission prompt or a gate denial on one of your calls is a stop for the developer, never something to work around.
- **One driver per parent.** Tell the developer to run one driver session per parent. Each write to the flow state is atomic and holds a lock, but nothing stops a second session from picking too.
- Every stop for the developer goes through `AskUserQuestion`, recommended option first.
- The helper is `${CLAUDE_PLUGIN_ROOT}/scripts/flow.py`. It needs Python >= 3.14: start it with `python3 -I`, or with `"$GATE_INFRA_PYTHON" -I` when `python3` is older and that variable is set. Always pass `--parent PARENT`. Every command prints JSON lines on stdout and diagnostics on stderr; a usage error exits 2, a failure exits 1 with `{"outcome": "error", "message": ...}`.
- Only the commands `status`, `launch`, `skip`, `retry` and `wait` exist. The state is written only by them.

## 1. Start

1. Read the config of the main checkout (the first `worktree` entry of `git worktree list --porcelain`): `.claude/gated-dev-simple-workflow/config.json`, (the flow CLI creates it on its first run that is not a dry run, moving an old `.claude/gated-dev.json` there or copying the plugin's `templates/config.json`; with `--dry-run` only the old file present, read that one). Without a `tracker`, stop: "`/ticket-flow` needs a Jira tracker in `.claude/gated-dev-simple-workflow/config.json`." Settings are in `flow` (`references/config.md`).
2. Check the session's Jira tools: read issue and search by JQL are both required. If either is missing, stop with: `tracker "jira" is configured, but this session has no Jira tools (read issue, search by JQL). Connect a Jira server or remove "tracker" from .claude/gated-dev-simple-workflow/config.json.`
3. Read the parent issue; stop when it cannot be read. Print its key and summary.
4. Mode flags:
   - `--status`: section 5, then end.
   - `--skip KEY [reason]`: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/flow.py" skip --parent PARENT KEY --reason "<reason>"`, print the result, end. A running ticket is only marked; its session is not touched.
   - `--retry KEY`: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/flow.py" retry --parent PARENT KEY`, print the result, end. It frees a skipped or aborted ticket for the pick.
5. Observe: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/flow.py" status --parent PARENT [--max N]`. It observes the launched runs, saves the state and prints one JSON line (`tickets`, `slotsUsed`, `freeSlots`, `foreign`, `lastPick`, `errors`). Report what a continued flow already has. A non-empty `errors` is shown, not fatal.
6. Fill the free slots (section 2) until full or nothing is ready.
7. End condition: nothing launched, nothing holds a slot and nothing waits for review: print the final report and end. With `--once`, print the report and end. Otherwise start the wait (section 3) and end the turn.

`--dry-run`: steps 1 to 5 (the status call refreshes an existing state and creates none), then run the pick of section 2 and show the choice, the mode and the launch plan (`flow.py launch ... --mode <mode> --dry-run`) without launching. Nothing else is saved.

## 2. Pick

Follow `references/flow.md`, "Pick". In short:

1. Run the candidate query with `{parent}` replaced (all pages). Pass the keys of every candidate to `status` once as `--candidate KEY` (repeat the option); keys with a run this flow did not launch come back as `foreign` and are listed, not picked.
2. Apply the hard filter and the judgement step, in query order. Take the first ticket left.
3. Nothing left: say why (the most useful blocker first) and stop picking.
4. Otherwise decide the subtask's mode from the issue you just read, with the rules in [modes.md section 4](../ticket/references/modes.md#4-decision-rules-d2): rule 2 (config `mode` other than `auto`, used as is) and rules 3 to 6 with the config `riskPaths`, `escalationExtra` and `modeRules`. Take the heavier mode when unsure. Then launch: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/flow.py" launch --parent PARENT KEY --summary "<summary>" --reason "<pick reason>; mode <mode>: <reason>" --mode <mode> [--remote-control]`. Add `--remote-control` when `flow.remoteControl` is true. The driver never launches without `--mode`. It refuses a key that is already in the state or has a run, and records the ticket as `launched` only after the launcher succeeded. Print the key, the mode and its reason, and the tab it opened.
5. A failed launch is reported and not retried in this pass, and the developer is notified (section 6). Go on while slots are free.

## 3. Wait

Start the wait with the Bash tool and `run_in_background: true`: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/flow.py" wait --parent PARENT` (absolute path). It polls with `gh` and git every `flow.pollSeconds`, and ends with one JSON line on stdout. Print one line ("Waiting for <keys>; this session wakes when a slot frees or a run needs attention. Closing it stops the flow; `/ticket-flow PARENT` continues it.") and end the turn. Do nothing else while it runs.

On wake-up, read the last stdout line of the background command as JSON (`outcome`, `tickets`):

- `slot-free`: the listed runs reached `done`, `merged` or `aborted`. Observe with `status` (for an `aborted` ticket say that `--retry KEY` makes it pickable again), fill the free slots (section 2), then end as in section 1 step 7.
- `lost-watch`: a run with an open PR lost its PR watch (its session was closed). For each entry whose `capped` is false, reopen the run: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/launch.py" KEY` (a key with a run is resumed with `/ticket KEY --resume`, which restarts the watch). For a capped entry (3 relaunches already), notify (section 6), say that the ticket is left to the developer and do not relaunch. Then wait again.
- `merged-orphan`: the PR is merged but its session is gone. Same as `lost-watch`: the resume runs the merge cleanup.
- `stale`: a run holding a slot has not changed for `flow.staleHours`. Notify once (section 6; the waiter does not report it again) and wait again. A run waiting for plan approval is never stale: it waits for a person on purpose.
- `error`, a last line that is not JSON, or a killed process: report the message and say that `/ticket-flow PARENT` continues.

## 4. Stop points

The developer decides at these points; the driver never decides for them:

- no `tracker`, missing Jira tools or an unreadable parent (section 1);
- a failed launch, a failed relaunch, a capped ticket (sections 3 and 6);
- a candidate the judgement step cannot call (for example a dependency that may or may not be met): ask **Pick it** · **Skip it (recommended)** · **Stop**;
- every plan approval, which happens in the launched session, not here.

## 5. Status

`--status` makes no Jira call that writes: one `flow.py status --parent PARENT` call (it saves its observation unless another process holds the flow lock, and creates no state), then a table: key, status, mode (`-` when unknown), stage, PR, reason; then `freeSlots` and the reason of `lastPick`. A flow that was never started prints that and nothing else.

## 6. Notifications

Notify with `bash "${CLAUDE_PLUGIN_ROOT}/hooks/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/notify.py" "<PARENT> <event>" "<one line>"`, on: nothing ready while nothing runs (the flow is finished or blocked), `stale`, a launch failure and the third failed relaunch (a capped ticket). A failure is ignored; the report is already printed. `GATED_DEV_NOTIFY=0` disables it.
