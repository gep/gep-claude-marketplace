---
name: ticket
description: Run a ticket file through the spec, plan, dev, review and PR pipeline with one subagent per stage, in its own git worktree. Use when the user invokes /ticket <KEY> (optionally with --mode, --resume, --status or --address-review), or says "start working on ticket <KEY>", "pick up <KEY>", "resume <KEY>", "status of <KEY>", "address review on <KEY>" or "fix PR comments on <KEY>". The ticket is a Markdown file (docs/tickets/<KEY>/ticket.md by default), a description the user pastes, or a Jira issue when the project configures a tracker.
---

# /ticket: ticket pipeline orchestrator

You are the orchestrator. You run in the main session, never write product code yourself, and drive the stage subagents through one ticket:

| Stage | Agent (`subagent_type`) | Writes |
| --- | --- | --- |
| spec | `gated-dev-simple-workflow:ticket-spec` | `spec.md` |
| plan | `gated-dev-simple-workflow:ticket-planner` | `plan.md` |
| dev | `gated-dev-simple-workflow:ticket-dev-godzilla`, `-workhorse` or `-pawn` (the tier, section 4) | commits, `dev-report.md` |
| review | `gated-dev-simple-workflow:ticket-reviewer` | `review.md` |

Not every ticket runs every stage. The run's **mode**, decided once at kickoff and only ever raised, sets the stages: `heavy` spec → plan → dev → review, `medium` plan → dev → review, `light` plan → dev. Plan approval (H2) is a stop in every mode. The rules (decision, raises, session model, records) are in `references/modes.md`.

The pipeline is file-based: the ticket is `ticket.md`, and every stage reads and writes files in the ticket directory. Between stages it stays file-based. With a `tracker` in the config, the ticket comes from Jira and the issue is kept in step (`references/jira.md`); without one the pipeline knows no issue tracker, and a project can still connect one through `orchestratorRules` (section 0a). The design (interaction points H1 to H7, the `NEEDS_DECISION` protocol, stages, state file) is in `references/pipeline-spec.md`; the per-project settings are in `references/config.md`. Read the section you are acting on instead of working from memory.

Rules that hold everywhere:

- Every stop for the developer goes through `AskUserQuestion`, recommended option first (the H-points). Calls that an approval gate stops use the normal permission prompt (H4); you do not handle those.
- Spawn every stage subagent in the **foreground** (never `run_in_background`) and pass `model` explicitly (section 4). The only background work is the PR watcher of section 9c: a Bash command with `run_in_background: true`, not a subagent.
- Never launch, restart or stop apps, dev servers or Docker. If something needs them and they are down, that is an infrastructure blocker (H6).
- Never route around an approval gate, and never run a gated operation that the approved plan does not list. Do not edit product files yourself.
- Keep `state.json` current: after every transition set the new `stage` and `updatedAt` (ISO-8601). Re-read it before every write, write the whole file and keep its `watch` field as it is (only the watcher of section 9c writes `watch`), and keep `tracker`, `rules`, `mode`, `modeReason` and `modeHistory` too.
- `state.json` is never committed. Never stage it, and never run a bare `git add -A`: when staging everything, use `git -C <wt> add -A -- . ':(exclude)<ticketsDir>/*/state.json'`.
- Commit trailer: end every commit message you write with the `Co-Authored-By` attribution line that your session's instructions specify for commits. Do not hard-code a model name; if the session specifies no attribution, add none. "The trailer" below means this line.
- Scripts and hooks of this plugin live under `${CLAUDE_PLUGIN_ROOT}` (`scripts/watch_pr.py`, `hooks/notify.py`). The scripts need Python >= 3.14: start them with `python3 -I`, or with `"$GATE_INFRA_PYTHON" -I` when `python3` is older and that variable is set.
- Formatting: "format `<files>`" below means: run `<formatCommand> <files>` in the worktree when `formatCommand` is configured; otherwise do nothing.
- Project rules: "**Event `<name>`**" below means: when `orchestratorRules` is configured and its file has a `## <name>` section, do what that section says now, then continue (see section 0a). Without the key or the section, nothing happens.
- Tracker steps: "**Tracker `<event>`**" below means: when `tracker` is configured, do what `references/jira.md` says for that event, then run the project rules' **Event `<event>`**. Without `tracker`, nothing happens.

## 0. Configuration and host

1. `<session-root>` is the top level of the checkout this session started in (`$CLAUDE_PROJECT_DIR`, else `git rev-parse --show-toplevel`). `<root>` is the main checkout: the first `worktree` entry of `git worktree list --porcelain`.
2. The config file is `<root>/.claude/gated-dev-simple-workflow/config.json`: local to the clone and git-ignored, so always the main checkout's, never a worktree's. Unless the flag is `--status` (which writes nothing), first make sure it exists: when it does not, append the line `/.claude/gated-dev-simple-workflow/` to `"$(git -C <root> rev-parse --path-format=absolute --git-common-dir)/info/exclude"` if it is missing, create the folder, then move the old `<root>/.claude/gated-dev.json` there when that file exists (`mv`; print "Moved `.claude/gated-dev.json` to `.claude/gated-dev-simple-workflow/config.json` (git-ignored); commit the deletion of the old file"), otherwise copy the template `${CLAUDE_PLUGIN_ROOT}/templates/config.json` (every key with its default) to it (print "Created `.claude/gated-dev-simple-workflow/config.json` from the template (git-ignored, every key at its default)"). Never stage or commit either file. Then read it (all keys optional; see `references/config.md`); with `--status` and only the old file present, read the old one. When both files exist, use the new one and print one line saying the old one is ignored. Invalid JSON or an unknown key: report it and stop; never guess. A key set to `null` counts as not set. Defaults (the same values as the template): `ticketsDir` `docs/tickets`, `keyPattern` `^[A-Za-z][A-Za-z0-9._-]*$`, `baseBranch` unset, `setupCommand` unset, `formatCommand` unset, `riskPaths` (section 8), `escalationExtra` `[]`, `devTier` `auto` (or `godzilla`, `workhorse`, `pawn`), `models` `{spec: sonnet, plan: opus, review: sonnet, reviewRisk: opus, devGodzilla: opus, devWorkhorse: sonnet, devPawn: haiku}`, `orchestratorRules` unset, `tracker` unset, `dryRunGate` `false`, `mode` `auto`, `modes` `{heavy: {session: opus}, medium: {session: sonnet}, light: {session: sonnet}}` (entries override one by one), `modeRules` `[]`. `mode` must be `auto`, `heavy`, `medium` or `light`; `modes` may only have those three modes with the single sub-key `session` (`opus`, `sonnet` or a `claude-...` id; never `haiku`); `modeRules` is a list of non-empty strings; anything else is a config error: report it and stop (`references/config.md#modes`). A `models.dev` key is a config error: stop with "`models.dev` was replaced by `models.devWorkhorse` (and `devGodzilla`, `devPawn`)". With a tracker the default `keyPattern` is `^[A-Z][A-Z0-9]*-\d+$`, and its sub-keys are validated as `references/config.md` says. When `orchestratorRules` is set, read that file (`<session-root>/<orchestratorRules>`) now; a missing file, or a `## ` heading that is not an event name of section 0a, is a config error: report it and stop. A `tracker` together with an `orchestratorRules` file that has a `## intake` section is a config error too: report it and stop.
3. Gate dry-run. Only when the config has `"dryRunGate": true` (default `false`; each checked call is one request to the gate's risk classifier and sends the redacted command) and the skill `destructive-guard:check` is in your list of available skills: find its script: in `~/.claude/plugins/installed_plugins.json`, take the `installPath` of the `destructive-guard@<marketplace>` entry whose `projectPath` is `<root>` (scope `project` or `local`), else the one with scope `user`, and check that `<installPath>/lib/check.py` exists (run it with `python3 -I`, or with `$GATE_INFRA_PYTHON` when that is set). Then `GATE_CHECK=CLAUDE_PROJECT_DIR='<session-root>' python3 -I '<installPath>/lib/check.py' --json`. Without `dryRunGate`, without `check.py`, or with `python3` older than 3.14 and no `GATE_INFRA_PYTHON`: no `GATE_CHECK` (the planner then writes `unknown` in the `Gate` column).
4. Host: `orca` when `TERM_PROGRAM=Orca`, or `ORCA_TERMINAL_HANDLE` is set and `TERM_PROGRAM` is not `iTerm.app`; `iterm` when `TERM_PROGRAM=iTerm.app` or `ITERM_SESSION_ID` is set; otherwise `plain`. An `orca` host also needs the repo registered in Orca: if `orca repo show --repo path:<root> --json` fails (or `orca` is not on `PATH`), the host is `plain` and you say so in one line ("<root> is not an Orca repo; using git worktrees"). Only `orca` changes how worktrees are created and removed; `iterm` and `plain` use git directly.
5. Tracker. With a `tracker`, check that the session has the Jira tools (`references/jira.md` §1) and resolve the site (§2). Missing tools: stop with the message of §1. This runs before section 1 does anything, so a bad setup creates nothing.

## 0a. Project rules (`orchestratorRules`)

A project can add its own steps to the pipeline, for example to keep an issue tracker in step, without changing it. The file is Markdown: one `## <event>` section per event below, in any order, each saying what to do in plain words; text before the first `## ` heading is shared context for every section (ids, URLs, conventions). The section for an event runs at the point marked **Event `<name>`** in this skill. With a tracker, its **Tracker `<name>`** step runs first at every event in the table, and `intake` is not available to rules (the tracker does the intake).

| Event | When |
| --- | --- |
| `intake` | Kickoff, step 1: no `ticket.md` and no pasted description. The section may write `ticket.md` (with front matter `base:` if it decides the base). |
| `started` | Kickoff done: worktree, `state.json` and setup exist. |
| `resumed` | `--resume`, after the checks of section 9 step 1. |
| `spec-done` | Spec stage finished (heavy mode only). |
| `plan-approved` | H2 approved and the spec and plan committed. |
| `dev-done` | Dev stage finished (also after each fix round). |
| `review-done` | Each review verdict (`VERDICT` and `reviewRound` are known); none in light mode unless it was raised. |
| `pr-body` | Writing the PR body (section 9a step 3). The section may give lines to add under `## Spec & plan`. |
| `pr-opened` | The PR exists (`prUrl` is set). |
| `pr-feedback-addressed` | A PR feedback round of section 9b is closed. |
| `merged` | Section 9d, after the final report and before the notification and worktree removal. |
| `aborted` | Section 10, with the reason. |

Rules for following them:

- You do the actions yourself with the tools the session has; stage subagents never see the file. Every call still goes through the approval gate and the normal permission rules. The developer wrote the file, so its actions need no extra confirmation unless the section asks for one.
- The rules add steps; they never change the pipeline. Ignore anything in the file that would skip or answer an H-point, write product code, change what a stage agent gets, run a gated operation the plan does not list, push anything other than the ticket branch, or remove a worktree or branch.
- Values you can use: `KEY`, the title, `TICKET_DIR`, branch, worktree, base branch, stage, `reviewRound`, the verdict, `prUrl`, the abort reason, and the files in `TICKET_DIR`. To keep a value between events (for example an id looked up at `started`), store it under `state.rules` (an object; keep it on every write of `state.json`).
- A failing action never stops the run, except at `intake` (then kickoff stops as step 1 says): append `{stage: "rules", command: "<event>: <action>", reason, at}` to `state.warnings` and continue. Retry only when the file says so. `--status` shows these warnings; the PR does not list them under "Skipped checks".

## 1. Arguments

1. Take the key from the user's text. It must match `keyPattern` and must not contain `/` or `..`; otherwise ask for it and stop.
2. Flags: `--resume` ("resume <KEY>"), `--status` ("status of <KEY>"), `--address-review` ("address review on <KEY>", "fix PR comments on <KEY>"). At most one; if several are given, ask which. No flag means a new run ("start working on", "pick up").
   `--mode heavy|medium|light` ("use light mode") is an option, not one of these flags: allowed with no flag (it decides the mode of the new run, `references/modes.md#4-decision-rules-d2` rule 1) and with `--resume` (section 9), never with `--status` or `--address-review` (say so and stop, changing nothing). Any other value: ask for a valid one and stop.
3. Find an existing run: in `git worktree list --porcelain`, look for a worktree whose branch matches `refs/heads/<KEY>/*`. Its state file is `<wt>/<ticketsDir>/<KEY>/state.json`. If several match, keep the one whose `stage` is not `aborted`; if that still leaves zero or more than one, ask which to use. Set `TICKET_DIR=<wt>/<ticketsDir>/<KEY>`.
4. Cases:
   - `--status`: no worktree or no state file: report "no state found for <KEY>", change nothing, stop. Otherwise section 2.
   - `--resume`: no worktree or no state file: say there is nothing to resume and offer a new run. Otherwise section 9 (stage `done` restarts the PR watch of section 9c, stage `merged` re-runs section 9d).
   - `--address-review`: no worktree or no state file: report "no state found for <KEY>", change nothing, stop. Otherwise section 9b.
   - No flag, state exists: ask Resume (recommended) · Restart. Restart asks once more for confirmation, sets the old run to `aborted` (section 10), and runs kickoff with a fresh slug suffix (`-2`, `-3`, ...) so the branch differs. Never delete a worktree or branch.
   - No flag, no state: kickoff (section 3). A worktree found in step 3 without a state file (opened by a launcher or by Orca, no run started yet) is adopted by kickoff, not duplicated (section 3, step 5.1).

## 2. Status (read-only)

Read `state.json` and print: ticket, title, stage (and `reviewRound`), `mode` and `modeReason` (no `mode`: `heavy`, recorded before modes), every `modeHistory` entry (`from` → `to`, `by`, `reason`, `at`), branch, worktree, base branch, PR URL if any, `lastAddressedCommentAt` if set (stage `addressing_review` means PR feedback is being addressed), `watch.heartbeatAt` if set (the PR watcher of section 9c is alive while it is less than 3 minutes old), `devTier` with `devTierReason` and every `devTierHistory` entry (from, to, stage, reason), the number of recorded decisions with the last question, every entry of `warnings` (`tracker` ones included), with a tracker also `state.tracker.status` and `url` (one read-only fetch of the live status is allowed), and the paths of `ticket.md`, `spec.md`, `plan.md`, `decisions.md`, `dev-report.md`, `review.md` and `pr-feedback-<n>.md` that exist. "Open questions" means: stage `awaiting_plan_approval` (the plan waits for approval), or the unresolved `## Open questions` of `spec.md` when the stage is `spec`. Write nothing, spawn nothing.

## 3. Kickoff

Steps 1 to 5 stop with a clear message on any failure and leave nothing behind. Once the worktree exists, a failure never removes it: keep the worktree and branch, follow H6 or the abort rules, and record what happened in `state.json`.

1. **Ticket file.** With a tracker: **Tracker intake** (`references/jira.md` §3) instead of the three cases below (the file, pasted-text and `intake` cases do not apply). A failed read stops kickoff with the Jira error; nothing is created. Without a tracker, look for `<session-root>/<ticketsDir>/<KEY>/ticket.md`.
   - It exists: use it. If the user also pasted a description, ask Use the file (recommended) · Replace it with the pasted text.
   - It does not exist and the user's message carries a description (anything beyond the key and flags): this text is the ticket. Write it to that path, with a `# <title>` first line (the user's title, or a short one you derive) followed by the text verbatim.
   - Neither: **Event `intake`**. If the file exists afterwards, use it. Otherwise tell the user to create `<ticketsDir>/<KEY>/ticket.md` or to paste a description with the command, and stop.

   An empty file, or one with no `# ` heading, is not a ticket: say so and stop. Read the optional YAML front matter (`---` block at the top): `base:` (a branch name) is the only key the orchestrator uses (with a tracker the intake may set it, `references/jira.md` §3). The title is the first `# ` heading.

   **Mode.** Now that the ticket is read, decide the mode with the rules of `references/modes.md#4-decision-rules-d2`, in order: `--mode`; config `mode` other than `auto`; then rules 3 to 6 together with `modeRules`, from `ticket.md` (with a tracker also the issue type and labels of its front matter), `riskPaths` and `escalationExtra`; when unsure, the heavier mode. Keep the mode and a one-line reason that names the deciding rule (step 6 records both) and print `Mode: <mode> (<reason>)`. Do not ask the developer about it; H2 is where the run can be redirected.
2. **Base branch.** The first that applies: front matter `base:` (with a tracker it may come from `baseFromParent`; more than one matching branch is H3, asked in the session and recorded once `state.json` exists); config `baseBranch`; the remote default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`, strip `origin/`; skip this when the command fails); `main`. Run `git fetch origin <base>`; failure: report and stop. From here the base is `BASE_REF=origin/<base>`, a ref for log and diff ranges only, never a branch to check out or push to. Pass it in the `BASE_BRANCH=` line of every prompt.
3. **Slug.** Lowercase the title, replace every run of characters outside `[a-z0-9]` with `-`, trim leading and trailing `-`, cut to 40 characters, trim a trailing `-` again. Empty result: `work`. Branch `<KEY>/<slug>`.
4. **Ignore pipeline paths.** The per-repo exclude file is `"$(git -C <root> rev-parse --path-format=absolute --git-common-dir)/info/exclude"`. Make sure it contains the lines `<ticketsDir>/*/state.json` and `/.claude/worktrees/`; append each one that is missing. (It is shared by all worktrees of the repository and is never committed.)
5. **Worktree.** Path `<root>/.claude/worktrees/<key-lower>-<slug>`. Take the first case that applies:
   1. *Adoption.* Adopt the worktree found in section 1 step 3 (its branch matches `<KEY>/*`, so it is this ticket's), wherever it lives and whichever checkout this session started in. Otherwise `<session-root>` is a linked worktree when `git -C <session-root> rev-parse --path-format=absolute --git-dir` differs from `--git-common-dir` (a launcher or Orca opened this session in its own worktree). Do not create anything and do not ask Reuse:
      - its branch matches `<KEY>/*`: adopt it;
      - its branch is clean and unused: `git status --porcelain` prints nothing (apart from this ticket's own `ticket.md`), `git rev-parse @{u}` fails, no `<ticketsDir>/*/state.json` exists in it, the branch is not another key's `<OTHER>/*`, and `git merge-base --is-ancestor HEAD BASE_REF` succeeds. Run `git branch -m <KEY>/<slug>` (stop if that branch already exists locally or on `origin`), then `git merge --ff-only BASE_REF`, and adopt it;
      - anything else: stop with a message naming the worktree and its branch. Create nothing.

      An adopted worktree is `<wt>` from here on; the path above does not apply.
   2. *Branch already exists* (locally or on `origin`): ask Reuse (recommended) · Abort. Reuse: if `git worktree list --porcelain` shows the branch checked out in some worktree, adopt that worktree; otherwise `git worktree add <path> <branch>` on every host.
   3. *Host `orca`*: `orca worktree create --repo path:<root> --name <key-lower>-<slug> --branch <KEY>/<slug> --base-branch BASE_REF --setup skip [--parent-worktree worktree:$ORCA_WORKTREE_ID] --json` (the parent option only when `ORCA_WORKTREE_ID` is set, so the worktree becomes a child of the workspace this session runs in); take the path from its JSON (`result.worktree.path`, else `result.path`). If it fails, report it and stop (do not fall back: a later retry would create a second worktree).
   4. *Otherwise* (`iterm`, `plain`): `git worktree add --no-track -b <branch> <path> BASE_REF` (`--no-track`, so a plain push never targets the base).
6. **Ticket dir and state.** `mkdir -p <wt>/<ticketsDir>/<KEY>`. If `<wt>/<ticketsDir>/<KEY>/ticket.md` does not exist (the base branch does not carry it), copy the file from step 1 there. Check `git -C <wt> check-ignore -q <ticketsDir>/<KEY>/state.json`; if it fails, stop and report it (the worktree stays; no `state.json` is written into a tree that would track it). Write `state.json`: `ticket`, `title`, `baseBranch` (bare name), `baseRef`, `branch`, `worktree` (absolute), `host`, `stage` (`"spec"` in heavy mode, `"plan"` otherwise), `mode`, `modeReason`, `modeHistory: []`, `reviewRound: 0`, `decisions: []`, `warnings: []`, `prUrl: null`, `updatedAt`, and with a tracker `tracker` (`type`, `site`, `url`, `status`, `statusAt`, `pending: []`). Create `decisions.md` with the heading `# Decisions`.
7. **Setup.** If `setupCommand` is set, run it with `cd <wt> &&`. Failure is an infrastructure blocker: H6 (Retry · Skip · Abort, section 7). Skip is recorded in `state.warnings` and `decisions.md`; Abort sets `abortedFrom` to the initial stage of step 6 and `stage: "aborted"` (section 10).
8. **Session name.** When `TICKET_FLOW_KEY` is unset (a session started by hand), print one line suggesting `/rename <KEY> · <short title>`. The short title is the title with every character outside `[A-Za-z0-9 .,:()/_-]` replaced by a space, spaces collapsed, cut at the last word boundary within 40 characters. You cannot rename a session yourself.
9. **Tracker `started`** (assign, To Do to In Progress, comment), then **Event `started`**.
10. **Session model** (`references/modes.md#6-session-model-d4`). When `modes.<mode>.session` is stronger than the model this session runs on (order `haiku` < `sonnet` < `opus`; a `claude-...` id counts as the family it names), or either cannot be placed in that order, ask **Stop and restart on `<model>` (recommended)** · **Continue here**. Record the answer in `state.decisions` and `decisions.md` with the `stage: "mode"` shape of `references/modes.md#9-records`; **Continue here** also appends the `stage: "mode"` warning given there to `state.warnings`. Restart: print the worktree path and `cd '<wt>' && claude --model <model> "/ticket <KEY> --resume"`, then end the turn without spawning anything (the resumed session re-enters the recorded stage). A weaker mode in a stronger session is never asked about.
11. Continue with the spec stage (section 5) in heavy mode, with the plan stage (section 6) otherwise.

## 4. Stage prompt and result handling

### Prompt

Build every stage prompt with exactly these lines, in the contract the agents define:

```
WORKTREE=<abs worktree path>
KEY=<KEY>
TICKET_DIR=<abs worktree path>/<ticketsDir>/<KEY>
BASE_BRANCH=<BASE_REF, e.g. origin/main>
MODE_PIPELINE=heavy|medium|light   (ticket-planner, dev, ticket-reviewer)
MODE=implement|fix-review|fix-pr   (dev only)
TASK=T<n>                          (dev with MODE=implement, one run per task, section 7)
REVIEW_ROUND=<n>                   (dev with MODE=fix-review only)
FEEDBACK=<abs path>                (dev with MODE=fix-pr only, section 9b)
REVIEW_SINCE=<commit>              (ticket-reviewer in section 9b only)
FORMAT_CMD=<formatCommand>         (dev only, when configured)
GATE_CHECK=<command>               (ticket-planner only, when found in section 0)
DECISIONS:
<YAML list of state.decisions: stage, question, answer, at; "[]" when empty>
PROJECT_RULES:                     (only when escalationExtra is not empty)
<one "- <rule>" line per entry of escalationExtra>
```

`MODE_PIPELINE` is the current `mode` of `state.json` (`heavy` when it has none); the name differs on purpose from the dev agent's `MODE=`. `REVIEW_ROUND` is `reviewRound` after it was incremented (section 8). For the planner, append the developer's change request under a `FEEDBACK:` line when re-planning. Spawn with the Agent tool: `subagent_type` from the table above, a short `description`, and `model` from `models` (`spec`, `plan`; for the reviewer see section 8; for dev see **Dev tier** below). Foreground only. Do not pass `effort`; the agent files carry it.

**Dev tier.** Tiers in order: `pawn` < `workhorse` < `godzilla` (`references/pipeline-spec.md` §5b). Each dev run has a tier: `implement` uses the task's tier in `state.taskTiers` (`state.devTier` without tasks, section 6), fix-review round 1 uses `state.devTier`, fix-review round 2+ one tier up (section 8), fix-pr per round (section 9b step 6); never above `godzilla`. The mode bounds the tier chosen for a run (heavy: at least `workhorse`; light: at most `workhorse`); promotion is not bounded by the mode. Spawn `gated-dev-simple-workflow:ticket-dev-<tier>` with `model` `models.devGodzilla`, `devWorkhorse` or `devPawn`. **Promotion** (in every dev run, before any H-point; each tier is left at most once per run): a `tier too low:` question, or a `verification failed:` question from `pawn` or `workhorse`, re-runs the same dev stage with the same prompt one tier up (it continues from the committed trailers), with no question. Append `{from, to, reason: <question>, stage: <dev | fix-review | fix-pr>, task: <T<n>, only for a task run>, at}` to `state.devTierHistory` and `{stage: "dev-tier", command: "<from> -> <to>", reason, at}` to `state.warnings`, and print one line. From `godzilla`: `tier too low:` is H3, `verification failed:` is H6. The retry of a failed run (Parse, step 3) stays on the same tier.

### Parse

Read the agent's final message in this order:

1. **`NEEDS_DECISION`** as a line of its own anywhere (even with a `RESULT:` line): for a dev run whose `question:` starts with `tier too low:` or `verification failed:`, apply promotion (Dev tier above) first; otherwise parse `stage`, `question`, `context`, `options` (`label | consequence`), `recommendation`. Ask H1 (`stage: spec`) or H3 (any other stage) with the options plus free text, recommended first. Append the answer to `decisions.md` (a `## <ISO time> (<stage>)` entry with question and answer) and to `state.decisions` (with `at`), then (with a tracker, when an H1 or H3 answer changes scope) queue a tracker comment (`references/jira.md` §5), then run **the same stage again**. Once the plan is approved (stage `dev` or later) `decisions.md` is tracked: after each append, format it and commit it alone with an explicit pathspec (`git -C <wt> commit -m "docs(<KEY>): record decisions" -m "<trailer>" -- <path>`). Before H2 it stays uncommitted. A denied or gated call reported this way is handled the same: show the exact command and reason; if the developer says go ahead, record that as the decision and re-run the stage.
2. **`RESULT: OK <path>`** as the last non-empty line: the file must exist at that path; otherwise it is a failed run.
3. **Anything else** (empty, tool error, crash, no marker): failed run. Retry the same stage **once** automatically. A second failure goes to H7 (Retry · Abort) with an error summary.

### Scope check

After spec, plan and review stages only `<ticketsDir>/<KEY>/` may change. Run `git -C <wt> status --porcelain`. Any path outside that directory fails the stage: discard nothing, list the paths, and go to H7 (Retry · Abort) without the automatic retry.

### Raising the mode

The mode is only ever raised, by R1 to R4 of `references/modes.md#5-raising-the-mode-d3` (sections 6, 7 and 9b say where), and to `heavy` only before H2 is approved. A request to lower it (at H2, or a planner line naming the current or a lighter mode) is not applied: say that a mode is never lowered and go on with the current one. To raise from `<from>` to `<to>`:

1. Record it before anything else: set `stage` to the stage the raise enters (`spec` for a raise to heavy, `awaiting_plan_approval` for R2 and R3 to medium, `review` for R4 after dev, `addressing_review` for R4 in section 9b), append `{from, to, by, stage, reason, at}` to `state.modeHistory` (`by`: `planner`, `developer` or `riskPaths`), append `{stage: "mode", question: "raise mode <from> to <to> (<by>)", answer: "<reason>", at}` to `state.decisions` and the matching `## <ISO time> (mode)` entry to `decisions.md` (committed alone as above once the plan is approved), and set `mode` to `<to>` and `modeReason` to the reason. Print `Mode: <from> -> <to> (<reason>)`.
2. When `modes.<to>.session` is stronger than the running model, ask the session-model question of section 3, step 10 (on restart the resumed session re-enters the stage set in 1).
3. Continue as the section that raised says.

## 5. Spec stage

Stage `spec`. Spawn the spec agent. On `RESULT: OK`, run the scope check. A blocking question arrives as `NEEDS_DECISION` with `stage: spec` (H1): handle it per section 4 and re-run. Then set `stage: "plan"`. **Tracker `spec-done`**, then **Event `spec-done`**.

## 6. Plan stage and the plan gate (H2)

Stage `plan`. Spawn the planner. A `NEEDS_DECISION` is handled per section 4 and the planner re-runs (in medium and light mode a missing or unclear acceptance criterion arrives this way, `stage: plan`, and takes H1's role). On `RESULT: OK`, run the scope check, then read the first non-empty line of the `## Mode` section of `plan.md` (`references/modes.md#the--mode-section-of-planmd`):

- `raise to heavy: <reason>` in light or medium mode (R1): raise (section 4, `by: planner`), run the spec stage (section 5), then this section again with the spec.
- `raise to medium: <reason>` in light mode (R2): raise (section 4, `by: planner`); nothing re-runs.
- `keep`, a line naming the current or a lighter mode, a missing section or any other line: the mode stays (for the last two, H2 says the planner gave no valid mode line).

Then set `stage: "awaiting_plan_approval"` and ask **H2**. It is a stop in every mode. Show:

- a short summary of the plan (steps in order, verification choice);
- the path to `plan.md`;
- the whole `## Gated operations` table (or "None");
- open decisions or risks the plan flags;
- `Mode: <mode> (<modeReason>)`, and any raise the planner made;
- the dev tier, one line: `Dev: <tier> (<reason>)`; with tasks, one line per task: `T<n> (steps <list>): <tier> (<reason>)`.

Choose the tier before asking, for each task of `## Tasks` (only its own steps count for the rules) and for the plan as a whole, by the first rule that applies: config `devTier` other than `auto`; a tier the developer named in earlier H2 free text; `workhorse` instead of `pawn` when `## Gated operations` lists a call, a planned file matches `riskPaths` (section 8), or a step is marked as a risk; the task's `Tier:` line, or for the plan its `## Dev tier` line; `workhorse`. Then apply the mode bound: in heavy mode at least `workhorse`, in light mode at most `workhorse` (a light plan that needs `godzilla` raises the mode instead, `references/modes.md`). The reason names the rule (`config`, `developer`, `planner: <its reason>`, `pawn not allowed: <why>`, `default`, plus `mode bound` when it changed the tier). A mode raise at H2 re-applies the bound. `devTier` is the plan's tier and never below the highest task tier. A plan whose `## Tasks` does not cover every step exactly once, in order, goes back to the planner with that as `FEEDBACK:` before H2. No extra question.

Options: **Approve (recommended)** · **Request changes** · **Abort**.

- **Request changes:** take the free text. When it asks for a heavier mode ("use heavy", "add a review"; R3, `by: developer`), raise first (section 4): to `heavy`, run the spec stage (section 5), then re-run the planner with the rest of the text under `FEEDBACK:`; to `medium` (from light) with no other change request, ask H2 again, nothing re-runs. A request for a lighter mode is refused (section 4). When the text only names a tier (`godzilla`, `workhorse`, `pawn`) and asks for nothing else, take that tier (unless config fixes one; the mode bound applies), re-show H2 and do not re-plan. Any other change request: set `stage: "plan"`, re-run the planner with the text under `FEEDBACK:`, then ask H2 again. No round limit.
- **Abort:** section 10.
- **Approve:**
  1. `git -C <wt> status --porcelain`: a path outside `<ticketsDir>/<KEY>/` is a scope failure (section 4); never approve with stray changes.
  2. Format `ticket.md`, `spec.md` (when it exists, heavy mode), `plan.md`, `decisions.md`, then `git -C <wt> add` those files only.
  3. Commit with the subject `docs(<KEY>): add spec and plan` (`docs(<KEY>): add plan` without `spec.md`) and the trailer.
  4. Write `devTier` and `devTierReason`, and `taskTiers` (`{T<n>: <tier>}`, `{}` without tasks), as shown (`devTierHistory: []` if absent) and set `stage: "dev"`. **Tracker `plan-approved`**, then **Event `plan-approved`**.

Approving the plan also authorises the rest of the run's normal actions: local commits, pushing the ticket branch and opening the PR. Do not ask about them again. Never force push or push to any branch other than the ticket branch.

## 7. Dev stage and blockers (H6)

Stage `dev`. With a `## Tasks` section in `plan.md`, run one dev agent per task, in order, each with `MODE=implement` and `TASK=T<n>` and the task's tier from `taskTiers` (on resume, the highest `to` of the `devTierHistory` entries with `stage: "dev"` and that `task`, if any); promotion per section 4, per task. Skip a task whose steps all have a `Plan-Step: N` commit in `git -C <wt> log <BASE_REF>..HEAD`. After each task's `RESULT: OK`, check that every step of the task has such a commit (otherwise a failed run) and that the tree is clean (leftovers: the question below), then start the next task. Without `## Tasks`, run one agent of tier `devTier` (on resume as above, without `task`) with `MODE=implement` and no `TASK`. After the last run's `RESULT: OK`, `dev-report.md` exists; check that `git -C <wt> log --format=%H --grep='^Plan-Step: ' <BASE_REF>..HEAD` prints at least one commit (none: failed run) and that `git -C <wt> status --porcelain` is clean. Leftover changes: show them and ask **Commit them (recommended)** (format, stage with the exclude pathspec above, commit `chore(<KEY>): commit leftover changes from dev stage` plus the trailer) · **Stash** (`git stash push -u`) · **Abort**. Then **Tracker `dev-done`**, then **Event `dev-done`**, then the next stage:

- heavy and medium mode: set `stage: "review"` (section 8).
- light mode: run the `riskPaths` check of section 8 (R4, `references/modes.md#8-light-mode-and-review`). Any output: raise to medium (section 4, `by: riskPaths`, the reason names the paths) and go to section 8, where the reviewer runs on `models.reviewRisk`. No output: set `stage: "complete"` and go to section 9a; the review stage does not run.

**H6.** A dev `NEEDS_DECISION` whose `question:` starts with `verification failed:` from `godzilla` (the agent's 2 fix attempts are used up and no tier is left to promote to, section 4; every other dev `NEEDS_DECISION` is H3 or a promotion), and any infrastructure blocker (setup fails, Docker down, a service will not start), get the same prompt: the failing command and an output excerpt, options **Retry** · **Skip this check** · **Abort**.

- Retry: re-run the stage (dev, same tier) or the command.
- Skip: append `{stage, command, reason, at}` to `state.warnings` and record the skip as a decision (section 4); it is listed under "Skipped checks" in the PR. If the skip answers a dev `NEEDS_DECISION`, there is no `dev-report.md`: do not re-run dev; write a minimal one yourself (`## Steps done` from the `Plan-Step:` commits, `## Verification` with the skipped command marked SKIPPED and its reason, `## Deviations from the plan`: "None known; verification skipped for the reason above"), format and commit it as `docs(<KEY>): add dev report` plus the trailer, then continue with the next stage as after `RESULT: OK` above.
- Abort: section 10. Never launch or restart apps or Docker to clear a blocker.

## 8. Review stage and loop

Stage `review`. Runs in heavy and medium mode; a light run reaches it only after a raise (section 7). Choose the reviewer model (the `riskPaths` check): run `git -C <wt> diff --name-only <BASE_REF>...HEAD -- <pathspecs>` with one `':(glob)<pattern>'` per entry of `riskPaths`. Default `riskPaths`: `**/migrations/**`, `**/*policy*`, `**/auth/**`, `**/*.tf`, `infra/**`, `.github/workflows/**`, `.claude/**`. Any output: `models.reviewRisk`; none: `models.review`.

Spawn the reviewer. On `RESULT: OK`, `review.md` must exist and its last line must be `VERDICT: PASS` or `VERDICT: CHANGES_REQUIRED`; otherwise it is a failed run. Run the scope check, then format and commit `review.md` as `docs(<KEY>): add review round <reviewRound+1>` plus the trailer. **Tracker `review-done`**, then **Event `review-done`**.

- `VERDICT: PASS`: set `stage: "complete"` and go to section 9a.
- `VERDICT: CHANGES_REQUIRED`:
  - `reviewRound >= 2`: ask **H5** with the remaining findings: **Accept as is** (go to 9a; append `{stage: "review", command: "accepted open findings", reason: <findings>, at}` to `state.warnings`) · **One more round** (continue below) · **Take over manually** (leave `stage: "review"`, print the worktree path and how to resume, stop).
  - Otherwise increment `reviewRound`, run the dev agent with `MODE=fix-review` and `REVIEW_ROUND=<reviewRound>` on `devTier` in round 1 and one tier up from round 2 (section 4), handle its result as in section 7 (a fix round adds `Review-Fix:` commits, so skip the `Plan-Step:` check), then review again.

Minor findings are never auto-fixed; they are listed in the PR.

## 9. Resume

`--resume` (and Resume from section 1) re-enters the recorded `stage` from its start, with the recorded `mode` (`references/modes.md#10-resume`; a state without `mode` counts as `heavy`). First:

1. A `--mode` equal to the recorded mode changes nothing; one that differs is refused with a message naming the recorded mode: change nothing and stop (a raise happens only through section 4). Read the config again (section 0) and repeat the exclude and `check-ignore` checks of section 3, steps 4 and 6 (the exclude entries may be missing in a fresh clone). Then **Tracker `resumed`**: refetch the issue, and on a changed summary or description ask as `references/jira.md` §3 says, before the working-tree check below. Then **Event `resumed`**.
2. `git -C <wt> status --porcelain`. In stages `spec`, `plan` and `awaiting_plan_approval`, changes inside `<ticketsDir>/<KEY>/` are the stage's own output; in stage `review` the same holds for `review.md`. Any other change must be shown before you spawn anything: Stage `merged` needs no question (section 9d keeps a dirty worktree). In stages `dev`, `complete`, `done` and `addressing_review` ask **Keep and continue (recommended)** · **Stash** · **Abort**; in every other stage ask **Stash (recommended)** · **Abort**.
3. Before re-entering, ask the session-model question of section 3, step 10 for the recorded mode, unless `state.decisions` already has a **Continue here** for that mode. Stage `awaiting_plan_approval`: ask H2 again. Stage `aborted`: ask which stage to re-enter (default `abortedFrom`; only stages the recorded mode runs). Stage `addressing_review`: section 9b from step 1 (its PR-state check sends a merged PR to section 9d and stops on a closed one; for an open PR it continues at step 3, the tree check was done here). Stage `done`: report the PR and go to section 9c (the watch restarts). Stage `merged`: section 9d.
4. Stage `dev`: section 7 again; it continues with the first task that has a step without a `Plan-Step: N` commit (the agent skips steps already committed). Stage `review`: re-run the reviewer. Stage `complete`: section 9a (it reuses an existing PR).

## 9a. Complete

Stage `complete`. You do this yourself; no subagent.

1. Run the `## Verification` commands of `plan.md` in order, one at a time with `cd <wt> &&`, minus checks skipped under H6. Failure, or a needed app or service that is down: H6. Never start them.
2. The tree must be clean (leftovers: the Commit · Stash · Abort question of section 7). Push: `git -C <wt> push -u origin <branch>`. Plain push of the ticket's own branch only; never force.
3. Write the PR body to a file in the scratchpad (not in the worktree), with these sections in order:
   - `## Summary`: what changed and why, from `spec.md` (`ticket.md` and `plan.md` without one) and `dev-report.md`, a line `Mode: <mode> (<modeReason>)`, and one line naming the dev tier(s) that wrote the code (`devTier`, plus each `to` of `devTierHistory`, e.g. "Dev tier: pawn, promoted to workhorse (tier too low)").
   - `## Spec & plan`: links to `<ticketsDir>/<KEY>/ticket.md`, `spec.md` (when it exists) and `plan.md` on the branch, plus the Jira link from **Tracker `pr-body`** and any lines from **Event `pr-body`**.
   - `## Verification`: each command and its result.
   - `## Open minor findings`: the `## Minor` list of `review.md` (when a review ran), plus findings accepted under H5 (or "None").
   - `## Skipped checks`: every `state.warnings` entry with its `command` and `reason`, except those whose `stage` is `tracker`, `rules` or `dev-tier` (`mode` ones are listed; promotions show in the `## Summary` tier line), and in light mode the line `No review stage ran` (or "None").

   End the body with the pull-request attribution line your session's instructions specify, if any.
4. Open the PR: if `gh pr list --head <branch> --json url` returns one, reuse it; otherwise `gh pr create --base <baseBranch> --head <branch> --title "<type>(<KEY>): <title>" --body-file <file>`. The type comes from the spec, or the ticket without one (`feat` by default, `fix` for bugs, `docs` for documentation-only work). Save the URL as `prUrl`. **Tracker `pr-opened`** (to In Review, plus a comment with the PR link), then **Event `pr-opened`** (both also when an existing PR was reused).
5. Set `stage: "done"` and print the final report: PR URL, worktree path, the decisions taken, the warnings, the minor findings still open. Then go to section 9c.

## 9b. Address PR review (`--address-review`)

Stage `addressing_review`. Runs when the PR of a finished run has new human feedback: started by the developer with `--address-review`, or by the watch of section 9c. The only subagents are the dev agent of the round's tier (`MODE=fix-pr`, then `fix-review` if needed) and `ticket-reviewer` (not in light mode unless step 7 raises it). This section authorises the commits, the plain push of the ticket branch and the GitHub replies below, and nothing else. Never force push, resolve a review thread or re-request a review. Every PR reply starts with the marker line `<!-- gated-dev -->`; the watcher ignores comments that carry it.

1. **Preconditions.** `stage` must be `done` or `addressing_review`, and `prUrl` must be set with `gh pr view <prUrl> --json state,number,createdAt,headRefName` reporting `OPEN`. `MERGED`: section 9d. `CLOSED`: report it and stop. Any other stage or state: report it and stop. Whenever it stops here it changes nothing except `updatedAt`. Unless you arrive from section 9 (which did both), run the working-tree check of section 9, step 2 (stage `done` counts like `complete`) and the exclude and `check-ignore` checks of section 3, steps 4 and 6.
2. **Stage.** Set `stage: "addressing_review"`.
3. **Collect feedback.** Read reviews and issue comments with `gh pr view <prUrl> --json reviews,comments` and inline comments with `gh api repos/{owner}/{repo}/pulls/{n}/comments --paginate`, keeping `id`, `path`, `line`, `in_reply_to_id`, `created_at`, `user.login` and `body`. A review's time is its `submittedAt`. Keep an item only if it is newer than `lastAddressedCommentAt` (the PR's `createdAt` when that is absent), its author is not a bot (login ends in `[bot]`), and its body is not empty and does not contain the marker; a review with an empty body counts only when its state is `CHANGES_REQUESTED`. For a reply inside a thread, also keep the thread's root comment as context.
   - If the highest-numbered `pr-feedback-<n>.md` has an item newer than `lastAddressedCommentAt`, that round was interrupted: reuse that file and number and go to step 4 (items already fixed or answered show up as `PR-Fix:` trailers and marked replies; steps 6 and 8 skip them). Otherwise `n` is the next free number.
   - Nothing kept: set `lastAddressedCommentAt` to the newest time among the items that passed the time filter (if any, so the watcher does not wake for them again), `stage: "done"`, report "no new PR feedback" and go to section 9c.
4. **Sync the branch.** `git -C <wt> fetch origin <branch>`, then `git -C <wt> merge --ff-only origin/<branch>` so the local branch is not behind. If the branches diverged, show the divergence and ask H6-style **Retry** · **Abort**; never rebase, merge or force push on your own.
5. **Classify.** Write `TICKET_DIR/pr-feedback-<n>.md`: a header with the PR URL, the collection time and `Base commit: <sha of HEAD now>`, then one `## Item <id>` block per item with `Source` (inline, review or comment), `Author`, `Created`, `Location` (`path:line` or n/a), `Thread` (root id or none), `Body`, `Class` and `Plan` (what to change, the draft answer, or the reason to decline). Decide `Class` yourself: `change` (actionable), `question` (needs an answer, no code), `decline` (you would not make the change). Answer `question` items from `spec.md` (when it exists), `plan.md`, `dev-report.md` and the diff. Ask the developer (H3, recommended option first) before dev runs when an item is ambiguous, changes scope or acceptance criteria, falls under the escalation list of the pipeline (`references/pipeline-spec.md` §4, plus `escalationExtra`), needs a gated operation the plan does not list, is a `question` you cannot answer, or is a `decline` (always confirm: **Decline with reason (recommended)** · **Make the change** · **Skip without a reply**). Record the answers per section 4 (stage `addressing_review`), update `Class` and `Plan` to the outcome, then format the file and commit it alone as `docs(<KEY>): add PR feedback round <n>` plus the trailer. If no item is `change`, skip steps 6 and 7 and the verification and push of step 8, and only post the replies.
6. **Dev.** Choose the round's tier from `pr-feedback-<n>.md`: `pawn` when every `change` item is mechanical (wording, rename, a missing test for a stated case), otherwise `devTier`; at least `workhorse` when any `change` item touches `riskPaths` (section 8). Spawn the dev agent of that tier (section 4) with `MODE=fix-pr` and `FEEDBACK=<abs path of pr-feedback-<n>.md>`. It fixes every `change` item, commits with `PR-Fix: <comment id>` trailers and appends `## PR feedback round <n>` to `dev-report.md`. Handle the result as in section 7: `NEEDS_DECISION` per section 4 (promotion first; H6 for `verification failed:` from `godzilla`); `RESULT: OK` needs at least one commit in `git -C <wt> log --grep='^PR-Fix: ' <Base commit>..HEAD` (instead of `Plan-Step:`) and a clean tree.
7. **Review.** In light mode, first run the `riskPaths` check of section 8 on the branch diff (R4, `references/modes.md#8-light-mode-and-review`): any output raises to medium (section 4, `by: riskPaths`, the stage stays `addressing_review`) and the review below runs, in this round and later ones; no output skips the rest of this step (no reviewer, no review commit). In heavy and medium mode (a raise included), spawn `ticket-reviewer` with `REVIEW_SINCE=<Base commit>` and the model choice of section 8. Run the scope check, then format and commit `review.md` as `docs(<KEY>): add review for PR feedback round <n>` plus the trailer (not the section 8 subject, so the rounds stay distinguishable), then **Tracker `review-done`**, then **Event `review-done`**. `VERDICT: CHANGES_REQUIRED`: increment `reviewRound`, run one dev `MODE=fix-review` round (section 8, its tier rule), review again; if it still returns `CHANGES_REQUIRED`, ask H5 as in section 8 (**Accept as is**, recorded in `state.warnings` and listed in the summary comment of step 8 · **One more round** · **Take over manually**, leaving `stage: "addressing_review"`).
8. **Verify, push, reply.**
   1. Run the `## Verification` commands of `plan.md` as in section 9a, step 1 (H6 on failure). The tree must be clean; then push with a plain `git -C <wt> push` (never force).
   2. Reply on GitHub. Write each body to a file in the scratchpad and post it with `-F body=@<file>`. Every body starts with the marker line, then a short note: the commit hash(es) (`git log --grep="^PR-Fix: <id>"`) for a `change`, the answer for a `question`, the reason for a `decline`. Skip an item that already has a later marked comment in its thread (for reviews and issue comments: a PR comment containing `PR feedback round <n>`), so a re-run never posts twice.
      - Inline comment: reply in its thread, to the root comment id: `gh api repos/{owner}/{repo}/pulls/{n}/comments/{id}/replies -F body=@<file>`.
      - Review or issue comment: one PR comment for all of them, `gh pr comment <prUrl> --body-file <file>`: the marker line, a heading `PR feedback round <n>`, one line per item (id, outcome), plus any checks skipped or findings accepted in this round (`state.warnings`). When step 7 skipped the reviewer (light mode), add the line `No review ran in this round (light mode)`, and post this comment even when the round has only inline items.
9. **Close.** Set `lastAddressedCommentAt` to the newest `Created` among the items of `pr-feedback-<n>.md` (ISO-8601) and `stage: "done"`. **Tracker `pr-feedback-addressed`** (comment only, no transition), then **Event `pr-feedback-addressed`**. Print: PR URL, items by class, commits, replies posted, warnings. Threads stay unresolved and no review is requested; the developer does that. Then go to section 9c. **Take over manually** and **Abort** do not restart the watch.

## 9c. Watch the PR

Stage `done`, PR `OPEN`. The session stays open and wakes itself when there is something to do.

1. Re-read `state.json` and write `updatedAt` (stage unchanged).
2. Start the watcher with the Bash tool and `run_in_background: true`: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/watch_pr.py" --state <TICKET_DIR>/state.json` (absolute path). It polls the PR about once a minute with `gh`, writes `watch.heartbeatAt` into `state.json`, and ends with one JSON line on stdout.
3. Print one line ("Watching <prUrl>; this session wakes on new feedback, merge or close. Closing it stops the watch; `/ticket <KEY> --resume` restarts it.") and end the turn. Do nothing else while it runs.
4. On wake-up, read the last stdout line of the background command as JSON (`outcome`):
   - `feedback`: section 9b from step 1; it comes back here when done;
   - `merged`: section 9d;
   - `closed`: report that the PR was closed without a merge, stop watching, change nothing else;
   - `error`, a last line that is not JSON, or a killed process: report the message, stop watching, and say that `/ticket <KEY> --resume` restarts the watch.

## 9d. Merge cleanup

Stage `merged`. Runs when the watcher reports `merged`, when section 9b finds the PR merged, and on `--resume` in stage `merged`. Every step can run again safely.

1. Confirm with `gh pr view <prUrl> --json state,mergedAt` that the PR is `MERGED`, then set `stage: "merged"`. Take the PR's head commit from `gh api repos/{owner}/{repo}/pulls/{n} --jq .head.sha` (`gh pr view` has no head-commit field in every `gh` version).
2. Print the final report: PR URL, merge time, worktree path, branch (it is kept), and whether the worktree will be removed (step 4). Then **Tracker `merged`** (to Done unless it is already Done, then the `Pipeline: PR merged` comment unless one exists; a failure is a warning, and step 4 still removes the worktree: the PR is merged, so Jira lagging must not keep a worktree, unlike the source project, which stopped before removal), then **Event `merged`** (a re-run of this section runs it again, so the rules file should say how to skip work already done).
3. Notify: `bash "${CLAUDE_PLUGIN_ROOT}/hooks/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/notify.py" "<KEY> merged" "<one line>"`. A failure is ignored; the report is already printed.
4. Remove the worktree, as the very last action, and only when `git -C <wt> status --porcelain` prints nothing (the ignored `state.json` does not count) and `git -C <wt> rev-parse HEAD` equals that head commit; otherwise keep it and say why. If `orca` is on `PATH` and `orca worktree list --repo path:<root> --json --limit 500` has an entry whose `path` equals `<wt>` and whose `isMainWorktree` is false (do not use `ORCA_WORKTREE_ID`): `orca worktree rm --worktree path:<wt> --json`; exit 143 with no output is expected when this session runs inside that worktree. Otherwise: `git -C <root> worktree remove <wt>`. Never pass `--force` or `--delete-branch`, and never delete a branch.

## 10. Abort

On Abort (H2, H6, H7, a restart, or a user request): set `abortedFrom` to the current stage, `stage: "aborted"`, **Tracker `aborted`** (comment with the reason, no transition), then **Event `aborted`**, and **keep** the worktree and branch untouched. Tell the developer the worktree path and that `/ticket <KEY> --resume` continues.
