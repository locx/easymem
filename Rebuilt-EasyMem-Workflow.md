# EasyMem Autonomous Workflow

A staged pipeline that audits and **cleans up** EasyMem behind **two human gates — plan approval and
final-diff approval**. Everything between the gates runs autonomously; a diff reaches the human only
after it passes implementation, the verify gate, the smoke, a self-audit, an adversarial review ⇄
correction loop, and a fresh-eyes integration review.

**Scope — cleanup and hardening, not feature work.** Dead code, duplication, excess complexity, and
tech-debt are the deliverable: removed, consolidated, and simplified **strictly behavior-preserving**,
never traded for new behavior. Clean up as much as the gates and invariants allow.

**Repo:** a local-first knowledge-graph memory plugin for Claude Code — Python 3.10+
(`semantic_server/`, `hooks/`, `easymem-cli.py`, `maintenance.py`) plus shell (install/setup/hooks),
with optional `model2vec` int8 vector retrieval over an append-only `.easymem/graph.jsonl` store.

## Invariants (load-bearing — absolute in every phase)

1. **Local-first.** Every byte stays under `.easymem/`; nothing readable or committable leaks elsewhere.
2. **Zero runtime egress.** EasyMem has no online sources, so *any* non-loopback socket from the code
   under test is a regression. The only allowed network touch is the one-time `model2vec` fetch in
   `install.sh` / `setup-project.sh`; it must never reach runtime.
3. **Scrub before store.** Secrets are redacted **before** anything reaches `graph.jsonl`.
4. **Durability holds.** Locks, atomic writes, crash recovery, and bounded loops are never weakened.
5. **Contract is stable.** `graph.jsonl`'s record shape and the tool schema don't drift.

**Stop and surface immediately on only two unplanned conditions:** a change weakens a privacy or
durability invariant, or a circuit breaker trips (both defined in *Guardrails*).

---

## Preconditions

- Run from repo root, on `main`, with a **clean tree**. Execute phases **strictly in order**.
- **Provision tooling or fail fast.** `./install.sh --dev` creates the project-local `.venv`
  (`ruff` + `pytest`); add `shellcheck`. **Run every gate from `.venv` (Python 3.10)** — a missing gate
  tool is a hard stop, never a silent skip. Run `easymem doctor` to validate Python, model cache, store.
- **Baseline (orchestrator records once, in the worktree, before batch 1).** The worktree is pinned at
  `BASE` (= `main`'s HEAD at gate time), so its baseline **is** the `main`-equivalent — there is no
  separate "run on main" step. `python3 -m pytest` from `.venv` must be green; record its pass/skip
  counts and tag them with `BASE` (the baseline-tag SHA must equal `BASE`). **The recorded counts are
  authoritative, never a hardcoded literal** (they drift as batches add tests). Vector tests *skip*
  (never fail) when the `model2vec` cache (`~/.cache/huggingface/`) is absent. A non-green baseline is a
  STOP; thereafter a pass-count below baseline or a skip-count above it is a regression.
- `<DATE>` = today (ISO `YYYY-MM-DD`). Each run owns `docs/audit/audit-<DATE>/`; a same-day re-run takes
  the next suffix (`-1`, `-2`, …) and never overwrites a prior run.

---

## Pipeline

```text
1     Repository Intelligence    (Explore + Plan)                       ── autonomous
2     Production Audit           → analysis_report / plan / tasklist    ── autonomous
   └─ 2b  10× Performance pass   (folds into the same docs)             ── autonomous
3     Tech-Lead Prioritization   (reorder + dedupe + batch)             ── autonomous
3.5   Plan Red-Team              (independent agent tries to break it)  ── autonomous
──────────────────  ⛔ PLAN APPROVAL — human stop #1  ──────────────────
             ── Phases 4–9 run in an isolated git worktree ──
4     Autonomous Execution       (batches; pytest + ruff; smoke for store/AI/hook changes)
5     Self-Audit & Simplify      (author cleans own diff, re-verifies)
6 ⇄ 7 Adversarial Review ⇄ Correction   (loop until two clean passes)
8     Final Integration Review   (fresh-eyes, looped until regression-free)
9     Artifact Cleanup           (prune scratch; audit docs stay)
──────────────────  ⛔ FINAL DIFF APPROVAL — human stop #2  ──────────────────
            ── on approval: squash-merge to main (one commit) + teardown ──
```

---

## Worktree & artifacts (Phases 4–9)

- **Pin the base** at creation: `BASE=$(git rev-parse HEAD)`. Diff every later phase against that fixed
  SHA so unrelated `main` movement can't pollute review.
- **Isolation:** every Phase 4–9 subagent `cd`s into the worktree so it imports the worktree's
  `semantic_server/`, not `main`'s. No symlinks needed — tests build their own stores under `tmp_path`.
- **Commits:** subagents never commit (the briefing forbids self-authorized git). The orchestrator
  commits each green batch after verifying the returned test output. Plan-gate approval authorizes these
  worktree-branch checkpoints; the final squash to `main` needs a separate explicit imperative (§1.1).
- **Artifacts** — `analysis_report.md`, `implementation_plan.md`, `tasklist.md`, `progress.md` — live in
  **one** home under `docs/audit/audit-<DATE>[-N]/`: either committed on the worktree branch or untracked
  in the main checkout, never split. `main` carries no audit folder. (`docs/` is outside `ruff` scope.)
- **Resume:** persist run state (phase, batch, open findings) to `progress.md` each checkpoint. On
  resume, read it, re-verify the last checkpoint is green, continue at the next open batch.

---

## Orchestration

Phases run in order. Within a phase, work fans out; the orchestrator then barrier-joins, dedupes, and is
the **single writer** of every shared artifact. Parallel agents **return** findings — they never write
the same file concurrently.

- **Phase 1** fans `Explore` out by subsystem; `Plan` synthesizes one report. Explore returns **free
  text, not a forced schema** (it emits StructuredOutput unreliably); synthesis tolerates a missing slice.
- **Phase 2** runs lenses A–C as parallel auditors; one synthesis step writes the three docs.
- **Phase 6** runs the reviewer panel in parallel, then verifies each finding with 2–3 parallel skeptics.
  **Phase 8** is a separate, independent fresh-eyes reviewer over the same diff.
- **Batches run sequentially** — they share files and the smoke needs a stable before-state. Run the full
  suite every batch. Sole exception: split provably file-disjoint batches across separate worktrees only
  when a measured wall-clock win justifies the extra merge + re-test.

For long runs, encode this as a `Workflow` script (`pipeline` / `parallel` / loop-until-dry / verify)
for a token budget and journaled resume.

---

## Guardrails (paste into every subagent dispatch)

Paste these alongside the `<HARD-RULES v1>` briefing from `~/.claude/CLAUDE.md` §6 — `briefing-gate.sh`
blocks any dispatch missing it. Per-phase prompts assume these and don't restate them.

1. **Zero egress.** Any outbound socket — telemetry, HTTP, a cloud SDK, a network-capable dep, or
   model-download in a runtime path — is a regression (see Invariant 2).
2. **Never weaken the scrubber.** `hooks/capture_tool_context.py` and `semantic_server/text.py` redact
   secrets (`AKIA…`, `ghp_…`, `sk-…`, `Bearer …`, `PASSWORD=`, `--token`, URL userinfo, `PRIVATE KEY`)
   **before** anything reaches `graph.jsonl`. Loosening a pattern, reordering capture-before-scrub, or
   bypassing it is a privacy regression. Any **identity-field** scrub (entity name, relation endpoint,
   decision title) must be **idempotent and collision-resistant**. `test_secret_scrub.py` is the floor.
3. **`graph.jsonl` is the single source of truth.** Append-only JSONL; derived artifacts
   (`tfidf_index.json`, `vec_index.npz`, `recall_counts.json`, caches) rebuild from it and are disposable.
   Records carry a stable shape (`name`, `entityType`, `observations[]`, `_created`, `_updated`,
   `_branch`, `_source`, `_neighbors`); the 13-tool contract lives in `tools_schema.json`. Never drop or
   rename a field or tool; never rewrite the store outside the lock.
4. **Never weaken a durability control:** `GraphLock` (fcntl, 5s timeout, backoff); merge-pending
   **O_EXCL rotation + fsync** with `.processing` crash recovery; **mtime guards** on incremental reads;
   **recall-lock + fsync**; bounded loops (`traverse` ≤10k, `workflows` ≤200k combos, recall LRU ≤10k,
   cache byte-budget). Removing, loosening, or unbounding any of these is a regression even if tests pass.
5. **Respect the retrieval contract.** TF-IDF is always available; `model2vec` int8 vectors are optional
   and fuse via RRF (floor `0.05`). The system **degrades silently to TF-IDF** when the model is absent —
   vectors are never a hard dependency.
6. **Tag every `NEEDS-HUMAN`.** `NEEDS-HUMAN-BLOCK` — do **not** implement; surface at the plan gate
   (adds egress / weakens the scrubber or a lock / changes the schema or tool contract / adds a
   network-capable dep). `NEEDS-HUMAN-VERIFY` — implement with care, then require sign-off **before merge**
   with a before/after equivalence table. Bare `NEEDS-HUMAN` defaults to BLOCK. *Strengthening* a control
   is VERIFY at most, never BLOCK.
7. **Languages.** Python 3.10+ with type hints for the engine; POSIX sh/bash for hooks and install
   (keep `set -euo pipefail`, stay `shellcheck`-clean).
8. **Verification is real.** No "passing" or "fixed" without this-run output from `python3 -m pytest`
   (the full suite, never a subset) **and** `ruff check` (`select=["E9","F"]`, line 100, excludes
   `bench/docs/.easymem`), via a log-absorbing subagent that returns pass/fail + failing tests only.
   Store / retrieval / capture / hook changes also require the Phase 4 smoke.

**Cleanup-run scope (single source of truth for removals).** This run relaxes the usual
"flag-don't-remove" default in favor of **behavior-preserving** cleanup — removing dead code,
consolidating duplication, and simplifying complexity are the goal, not side effects. Invariants 1–5 and
Guardrails 1–6 remain absolute. Every removal requires the **full suite green and grep-proof of zero
callers**. Removing a public/exported symbol, a hook/CLI entry point, the deprecated MCP surface, or
anything reachable by dynamic dispatch you can't grep is **NEEDS-HUMAN**, not autonomous. Deleting a whole
file emits `rm` for the human (§1.2). The deprecated `python3 -m semantic_server` MCP entry point is
**retained for back-compat — do not propose its removal**; treat it as live, not dead code.

**Circuit breaker (per batch and per review round).** An *attempt* is one fix→reverify cycle; the counter
resets on a fully-green round. **Non-converging** = the open-finding (or failing-test) count didn't
strictly decrease, or the same `file:line + finding` recurs — surfacing *new* findings and fixing them is
progress, not a strike. A round that surfaces **zero new** findings is convergence — it advances the
two-clean-pass terminator (Phases 6/8), never counts as a strike. After **3 non-converging attempts**,
stop and surface. Never self-discard a
checkpoint (`git reset --hard` is §1.2-destructive — emit it for the human). Bounds Phases 4, 6, 8.

---

## Phase 1 — Repository Intelligence

**Dispatch** `Explore` (medium→very thorough) fanned out by subsystem; `Plan` synthesizes. Read-only.

```text
Repo-intelligence pass on EasyMem (local-first knowledge-graph memory plugin). DO NOT edit code.
1 — Structure & flow: layout under semantic_server/ (server.py stdio loop, protocol.py dispatch,
  graph.py + io_utils.py store I/O, tools.py writes, search.py + vector.py + text.py retrieval, recall.py,
  traverse.py, code_index.py, maintenance_utils.py, cache.py, slots.py, workflows.py, tools_schema.json);
  the import graph; entry paths (deprecated `python3 -m semantic_server`, the easymem CLI, hooks in
  hooks/hooks.json). Trace the write path (hook → graph.jsonl.pending → merge_pending → graph.jsonl) and
  the read path (load_graph_entities → TF-IDF + vector → RRF fusion → recall boost).
2 — Architecture: GraphLock boundary; merge-pending O_EXCL rotation + fsync + crash recovery;
  mtime-guarded incremental reads; recall-lock + fsync; vector staleness (.vec_index.meta); cache
  byte-budget eviction; coupling/layering.
3 — Concerns (observe, don't fix): PRIVACY FIRST (any outbound socket; scrub coverage + ordering;
  anything readable/committable under .easymem/); then DURABILITY (lock gaps, non-atomic writes, missing
  fsync, unrotated pending); correctness; perf (expensive scans, redundant rebuilds, O(n²)); memory
  (large candidate/embedding sets, cache growth); dead code; dup logic; circular imports; loop bounds.
4 — Risk hotspots: most fragile modules, highest-debt areas, scaling limits as the graph grows.
OUTPUT → docs/audit/audit-<DATE>/analysis_report.md: 1 architecture · 2 module/store-flow map ·
  3 subsystem notes · 4 privacy + scrub audit · 5 durability/lock audit · 6 risk hotspots · 7 tech-debt ·
  8 baseline (orchestrator fills — see below).
```

**Baseline — the orchestrator runs this directly, once, in the worktree before batch 1 (not a subagent):**
`python3 -m pytest` from `.venv` (record pass/skip counts; any red ⇒ STOP) plus 2–3 timings of the
build-store → rebuild-index → search → recall path. Use an **in-process harness over a scaled store**
(thousands of entities) — CLI-invocation timing is startup-dominated and won't catch a regression. Tag
the record with `git rev-parse HEAD`.

## Phase 2 — Production Audit (+ 2b: 10× Performance pass)

**Dispatch** `feature-dev:code-architect` (or `Plan`). Consumes the Phase 1 report. Read-only. Lenses
A–C fan out and return findings; one synthesis step writes the docs.

```text
From analysis_report.md, do a production-readiness audit. Python 3.10+, prefer stdlib.
A — Privacy, durability & correctness (top priority): any outbound socket? scrub coverage/ordering gaps
  (a raw payload reaching graph.jsonl)? identity-field scrub idempotent + collision-resistant? lock gaps
  / non-atomic writes / missing fsync / unrotated pending / mtime-guard holes? schema or
  tools_schema.json drift? bugs, missing validation, resource leaks; loop bounds hold.
B — Performance & memory (the 10× pass): hidden O(n²) over entities/relations; I/O (redundant rebuilds,
  full reloads where incremental suffices, repeated JSONL parses); memory (large candidate/embedding
  sets, int8 vector lifecycle, cache byte-budget). Every perf task names its measurement (before/after
  vs the Phase 1 baseline) and target — unmeasurable ⇒ not a perf task. "10×" is the ambition, not a
  license to skip numbers and not a literal acceptance threshold.
C — Architecture & cleanup (the PRIMARY goal): separation of concerns, circular imports, back-compat
  shim consolidation, dead code, duplicate utilities, CC>10 / deep nesting. REMOVE dead code and
  CONSOLIDATE duplication rather than merely flag — behavior-preserving, per the cleanup-run scope.
OUTPUT — edit IN PLACE: analysis_report.md · implementation_plan.md (phased A/B/C) · tasklist.md (one
  task per fix). Each task: title · description · rationale · affected files · steps · expected outcome ·
  priority · complexity · estimate · dependencies · status `[ ]` · privacy/durability note (adds egress /
  touches the scrubber / changes a lock or the schema? how is the invariant kept?).
Tests required for any code change; shell stays shellcheck-clean.
```

## Phase 3 — Tech-Lead Prioritization

**Dispatch** `Plan`. No code.

```text
Reorder docs/audit/audit-<DATE>/{analysis_report,implementation_plan,tasklist}.md for safe, high-leverage
execution. Order: privacy/durability/correctness first, then reliability, perf, memory, debt. Add missing
high-impact tasks; merge redundant; split oversized; drop cosmetic tasks with no evidence. Isolate into
its own batch any task touching graph.py / io_utils.py / tools_schema.json, the GraphLock/merge-pending
path, or capture_tool_context.py. Any retrieval/embedding or hook batch carries the smoke as acceptance.
Flag egress / weakened scrubber-or-lock / schema change → NEEDS-HUMAN.
OUTPUT: reordered plan + cleaned tasklist, batched into dependency-ordered groups of 2–3.
```

## Phase 3.5 — Plan Red-Team

**Dispatch** one independent `Plan` agent told to *break* the plan, not improve it. Read-only. Fold
confirmed findings back into the plan/tasklist before the gate; never silently dismiss a privacy/
durability finding. This feeds the gate — it adds no second human stop.

```text
Adversarially review implementation_plan.md + tasklist.md. Default to "unsafe or wrong" and prove it:
- Privacy honesty: a task adding a network call or weakening/reordering the scrubber, or an identity
  scrub that isn't idempotent + collision-resistant, not marked NEEDS-HUMAN?
- Durability: touches a lock, the merge rotation, an fsync, or an mtime guard without proving the store
  can't corrupt or lose data on crash/contention?
- Schema drift: record fields or tools_schema.json edited without a careful migration; a field/tool
  silently renamed/dropped?
- Control regression: loosens GraphLock, the scrubber, recall-lock, or a bounded loop?
- Removal safety: deletes an import path, public/exported symbol, entry point, or the MCP surface
  without grep-proof of zero callers, or without the NEEDS-HUMAN tag the cleanup-run scope requires?
- Dependency: adds/modifies a pip dep that could reach the network — justified?
- False "behavior-preserving" claims; steps that don't achieve their outcome.
- Retrieval/hook tasks with no smoke, or whose proving test isn't in the same batch (deferred = false
  green). Over-scoped batches (>3 real-change tasks); mis-ordered dependencies. Non-problems (drop).
OUTPUT: per-task verdict (sound / revise / drop / NEEDS-HUMAN) + corrected batch order.
```

---

## ⛔ PLAN APPROVAL GATE

Present, then **STOP** until explicitly approved:

- the batched task list (title · priority · complexity · estimate · privacy/durability note);
- a scope roll-up (task + per-batch counts; effort sum + subtotals);
- the execution order, flagging batches that touch the store/schema, the lock/merge path, or the scrubber;
- any `NEEDS-HUMAN` items.

Use the Major-task schema `| file | change | why | verify |`.

---

## Phase 4 — Autonomous Execution

Execute batch by batch in the worktree. **Ask no further questions** unless a task hits a
NEEDS-HUMAN-BLOCK trigger (Guardrail 6: egress / scrubber / lock / schema-or-tool-contract / network
dep) — then do **not** implement it: tag it NEEDS-HUMAN-BLOCK, skip it, continue the other batches, and
surface it at the final gate. **Per batch:**

1. **Implement** the 2–3 tasks: minimal diff, match style, edit over create, comments WHY-only ≤2 lines,
   no tracker IDs. Never weaken a test to pass — fix the code or the input.
2. **Verify** via a log-absorbing subagent: `python3 -m pytest` (full suite) **and** `ruff check`;
   `shellcheck` any changed shell. Versus the recorded baseline, pass-count must not fall and skip-count
   must not rise — skip→fail, pass→skip (a quietly disabled test), and a dropped test are all regressions.
3. **Smoke** — required if the batch touched the store, retrieval/embedding, capture, or a hook. Run
   `scripts/smoke.sh` — a first-class, reviewed harness kept in the repo (so the gate itself is testable;
   if absent, create it verbatim from **Appendix A**, never a hand-rolled substitute; exempt from Phase 9
   pruning). Against a throwaway `.easymem` it builds the store, races two named writers, rebuilds the
   index, and asserts these **hard gates**:
   - **Zero egress** — a descendant-scoped poller finds no non-loopback ESTABLISHED socket.
   - **Scrub held** — driving the hook `--mint-error` path with a unique secret sentinel, the sentinel
     reaches no store file (`graph.jsonl`, `.pending`, `.processing`).
   - **Durability** — both racing writes present (no lost write); every JSONL line parses.
   - **Retrieval** — the live index returns a seeded entity for the query.
   - **Vector coverage** — build the vector index *after* the egress window (a cold model fetch must not
     false-trip the egress gate); a fusion verdict requires `vec_index.npz`, **and the seeded target
     entity is present in the fused result and not ranked below its TF-IDF-only position** (fusion didn't
     demote it — this is "no fusion regression"; it's a self-contained check on the throwaway store, not a
     comparison to the Phase-1 baseline graph). Lexical-only ⇒ a vector change is unverified, task open.

   Choose queries that drive the *specific* code changed (fusion → a query where vector and TF-IDF
   disagree; scrubber → an observation carrying a secret; lock → concurrent writers). Land the proving
   test **in this batch**, preferring a CLI/search-entrypoint test over a unit test on internals. The
   smoke covers the CLI plus the capture hook **invoked directly** — not the `semantic_server` MCP
   protocol nor live in-session hook triggering — so a green smoke is not full-path coverage. A secret
   sentinel lives inside the script file, never an inline shell argument (scanners flag `ghp_…` etc.).
4. **If red:** fix forward in the same batch (systematic-debugging, never assertion-weakening); re-run.
   Circuit breaker applies.
5. **On green:** the orchestrator commits the checkpoint (subagents never self-commit). Record tasks,
   files, pytest+ruff summary, smoke result. Flip finished tasks to `[x]` in `tasklist.md` and echo:

   ```text
   Batch <N> of <B> done — <M>/<T> tasks (<P>%), ~<spent>h of ~<total>h est.
   ✅ <id> — <title>   (est <e>h / actual <a>h)
   ⬜ next: <id> — <title>   ·   ⬜ <id> — <title>
   ```

   Flag any task that ran >1.5× its estimate.

Dispatch text: `Implement batch <N> from tasklist.md in the worktree per the Phase 4 procedure (do NOT
commit). Report: tasks, files, pytest+ruff result, smoke result, progress checklist.`

**Mid-run sanity (every ~10–15 tasks):** privacy intact (no socket, no secret in `graph.jsonl`, nothing
staged under `.easymem/`); shims resolve; schema + `tools_schema.json` consistent; no perf/memory
regression in build→rebuild→search→recall. Run `pytest` + `ruff` (+ smoke for store/retrieval/hook
changes); fix red before the next batch.

## Phase 5 — Self-Audit & Simplify

The author's own cleanup over the cumulative branch diff (`git diff $BASE..HEAD`, as in Phases 6 and 8),
before any independent reviewer. It applies fixes, so it carries its own verify gate: re-run `pytest` +
`ruff` (+ smoke if the store/retrieval/hook path was touched); on green the orchestrator
checkpoint-commits.

```text
Audit the branch diff vs base against tasklist.md, then fix and clean in the worktree. Verify: tasks
satisfied; imports resolve, no broken shims, no circular deps; no regressions; privacy intact (no new
socket, no secret in graph.jsonl); durability intact (locks, merge rotation, fsync, mtime guards, bounded
loops); schema + tools_schema.json unchanged unless a reviewed migration; 3.10 compat; comments/docstrings
WHY-only ≤2 lines in every touched file. Then execute lens C's cleanup (pre-existing included) per the
cleanup-run scope: simplify the flagged CC>10 / deeply-nested functions, remove dead code, consolidate
duplicate utilities — behavior-preserving only, every removal grep-proof of zero callers (public-API /
entry-point / MCP removals are NEEDS-HUMAN), NO bug hunting (Phase 6), no new single-use wrappers. A
change to a durability-adjacent function (prune / merge / load paths) is NEEDS-HUMAN-VERIFY
(behavior-preserving, full durability suite green). Minimal diff per change, no gratuitous reformat/rename
of untouched code, diff ≤2× minimal. Don't touch the schema, weaken a control, or add a dependency.
Report files changed, what was removed/consolidated/simplified, risky/new-debt items graded
critical/major/minor, and any NEEDS-HUMAN; do NOT commit.
```

## Phase 6 — Adversarial Review

**Independent** review before presenting anything. Dispatch the panel in parallel, each told to *refute*:

- **`python-code-reviewer`** (or `feature-dev:code-reviewer`) — bugs, logic, conventions, regressions;
  runs the project's `ruff`/`pytest` to ground claims.
- **`feature-dev:code-explorer`** — only if the change spans subsystems; trace that no caller or shim
  consumer broke.
- **`code-simplifier:code-simplifier`** — only complexity newly introduced by Phase 7 fixes.
- **`python-architecture-validator`** — only if a layer boundary (store ↔ search ↔ hook ↔ CLI) or the
  lock/merge seam moved.

```text
Adversarially review git diff $BASE..HEAD (not the working tree). Default to "this is wrong" and prove
it. Priority: (1) does any change add a socket, let a secret reach graph.jsonl, alter the
schema/tools_schema.json unsafely, loosen a control (GraphLock, merge rotation/fsync, mtime guard,
scrubber, recall-lock, a bounded loop), add a network-capable dep, or remove a public symbol/entry
point/MCP surface without grep-proof? (2) correctness regressions / broken shims / circular imports /
lost or corrupt writes; (3) your domain lens. Cite file:line; mark each finding real/uncertain +
severity. Don't propose weakening tests or adding deps.
```

Merge + dedupe, then **verify each finding** with 2–3 parallel skeptics (each told to refute). Accept a
non-privacy/non-durability finding only on majority; privacy/secret/schema/lock findings are always
carried. **When a finding is one instance of a pattern, close the whole class** — grep every other site
and fix them together; the next review confirms the class is closed.

**Loop until dry.** After Phase 7 fixes a round, re-run. Keep a *seen* set keyed by `file:line + finding`;
each round drop anything already seen and add the newly surfaced. Dedup against *seen*, not against what
was accepted, so a rejected or partly-fixed finding can't re-trip the loop. Repeat until **two consecutive
passes surface nothing new**. Circuit breaker applies.

## Phase 7 — Auto-Correction

```text
Using the merged findings from the review phase that invoked this correction (5, 6, or 8), correct the
implementation. Minimal fixes only, no unrelated refactors. If a finding is one instance of a pattern,
fix ALL instances in this pass. Re-run pytest + ruff (+ smoke if the store/retrieval/hook path was
touched) until green. Restate which findings are resolved and which are NEEDS-HUMAN. Do NOT commit; do
NOT run git stash/checkout/reset (copy a file to /tmp if you need a pre-fix comparison).
```

---

## Phase 8 — Final Integration Review

A fresh-eyes pass over the integrated diff, carrying none of Phase 6's *seen*-set bias, **independent**
(a run is not clean just because Phase 6 was) and **looped until regression-free**. If you reduce rigor
under a budget (one pass, not the loop), you **must declare it at the final gate** ("review depth:
reduced") — silently collapsing the loop is a process failure.

Default: dispatch an independent reviewer (`python-code-reviewer` / `feature-dev:code-reviewer`) over
`git diff $BASE..HEAD` in the worktree. Do **not** rely on `/code-review high` with no PR arg — it bundles
the *session* branch, which stays on `main` while the work sits in a detached worktree, so it can't see
this delta. Use `/code-review high` only when the session is actually on the audit branch (`ultra` is
cloud, billed, user-triggered only).

Each round: triage correctness/regression first, then reuse/simplification/efficiency; fix every
correctness finding via Phase 7; re-run tests (+ smoke if relevant) to green; then **re-review** — a fix
can introduce a fresh bug. Repeat until **two consecutive rounds surface no correctness/regression
finding**. Circuit breaker applies. Record each round into `docs/audit/audit-<DATE>/`.

## Phase 9 — Artifact Cleanup

Inside the worktree, before the final diff. Prune scratch so the diff is exactly the intended change.

- **Remove:** throwaway smoke stores and temp dirs (`/tmp/em-smoke-*`, `/tmp/em-egress-*`); dead debug
  scratch (commented probes, ad-hoc prints); any accidentally-staged `.easymem/` data, `*.npz`, or
  secret-bearing file (MUST NOT reach a commit, §1.3).
- **Keep:** `docs/audit/audit-<DATE>/{analysis_report,implementation_plan,tasklist}.md` (tasklist all
  `[x]`), the before/after retrieval captures, and the committed `scripts/smoke.sh`.

Verify cleanup broke nothing: re-run `pytest` + `ruff`; confirm `git status` shows no untracked scratch
and no secret/`.easymem` data staged; confirm `git diff $BASE..HEAD -- pyproject.toml requirements.txt`
is empty or every change maps to an approved task. **Emit** any destructive removal (`rm`, etc.) for the
human (§1.2); `/tmp/*` is the only exception.

---

## ⛔ FINAL DIFF APPROVAL

Present, terse:

- what changed and why, batch by batch; the completed tasklist (all `[x]`) with est-vs-actual;
- `pytest` + `ruff` output proving green (this run), plus smoke output if the store/retrieval/hook path
  was touched;
- baseline timings re-run over the `$BASE..HEAD` end-state vs Phase 1 — an unexplained regression is a
  finding;
- empty-egress + scrubber-held confirmation;
- the dependency delta over `$BASE..HEAD` — empty or justified;
- Phase 6 findings + fixes; Phase 8 per-round findings + fixes (closed on two clean rounds);
- Phase 9 cleanup confirmation; any remaining `NEEDS-HUMAN`.

**Merge only after this approval and an explicit imperative** (§1.1). Integrate as a **single squashed
commit**: `git merge --squash <worktree-branch>`, unstage the audit folder (`git restore --staged
docs/audit/audit-<DATE>[-N]/`), then one `git commit` summarizing the whole change (no intermediate-commit
references) — see `superpowers:finishing-a-development-branch`. Tear down the worktree on merge; discard it
on rejection. A squash-merge leaves the branch
unmerged in git's view, so **emit** `git branch -D <worktree-branch>` for the human (§1.2). Nothing
reaches `main` without approval.

---

## Quick reference

| Need | Use |
| --- | --- |
| Map / explore | `Explore` (read-only; fan out: graph·retrieval·hooks·CLI) |
| Plan / architect | `Plan` or `feature-dev:code-architect` |
| Trace a subsystem | `feature-dev:code-explorer` / `python-codebase-explorer` |
| Parallelize | P1 Explores · P2 lenses A–C · P6 panel + verify-per-finding (barrier-join, dedupe) |
| Independent review | `python-code-reviewer` (+ `code-simplifier:code-simplifier`, `python-architecture-validator`) |
| Verify gate | `.venv` `python3 -m pytest` (recorded baseline) **and** `ruff check` (E9+F); `shellcheck` for shell |
| Smoke gate (store·AI·hook) | `scripts/smoke.sh`: throwaway `.easymem` → ZERO egress + scrubber-held (both hard) + no lost/corrupt write + retrieval live + no fusion regression |
| Integration review | fresh-eyes over `$BASE..HEAD`; ends on two consecutive rounds with **zero correctness/regression** findings (no seen-set; reuse/efficiency findings don't block). Differs from P6's "nothing-new" rule |
| Removals (cleanup run) | grep-proof zero callers + suite green; public-API/entry-point/MCP/whole-file → NEEDS-HUMAN / emit `rm` |
| Progress | flip tasklist `[ ]`→`[x]`; echo ✅/⬜ + M/T %, batch N of B, est-vs-actual |

**The one rule above all:** a green build is never worth breaking local-first or durability. Every byte
stays under `.easymem/`, no secret reaches `graph.jsonl`, no socket leaves the machine, the lock and
merge-rotation hold, and `graph.jsonl` round-trips without loss or corruption — fix the code or the input,
never the assertion.

---

## Appendix A — canonical `scripts/smoke.sh`

The authoritative smoke harness. If `scripts/smoke.sh` is absent, create it from this block **verbatim**
(it is the privacy/durability gate; a hand-rolled substitute is an unverified gate). Capture `REPO`
**before** any `cd` into a `/tmp` scratch dir. Swap the `'zebra'` query for one that drives the changed
code (see Phase 4).

```bash
# EasyMem has no server/port; it targets a project's .easymem/, overridable via EASYMEM_DIR. The
# wrapper isn't on PATH in a worktree, so call $REPO/easymem; a non-read-only cmd auto-bootstraps.
REPO=$(git rev-parse --show-toplevel); EM="$REPO/easymem"
SMOKE="/tmp/em-smoke-$$"; export EASYMEM_DIR="$SMOKE/.easymem"
GRAPH="$EASYMEM_DIR/graph.jsonl"; INPUT_JSON="/tmp/em-toolctx-$$.json"
mkdir -p "$SMOKE"; cd "$SMOKE"
trap 'kill "${EGRESS_PID:-}" 2>/dev/null; rm -f "$INPUT_JSON"; cd - >/dev/null' EXIT
SENT='ghp_SMOKESENTINEL0000000000000000'   # unique, so the scrub assert can't hit the [REDACTED] placeholder

# egress poller — watch the easymem process tree under $$ (this shell never opens a socket). Scope to
# descendants only; a global `pgrep -f easymem` would also catch the user's real session (false leak).
EGRESS="/tmp/em-egress-$$.log"; : >"$EGRESS"
( while :; do
    kids=$(pgrep -P "$$" 2>/dev/null)
    for p in $kids $(for k in $kids; do pgrep -P "$k" 2>/dev/null; done); do
      lsof -nP -a -p "$p" -iTCP -sTCP:ESTABLISHED 2>/dev/null | awk 'NR>1{print $9}'
    done | awk -F'->' 'NF>1{print $2}' | grep -Ev '^127\.0\.0\.1|^\[::1\]' >>"$EGRESS"
    sleep 0.2
  done ) &
EGRESS_PID=$!; disown "$EGRESS_PID" 2>/dev/null   # keep its kill out of the gate's stdout/stderr
# one write bootstraps the store; then race two NAMED writers to exercise GraphLock contention.
# observations share a token so the retrieval check below has a real hit to assert on.
"$EM" write '{"entities":[{"name":"WARM","entityType":"smoke","observations":["init zebra"]}]}' >/dev/null
"$EM" write '{"entities":[{"name":"RACE_A","entityType":"smoke","observations":["alpha zebra"]}]}' >/dev/null & wa=$!
"$EM" write '{"entities":[{"name":"RACE_B","entityType":"smoke","observations":["beta zebra"]}]}' >/dev/null & wb=$!
wait "$wa" "$wb"   # only the writers — bare `wait` would also block on the infinite egress poller
# rebuild-now actually builds TF-IDF (bare `rebuild` only marks dirty + defers). Then exercise
# retrieval — swap 'zebra' for a query that drives the CHANGED code (see Phase 4).
"$EM" rebuild --rebuild-now >/dev/null
hits=$("$EM" search 'zebra' 2>/dev/null); "$EM" recall 'zebra' >/dev/null
kill "${EGRESS_PID:-}" 2>/dev/null; EGRESS_PID=

# HARD GATE 1 — zero egress. Poller already reduced to remote endpoints; any line is a leak.
dests=$(sed '/^$/d' "$EGRESS" | sort -u)
[ -z "$dests" ] || { echo "PRIVACY FAIL: outbound socket(s): $dests"; exit 1; }
# HARD GATE 2 — scrub held. Scrubbing lives ONLY in the hook's --mint-error path (the CLI write path
# does NOT scrub), which redacts tool_response.stderr before appending an episode. Drive THAT path
# with $SENT in stderr, then assert $SENT reached no store file.
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"deploy"},"tool_response":{"stderr":"auth failed token='"$SENT"'"}}' > "$INPUT_JSON"
python3 "$REPO/hooks/capture_tool_context.py" --mint-error "$INPUT_JSON" "$GRAPH"
if grep -Rqs "$SENT" "$GRAPH" "$GRAPH.pending" "$EASYMEM_DIR"/*.processing; then
  echo "SCRUB FAIL: sentinel reached a store file"; exit 1
fi
# DURABILITY — both racing writes present (no lost write) and every line parses (no torn JSONL).
for n in RACE_A RACE_B; do
  grep -qs "\"$n\"" "$GRAPH" "$GRAPH.pending" || { echo "DURABILITY FAIL: lost write $n"; exit 1; }
done
python3 -c 'import json,sys; [json.loads(l) for l in open(sys.argv[1]) if l.strip()]' "$GRAPH" \
  || { echo "DURABILITY FAIL: torn/invalid JSONL"; exit 1; }
# RETRIEVAL — the index is live and returns a seeded entity (not a hollow results=0 drive).
echo "$hits" | grep -q '"entity": "RACE_' || { echo "RETRIEVAL FAIL: no hit for the query"; exit 1; }
# COVERAGE — build the vector index so the fusion verdict is real. Maintenance loads the model from
# local cache; run it AFTER the egress window so a cold model fetch can't false-trip GATE 1.
python3 "$REPO/maintenance.py" "$SMOKE" >/dev/null 2>&1
[ -s "$EASYMEM_DIR/vec_index.npz" ] && COV="fusion (vector+TF-IDF)" || COV="lexical-only — VECTOR UNVERIFIED"
echo "smoke OK: no egress, scrubber held, both writes intact, retrieval live; ranking coverage: $COV"
```
