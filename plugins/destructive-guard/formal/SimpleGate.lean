/-!
# Lean gate: Jev decides; if Jev cannot, a person does

Model of the lean `destructive-guard` (decisions D1-D12: see "Design decisions and history" in the README):

1. call → Jev (redacted) → risky: `ask` / not risky: no decision, so Claude
   Code's own permissions apply.
2. Jev configured but unavailable (timeout, HTTP error, network error,
   malformed answer) → a warning (`systemMessage`) **and** `ask` (D11).
3. A plugin fault (unreadable hook input, no suitable Python, internal error)
   → a warning **and** `ask`, whether or not a key is set (D12).
4. Jev misconfigured (no key, bad URL or prompt, bad value) → no decision, no
   per-call warning; `sessionStart` warns once instead (D3). `GATE_ENABLED=0`
   is the only off switch and is outside the model: the hook is not installed.
5. A call that mentions a `.env` file is never sent to Jev: no decision plus a
   warning; Claude Code's own permissions apply.

No rule packs, no fail-closed asks other than 2 and 3. Everything else is
redacted before sending.

## Theses (all proved below)

| # | Name | Statement |
|---|------|-----------|
| S1 | `never_allows` | The hook never returns `allow` or `deny`. |
| S2 | `asks_iff` | The hook asks **iff** it is a plugin fault (bad input, no Python, internal error), or Jev is on, the call was sent (no `.env` reference) and Jev either answered with `risk > escalate` or was unavailable. A `.env` call and a misconfigured setting never ask. |
| S3 | `warning_decision` | With a warning, the hook asks iff the reason is `jevUnavailable` or `pluginFault`; the `.env` warning has no decision. |
| S4 | `fallback_is_claude` | With the `.env` warning, or misconfigured, the final permission is exactly Claude Code's own. |
| S5 | `never_weaker` | The final permission is never weaker than Claude Code's own (allow < ask < deny): the plugin can only add prompts. |
| S6 | `threshold_monotone` | Lowering `escalate` never removes an ask. |
| S7 | `sees_only_redacted` | The outcome depends on Jev only through redacted payloads that mention no `.env` file. |
| S8 | `answer_no_warning` | A Jev answer never shows a warning. |
| S9 | `misconfigured_warns_once` | `sessionStart s = some w ↔ s = .misconfigured w`, and the hook never warns for a misconfigured setting. |
| S10 | `unavailable_asks` | Jev on, call sent, Jev unavailable ⇒ ask with that warning: an unreachable Jev never silently lets a call through. |

## Assumptions about Claude Code (not proved by Lean)

Checked in L0 against the documentation and a throwaway hook; evidence and
Claude Code version are in the table at the end of this comment.

* A1 A hook `ask` makes Claude Code prompt even when a settings `allow` rule
  matches the call (`effective`).
* A2 A settings `deny` wins over a hook `ask` (`effective`).
* A3 No hook decision leaves the call to Claude Code's permission rules and mode.
* A4 `bypassPermissions` is modelled as Claude's own permission being `allow`;
  a hook `ask` still prompts there (so S5 holds in that mode too).
* A5 Auto mode: a hook `ask` is handled like any `ask` (see the evidence).
* A6 `systemMessage` from `PreToolUse` and `SessionStart` is shown to the user.
* A7 One `PreToolUse` output may carry both `systemMessage` and
  `hookSpecificOutput.permissionDecision: "ask"`; both take effect (D11, D12).
* A8 `SessionStart` cannot block or stop the session; its `systemMessage` is
  emitted (C0, for the halt).
* A9 `UserPromptSubmit` printing `{"decision":"block","reason":...}` rejects the
  prompt: the model never runs and the reason is shown (C0).
* A10 `PreToolUse` printing `permissionDecision: "deny"` with `continue: false`
  and `stopReason`: the call does not run and Claude stops; this wins over a
  settings `allow` rule and over `bypassPermissions` (C0).

### Evidence (L0, 2026-11-22, Claude Code 2.1.330)

Docs: hooks <https://code.claude.com/docs/en/hooks> (#pretooluse-decision-control,
#json-output, #how-a-hook-resolves, #pretooluse-ask-decisions-in-auto-mode),
permissions <https://code.claude.com/docs/en/permissions>
(#manage-permissions, #precedence-among-rules-hooks-and-mods), modes
<https://code.claude.com/docs/en/permission-modes>.
Empirical: throwaway hook via `claude -p --settings <tmp.json> --setting-sources ""
--output-format stream-json --verbose`. `-p` has nobody to answer a prompt, so an
`ask` shows as a `permission_denied` event with `decision_reason_type: "hook"`
and the call does not run; "prompted" below means exactly that.

| # | Result | Evidence |
|---|--------|----------|
| A1 | verified | allow rule `Bash(touch m_*)`: no hook runs the command; hook `ask` is prompted and the command does not run. Docs: "force a prompt". |
| A2 | verified | deny rule + hook `ask`: "Permission ... has been denied", not run. Docs: deny, then ask, then allow; hooks do not bypass deny. |
| A3 | verified | hook silent: allow rule runs the command; no rule asks via Claude Code's own check; deny rule denies; bypass runs it. Docs: "no output means the hook has no decision". |
| A4 | verified | `--permission-mode bypassPermissions`: hook silent runs; hook `ask` prompted, not run; deny rule still denies. The docs do not state the hook case; only the test shows it. |
| A5 | verified | `--permission-mode auto`: hook silent runs; hook `ask` prompted, not run. Docs: "a PreToolUse hook's ask normally sends the tool call to the permission prompt"; exceptions for protected paths and unsandboxed retries are not modelled. |
| A6 | partly verified | `PreToolUse` verified in stream-json (`informational`: "PreToolUse:Bash says: ..."); `SessionStart`: output reaches the harness (`hook_response` event), display in the interactive UI documented, not verified (no TTY in `-p`). |
| A7 | verified | one output with `systemMessage` and `permissionDecision: "ask"` gave both the informational message and the prompt (default, bypass, auto, dontAsk modes). |
| A8 | verified | C0, 2.1.330, `-p`: `SessionStart` output `{"systemMessage":"SS-MSG-A8"}` arrives as a `hook_response` event; with `continue:false`, `stopReason` and `decision:"block"` added the session still ran (assistant turn, `stop_reason: end_turn`). Display in the interactive UI is documented, not verified (no TTY). |
| A9 | verified | C0, 2.1.330, `-p`: `{"decision":"block","reason":"UPS-REASON-A9"}` gave `informational` "UserPromptSubmit operation blocked by hook: UPS-REASON-A9" with `prompt_submit_outcome.kind: "blocked_by_hook"`, `prompt_kept: false`, `num_turns: 0`, cost 0: no assistant turn. Interactive mode not verified (no TTY). |
| A10 | verified | C0, 2.1.330, `-p`, marker file `touch m_a10`: hook `deny` + `continue:false` + `stopReason` left no marker and no assistant turn after the call (result `stop_reason: "tool_use"`); `tool_result` "PreToolUse:Bash hook error: <reason>", `permission_decision` source `hook`. Same with an allow rule plus `--allowedTools` and with `--permission-mode bypassPermissions`. Control without hook in bypass ran the command. `stopReason` text itself is not shown in `-p` output; only the deny reason is. |

Also seen: `dontAsk` with an allow rule and a hook `ask` is prompted (denied
under `dontAsk`); not modelled.

-/

namespace SimpleGate

/-- Why there is no Jev answer, or why the hook skipped Jev. Each one is shown
as a warning. -/
inductive Down where
  | timeout | http | network | malformed | envReference
  | badInput | pythonMissing | internalError
  deriving DecidableEq, Repr

/-- Jev is configured but did not give a usable answer. -/
def Down.jevUnavailable : Down → Bool
  | .timeout | .http | .network | .malformed => true
  | _ => false

/-- The plugin itself failed (D12). -/
def Down.pluginFault : Down → Bool
  | .badInput | .pythonMissing | .internalError => true
  | _ => false

/-- Reasons that come with an `ask`: everything except the `.env` skip. -/
def Down.asks : Down → Bool
  | .envReference => false
  | _ => true

theorem Down.asks_iff (w : Down) :
    w.asks = true ↔ (w.jevUnavailable = true ∨ w.pluginFault = true) := by
  cases w <;> simp [Down.asks, Down.jevUnavailable, Down.pluginFault]

/-- What the Jev request returned. `risk` is the per-mille sum of the prompt's
ask choices (e.g. `cloud_or_data_change + destructive`). -/
inductive JevResult where
  | down (why : Down)
  | answer (risk : Nat)
  deriving DecidableEq, Repr

/-- What is wrong with a misconfigured setting; shown once at session start. -/
inductive SessionIssue where
  | noKey | badUrl | badPrompt | badValue | pythonMissing
  deriving DecidableEq, Repr

/-- The Jev configuration: usable, or misconfigured for a reason (D2: there is
no "off" state; `GATE_ENABLED=0` removes the hook altogether). -/
inductive Setting where
  | misconfigured (why : SessionIssue)
  | on
  deriving DecidableEq, Repr

/-- `GATE_JEV_ESCALATE_THRESHOLD` in per mille. -/
structure Config where
  escalate : Nat := 500
  deriving Repr

/-- The hook protocol's decisions. `allow` and `deny` exist so that S1 says
something: the gate could print them, it just never does. -/
inductive HookDecision where
  | none | ask | allow | deny
  deriving DecidableEq, Repr

/-- What the hook prints: a decision and an optional warning (`systemMessage`). -/
structure HookOut where
  decision : HookDecision
  warning : Option Down
  deriving DecidableEq, Repr

/-- A Bash command or MCP call (its text as sent to Jev), hook stdin that could
not be read, a missing interpreter (`gate.sh`) or an exception in the engine. -/
inductive Call where
  | tool (payload : String)
  | badInput
  | pythonMissing
  | crash
  deriving DecidableEq, Repr

/-- The redactor and the `.env` detector, left abstract. -/
structure Env where
  redact : String → String
  mentionsEnv : String → Bool

/-- Jev's answer → hook output. A risky answer asks; a safe answer and the
uncertain band give no decision. Unavailable Jev and plugin faults ask with a
warning (D11, D12); only the `.env` skip stays silent. -/
def judge (c : Config) : JevResult → HookOut
  | .down why => if why.asks then ⟨.ask, some why⟩ else ⟨.none, some why⟩
  | .answer risk => if c.escalate < risk then ⟨.ask, none⟩ else ⟨.none, none⟩

/-- The whole hook. `q` is the Jev request. -/
def hook (e : Env) (c : Config) (s : Setting) (q : String → JevResult) : Call → HookOut
  | .badInput => ⟨.ask, some .badInput⟩
  | .pythonMissing => ⟨.ask, some .pythonMissing⟩
  | .crash => ⟨.ask, some .internalError⟩
  | .tool p =>
    match s with
    | .misconfigured _ => ⟨.none, none⟩
    | .on =>
      if e.mentionsEnv p = true then ⟨.none, some .envReference⟩
      else judge c (q (e.redact p))

/-- The `SessionStart` hook: warn once about a misconfigured setting. -/
def sessionStart : Setting → Option SessionIssue
  | .misconfigured w => some w
  | .on => none

/-! ## Claude Code's side -/

/-- Claude Code's own permission for a call (settings rules + mode). -/
inductive Perm where
  | allow | ask | deny
  deriving DecidableEq, Repr

def rank : Perm → Nat
  | .allow => 0
  | .ask => 1
  | .deny => 2

/-- Final permission: hook decision on top of Claude Code's own. -/
def effective : HookDecision → Perm → Perm
  | .none, p => p
  | .ask, .deny => .deny
  | .ask, _ => .ask
  | .allow, .deny => .deny
  | .allow, _ => .allow
  | .deny, _ => .deny

/-! ## Lemmas about `judge` -/

theorem judge_not_allow (c : Config) (r : JevResult) :
    (judge c r).decision ≠ .allow ∧ (judge c r).decision ≠ .deny := by
  cases r with
  | down w => cases w <;> simp [judge, Down.asks]
  | answer k =>
    by_cases h : c.escalate < k <;> simp [judge, h]

theorem judge_ask (c : Config) (r : JevResult) :
    (judge c r).decision = .ask ↔
      (∃ k, r = .answer k ∧ c.escalate < k) ∨ ∃ w, r = .down w ∧ w.asks = true := by
  cases r with
  | down w => cases w <;> simp [judge, Down.asks]
  | answer k =>
    by_cases h : c.escalate < k <;> simp [judge, h]

/-- A warning comes with an `ask` exactly when its reason asks. -/
theorem judge_warning (c : Config) (r : JevResult) (w : Down)
    (h : (judge c r).warning = some w) :
    ((judge c r).decision = .ask ↔ w.asks = true) ∧
    ((judge c r).decision = .none ∨ (judge c r).decision = .ask) := by
  cases r with
  | down v =>
    cases v <;> simp [judge, Down.asks] at h <;> subst h <;> simp [judge, Down.asks]
  | answer k =>
    by_cases hk : c.escalate < k <;> simp [judge, hk] at h

/-! ## Theses -/

/-- S1 -/
theorem never_allows (e : Env) (c : Config) (s : Setting) (q : String → JevResult)
    (call : Call) :
    (hook e c s q call).decision ≠ .allow ∧ (hook e c s q call).decision ≠ .deny := by
  cases call with
  | badInput => simp [hook]
  | pythonMissing => simp [hook]
  | crash => simp [hook]
  | tool p =>
    cases s with
    | misconfigured w => simp [hook]
    | on =>
      by_cases hm : e.mentionsEnv p = true
      · simp [hook, hm]
      · simp only [hook, hm]
        exact judge_not_allow c _

/-- S2: the only ways to get a prompt from the plugin. -/
theorem asks_iff (e : Env) (c : Config) (s : Setting) (q : String → JevResult)
    (call : Call) :
    (hook e c s q call).decision = .ask ↔
      call = .badInput ∨ call = .pythonMissing ∨ call = .crash ∨
      ∃ p, call = .tool p ∧ s = .on ∧ e.mentionsEnv p = false ∧
        ((∃ r, q (e.redact p) = .answer r ∧ c.escalate < r) ∨
         ∃ w, q (e.redact p) = .down w ∧ w.asks = true) := by
  cases call with
  | badInput => simp [hook]
  | pythonMissing => simp [hook]
  | crash => simp [hook]
  | tool p =>
    cases s with
    | misconfigured w => simp [hook]
    | on =>
      by_cases hm : e.mentionsEnv p = true
      · simp [hook, hm]
      · have hf : e.mentionsEnv p = false := by simpa using hm
        simp [hook, hf, judge_ask]

/-- S3 -/
theorem warning_decision (e : Env) (c : Config) (s : Setting)
    (q : String → JevResult) (call : Call) (w : Down)
    (h : (hook e c s q call).warning = some w) :
    ((hook e c s q call).decision = .ask ↔
        (w.jevUnavailable = true ∨ w.pluginFault = true)) ∧
    ((hook e c s q call).decision = .none ∨ (hook e c s q call).decision = .ask) := by
  rw [← Down.asks_iff]
  cases call with
  | badInput => simp [hook] at h; subst h; simp [hook, Down.asks]
  | pythonMissing => simp [hook] at h; subst h; simp [hook, Down.asks]
  | crash => simp [hook] at h; subst h; simp [hook, Down.asks]
  | tool p =>
    cases s with
    | misconfigured v => simp [hook] at h
    | on =>
      by_cases hm : e.mentionsEnv p = true
      · simp [hook, hm] at h; subst h; simp [hook, hm, Down.asks]
      · simp only [hook, hm] at h ⊢
        exact judge_warning c _ w h

/-- S4: the `.env` skip and a misconfigured setting leave the call exactly to
Claude Code. -/
theorem fallback_is_claude (e : Env) (c : Config) (s : Setting)
    (q : String → JevResult) (call : Call) (claude : Perm)
    (h : (hook e c s q call).warning = some .envReference ∨
         ∃ w p, s = .misconfigured w ∧ call = .tool p) :
    effective (hook e c s q call).decision claude = claude := by
  have hn : (hook e c s q call).decision = .none := by
    rcases h with h | ⟨w, p, rfl, rfl⟩
    · have := (warning_decision e c s q call _ h).1
      cases hd : (hook e c s q call).decision with
      | none => rfl
      | ask => simp_all [Down.jevUnavailable, Down.pluginFault]
      | allow => exact absurd hd (never_allows e c s q call).1
      | deny => exact absurd hd (never_allows e c s q call).2
    · simp [hook]
  rw [hn]
  cases claude <;> rfl

theorem effective_monotone (d : HookDecision) (claude : Perm) (hd : d ≠ .allow) :
    rank claude ≤ rank (effective d claude) := by
  cases d <;> cases claude <;> simp_all [effective, rank]

/-- S5: the plugin only ever adds prompts. -/
theorem never_weaker (e : Env) (c : Config) (s : Setting) (q : String → JevResult)
    (call : Call) (claude : Perm) :
    rank claude ≤ rank (effective (hook e c s q call).decision claude) :=
  effective_monotone _ claude (never_allows e c s q call).1

/-- S6 -/
theorem threshold_monotone (e : Env) (c₁ c₂ : Config) (s : Setting)
    (q : String → JevResult) (call : Call) (h : c₂.escalate ≤ c₁.escalate) :
    (hook e c₁ s q call).decision = .ask → (hook e c₂ s q call).decision = .ask := by
  rw [asks_iff, asks_iff]
  rintro (h1 | h1 | h1 | ⟨p, hc, hs, he, ⟨r, hq, hr⟩ | hw⟩)
  · exact Or.inl h1
  · exact Or.inr (Or.inl h1)
  · exact Or.inr (Or.inr (Or.inl h1))
  · exact Or.inr (Or.inr (Or.inr ⟨p, hc, hs, he, Or.inl ⟨r, hq, Nat.lt_of_le_of_lt h hr⟩⟩))
  · exact Or.inr (Or.inr (Or.inr ⟨p, hc, hs, he, Or.inr hw⟩))

/-- S7: two Jev endpoints that agree on every redacted, `.env`-free payload give
the same hook output, so nothing else can influence (or reach) Jev. -/
theorem sees_only_redacted (e : Env) (c : Config) (s : Setting)
    (q₁ q₂ : String → JevResult) (call : Call)
    (h : ∀ p, e.mentionsEnv p = false → q₁ (e.redact p) = q₂ (e.redact p)) :
    hook e c s q₁ call = hook e c s q₂ call := by
  cases call with
  | badInput => rfl
  | pythonMissing => rfl
  | crash => rfl
  | tool p =>
    cases s with
    | misconfigured w => rfl
    | on =>
      by_cases hm : e.mentionsEnv p = true
      · simp [hook, hm]
      · have hf : e.mentionsEnv p = false := by simpa using hm
        simp only [hook, hm]
        rw [h p hf]

/-- S8 -/
theorem answer_no_warning (e : Env) (c : Config) (q : String → JevResult) (p : String)
    (k : Nat) (hm : e.mentionsEnv p = false) (hq : q (e.redact p) = .answer k) :
    (hook e c .on q (.tool p)).warning = none := by
  by_cases h : c.escalate < k <;> simp [hook, hm, hq, judge, h]

/-- S9: a misconfigured setting is reported once, by `sessionStart`, and never
by the hook. -/
theorem misconfigured_warns_once (s : Setting) (w : SessionIssue) :
    sessionStart s = some w ↔ s = .misconfigured w := by
  cases s <;> simp [sessionStart]

theorem misconfigured_hook_silent (e : Env) (c : Config) (w : SessionIssue)
    (q : String → JevResult) (p : String) :
    hook e c (.misconfigured w) q (.tool p) = ⟨.none, none⟩ := rfl

/-- S10: an unreachable Jev never silently lets a sent call through. -/
theorem unavailable_asks (e : Env) (c : Config) (q : String → JevResult) (p : String)
    (w : Down) (hm : e.mentionsEnv p = false) (hq : q (e.redact p) = .down w)
    (hw : w.jevUnavailable = true) :
    hook e c .on q (.tool p) = ⟨.ask, some w⟩ := by
  have ha : w.asks = true := (Down.asks_iff w).2 (Or.inl hw)
  simp [hook, hm, hq, judge, ha]

/-! ## Examples (default `escalate = 500`), one per row of the conformance table.
Each `example` is preceded by a `-- example: NAME` line; `tests/test_lean_conformance.py` fails when a
name has no test, so a new example needs a test of the same name -/

private def env₀ : Env := ⟨id, fun s => s == ".env"⟩
private def q₀ (r : JevResult) : String → JevResult := fun _ => r

-- Jev answered
-- example: answer_0
example : hook env₀ {} .on (q₀ (.answer 0)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: answer_500
example : hook env₀ {} .on (q₀ (.answer 500)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: answer_501
example : hook env₀ {} .on (q₀ (.answer 501)) (.tool "ls") = ⟨.ask, none⟩ := by decide
-- Jev unavailable: warning and ask
-- example: down_timeout
example : hook env₀ {} .on (q₀ (.down .timeout)) (.tool "ls") = ⟨.ask, some .timeout⟩ := by decide
-- example: down_http
example : hook env₀ {} .on (q₀ (.down .http)) (.tool "ls") = ⟨.ask, some .http⟩ := by decide
-- example: down_network
example : hook env₀ {} .on (q₀ (.down .network)) (.tool "ls") = ⟨.ask, some .network⟩ := by decide
-- example: down_malformed
example : hook env₀ {} .on (q₀ (.down .malformed)) (.tool "ls") = ⟨.ask, some .malformed⟩ := by decide
-- misconfigured: silent per call
-- example: misconfigured_no_key_silent
example : hook env₀ {} (.misconfigured .noKey) (q₀ (.answer 900)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: session_start_misconfigured_no_key
example : sessionStart (.misconfigured .noKey) = some .noKey := by decide
-- example: session_start_on
example : sessionStart .on = none := by decide
-- `.env`: not sent, warning, no decision
-- example: env_mention_not_sent
example : hook env₀ {} .on (q₀ (.answer 900)) (.tool ".env") = ⟨.none, some .envReference⟩ := by decide
-- plugin faults: ask with a warning, with or without a key
-- example: bad_input_on
example : hook env₀ {} .on (q₀ (.answer 0)) .badInput = ⟨.ask, some .badInput⟩ := by decide
-- example: bad_input_misconfigured
example : hook env₀ {} (.misconfigured .noKey) (q₀ (.answer 0)) .badInput = ⟨.ask, some .badInput⟩ := by decide
-- example: python_missing
example : hook env₀ {} .on (q₀ (.answer 0)) .pythonMissing = ⟨.ask, some .pythonMissing⟩ := by decide
-- example: crash
example : hook env₀ {} .on (q₀ (.answer 0)) .crash = ⟨.ask, some .internalError⟩ := by decide
-- Claude Code's side
-- example: effective_ask_allow
example : effective .ask .allow = .ask := by decide
-- example: effective_ask_deny
example : effective .ask .deny = .deny := by decide
-- example: effective_none_allow
example : effective .none .allow = .allow := by decide

end SimpleGate
