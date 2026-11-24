# Jira tracker: reference

Used only when `.claude/gated-dev-simple-workflow/config.json` has `"tracker": {"type": "jira", ...}`.
Without that key none of this applies: `ticket.md` is the ticket and no Jira
call is made. `SKILL.md` refers to the sections below by number. Stage agents
never read this file and never call Jira.

## 1. Tools

The orchestrator uses the Jira tools of this session (a Jira MCP server or
connector). Needed, by capability:

| Capability | Required |
| --- | --- |
| Read an issue (fields, parent, links, comments) | yes |
| List an issue's transitions | yes |
| Apply a transition | yes |
| Add a comment | yes |
| Assign an issue | yes |
| Read the current user | yes |
| Add a remote link (the PR) | optional |
| Search issues (by JQL) | flow only |

The search capability is needed only by `/ticket-flow` (read issue and search
by JQL); `/ticket` never uses it.

If any of the first six is missing at kickoff, stop before creating anything
with:

```text
tracker "jira" is configured, but this session has no Jira tools (read issue, transitions, comment, assign). Connect a Jira server or remove "tracker" from .claude/gated-dev-simple-workflow/config.json.
```

## 2. Site

Use `tracker.site`. If it is unset and the tools reach exactly one site, use
that one. If they reach several, ask once (H3) and suggest setting the key.
Store the site in `state.tracker.site`, with any site id the tools need, so
later calls do not look it up again.

## 3. Intake

Kickoff always fetches the issue when a tracker is configured. If the issue is
missing or cannot be read, kickoff stops and nothing is created. It writes
`<session-root>/<ticketsDir>/<KEY>/ticket.md`:

```markdown
---
tracker: jira
url: https://acme.example.net/browse/PROJ-1
type: Story
status: To Do
parent: PROJ-0
base: feature/PROJ-0/reports   # only when baseFromParent finds exactly one branch
fetchedAt: 2026-11-21T10:00:00Z
---

# <summary>

<description, converted to Markdown>

## Parent

PROJ-0: <summary> (<status>)

## Linked issues

- is blocked by PROJ-2: <summary> (Done)

## Recent comments

- <author>, <date>: <body>
```

`## Recent comments` holds the last 5 human comments; `Pipeline:` comments are
left out. Leave out `parent`, `## Parent` and `## Linked issues` when the issue
has none.

Rules:

- A file with `tracker: jira` in its front matter was generated: rewrite it
  from Jira.
- A hand-written file (no `tracker:` key): ask **Use Jira (recommended)** ·
  **Keep the file**.
- A description pasted with the command is appended under
  `## Notes from the developer`.
- On `--resume`, refetch the issue. If the summary or description changed:
  before H2, rewrite `ticket.md` and say so; from stage `dev` on, ask **Keep
  the current spec (recommended)** · **Re-run from spec** (which sets the
  stage to `spec` and rewrites `ticket.md`).

Conversion: Jira rich text becomes Markdown, keeping headings, lists, code and
links. Attachments are listed by name only.

`baseFromParent`: when set and the issue has a parent, replace `{parent}` in
the glob with the parent key and run
`git ls-remote --heads origin "<glob>"`. Exactly one match becomes the base
branch (recorded as `base:` in the front matter). Zero matches: the normal
base rules apply. More than one: ask (H3).

## 4. Status lifecycle

Forward only.

| When | Moves | Only if the current status is |
| --- | --- | --- |
| Kickoff (`started`) | to `inProgress` | in `startFrom` (default: any status in Jira's To Do category) |
| PR opened or reused (`pr-opened`) | to `inReview` | `inProgress` or a `startFrom` status |
| PR merged (`merged`) | to `done` | not already `done` |
| Abort, restart, PR feedback rounds, resume | none | n/a |

Also at kickoff, when `assign` is true (default), assign the issue to the
current user.

Target names come from `tracker.statuses` (`inProgress`, `inReview`, `done`;
defaults `In Progress`, `In Review`, `Done`), matched without regard to case.
`inReview: null` skips that step.

Finding the transition: list the issue's transitions and take the one whose
**target status** has the configured name; if none, the one whose own name
matches. If none is found, add a `tracker` warning (`no transition to <X> from
<Y>`) and go on.

No other status change is ever made, and the issue is never moved backwards.

## 5. Comments

Posted unless `tracker.comments` is `false` (status changes happen either way).

```text
Pipeline: <event> finished
Done: <what actually happened, with file paths or commit hashes>
Next: <the next stage, or "waiting for plan approval">
Blockers: <none, or the open question or warning>
```

Events: `started`, `spec-done`, `plan-approved`, `dev-done`, `review-done`,
`pr-opened` (with the PR link; also add a remote link when the tools support
one), `pr-feedback-addressed`, `merged` and `aborted`, plus one for every
H-answer that changes scope.

- Every comment starts with `Pipeline:` so intake can skip it.
- `merged` is skipped when the issue already has a `Pipeline: PR merged`
  comment, so re-running the merge step is safe.
- Before posting a comment, post every queued `state.tracker.pending` comment,
  oldest first.

## 6. Failures

Every call other than the kickoff read: on failure append `{stage: "tracker",
command: "<operation>", reason, at}` to `state.warnings` and continue. Retry
nothing on the spot.

- A failed comment goes to `state.tracker.pending`.
- A failed transition is retried once at the next event, then left as a
  warning.
- `tracker` warnings are left out of the PR's "Skipped checks".

## 7. State

`state.json` gets `tracker`:

| Field | Meaning |
| --- | --- |
| `type` | `jira` |
| `site` | Site host name, plus any site id the tools need |
| `url` | Issue URL |
| `status` | Last known Jira status |
| `statusAt` | When `status` was last read or changed |
| `pending` | Comment bodies not yet posted, oldest first |

Every writer of `state.json` re-reads it and keeps `tracker`, like `rules`.
`--status` shows the last known status and may do one read-only fetch.

## 8. Authorisation

`/ticket <KEY>` with a tracker configured authorises exactly these operations:
the reads of section 1, the assignment, the three transitions of section 4
and the comments of section 5. They need no extra question.

Anything else in Jira is never done, even when a rules file asks for it:
creating issues, editing fields other than the assignee, other transitions,
deleting.

`/ticket-flow PARENT` authorises only: reading issues and searching by JQL
(the parent, its subtasks and their links), and launching `/ticket` sessions.
It never writes to Jira. The launched `/ticket <KEY>` session does the
assignment, transitions and comments above under its own authorisation.
