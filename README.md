# easymem 🧠

### 🚀 Persistent Memory · Hybrid Retrieval · Self-Managed

**Persistent memory for Claude Code agents.** Your agent remembers the project's architecture, files, and past decisions across sessions — so it never starts from zero.

**Install in two lines:**

```text
/plugin marketplace add locx/easymem
/plugin install easymem
```

> **LongMemEval-S** · R@5 **0.948** · MRR **0.888** · p50 **116 ms** (n=500). Local-first · zero cloud · zero API keys.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20WSL-lightgrey.svg)](https://github.com/locx/easymem)

---

## Contents

|      # | Section                                                              | Key Focus                                            |
| -----: | -------------------------------------------------------------------- | ---------------------------------------------------- |
|  **1** | [**What It Remembers**](#1-what-it-remembers)                        | Architecture · Files · Decisions · Slots             |
|  **2** | [**Demo**](#2-demo)                                                  | Capture · Recall · Cross-Session Loop                |
|  **3** | [**Getting Started**](#3-getting-started)                            | Install · Auto-Init · Nudge Controls                 |
|  **4** | [**Operations & Usage**](#4-operations--usage)                       | Autopilot · Day-Two Commands                         |
|  **5** | [**Under the Hood**](#5-under-the-hood)                              | Self-Managed · Hybrid Retrieval                      |
|  **6** | [**Compared to the Field**](#6-compared-to-the-field)                | agentmemory · Mem0 · Zep · Letta                     |
|  **7** | [**How It Works**](#7-how-it-works)                                  | Storage · Code Indexer · Hooks                       |
|  **8** | [**Project Info**](#8-project-info)                                  | Troubleshooting · Limits · Platform · License        |

---

## 1. What It Remembers

Four pillars in one hybrid retrieval graph.

| Pillar                       | What gets stored                                                                                              | How it surfaces                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| **🏗&nbsp;Architecture**      | Module relations, repeated workflow patterns, branch-aware context                                            | Graph traversal on `recall`; auto-injected at session start      |
| **📁&nbsp;Files**            | `file:<path>`, `function:<file>::<name>`, `class:<file>::<name>` entities with exports / imports / docstrings | `easymem search` and `easymem index-code`                        |
| **🧭&nbsp;Decisions**        | Title, rationale, alternatives, outcome — linked to the entities they affect                                  | `easymem decide` to capture; surfaced on the next session        |
| **🪪&nbsp;Slots**            | Editable `persona`, `preferences`, `guidelines` strings                                                       | Injected at session start                                        |

All four live in the same hybrid retrieval graph — one search returns the most relevant mix, not four siloed queries.

---

## 2. Demo

```bash
# 1. Capture — index your codebase, record a decision, pin a persona
$ easymem index-code .
indexed: 47 files, 312 symbols, removed: 0 stale, relations: 89

$ easymem decide '{"title":"Use JWT","rationale":"Stateless, no session DB","alternatives":["cookies"]}'
✓ decision recorded

$ easymem slots set persona "senior backend engineer; prefer explicit types"
✓ persona set
```

```bash
# 2. Recall — next day, in a new Claude Code session
SessionStart hook (auto-injected, ~50 tokens):
  📌 persona: senior backend engineer; prefer explicit types
  Recent: Decision "Use JWT" (2026-05-22) · file:src/auth.py
  Pending decisions: 0

USER: "How do we handle auth?"

Agent → easymem search "how do we handle auth?"
─ Decision (2026-05-22)        "Use JWT" — stateless, no session DB
─ file:src/auth.py             (export: login, login_handler; doc: Auth helpers)
─ function:src/auth.py::login
─ Workflow "auth refactor"     4 related sessions (login → token → refresh)

Agent → easymem recall "AuthService"
        → JWTValidator, SessionStore, login_handler (1-hop neighbours)

USER: "Switch to a CRDT-based session store"
Agent edits the code, then captures the follow-up decision:
  easymem decide '{"title":"CRDT session store",
                    "rationale":"Preserve concurrent edits",
                    "alternatives":["LWW — loses concurrent writes"]}'
```

- **Why · Where · How** — decision, file + symbol, and workflow surfaced in one search hop.
- **Primed with persona** — slots auto-injected at SessionStart; no re-explaining.
- **Closes the loop** — the new CRDT decision lands in the same graph, available to every future session.

---

## 3. Getting Started

Requires Python 3.10+ and git. Vector retrieval pulls 3 pip deps (`requirements.txt`); `install.sh --no-vector` skips them.

### a. Claude Code (recommended)

```text
/plugin marketplace add locx/easymem
/plugin install easymem
```

The plugin wires `SessionStart` / `PostToolUse` / `Stop` / `PreCompact` hooks globally. Per-project state is created on first use — see §3.c.

### b. Other agents (Codex, scripts, CI)

```bash
git clone https://github.com/locx/easymem.git
cd easymem
./install.sh                       # Deploys runtime + hooks + venv (model2vec, numpy, orjson)
./install.sh --minimal             # OR: files only, no hook wiring, no vector deps

# Optional: initialize a project explicitly (the SessionStart hook does this on first use)
./setup-project.sh /path/to/project

# Use the easymem wrapper globally
export PATH="$HOME/.claude/easymem-bin:$PATH"
easymem status                     # Verify install
```

| Flag          | Effect                               |
| ------------- | ------------------------------------ |
| `--no-vector` | Skip pip deps; lexical (TF-IDF) only |
| `--no-hooks`  | Skip hook wiring (manual activation) |
| `--minimal`   | Both of the above                    |

`install.sh` auto-detects the plugin and skips its own hook registration to prevent double-fire.

### c. First-Session Auto-Init

When Claude Code starts in a project without `.easymem/`, the `SessionStart` hook auto-runs setup. It creates:

- **`.easymem/`** — graph + config + indices (also added to your `.gitignore`).
- **`~/.claude/easymem-bin/easymem`** — stable wrapper. `CLAUDE.md` docs and the Bash allow-list both reference this path so they don't drift across plugin updates.
- **`.claude/settings.json`** — appends `Bash($HOME/.claude/easymem-bin/easymem *)` to the allow-list.
- **`CLAUDE.md`** — appends an EasyMem section so the agent knows the commands available to it. Creates the file if it doesn't exist.

If auto-setup fails (e.g. missing `python3`), the hook prints the manual `setup-project.sh` command instead.

### d. Suppressing the Nudge

If `.easymem/` is later missing on a session (you deleted it deliberately, or auto-setup failed), the hook nudges once per 24 hours. After three nudges it offers `easymem nudge suppress` — that writes a marker at `~/.config/easymem/nudge/<hash>.suppress` and silences the hook for that project. Delete the marker to re-enable.

---

## 4. Operations & Usage

### a. Autopilot — Hooks Drive the Loop

Hooks + the agent's `CLAUDE.md` section drive the daily loop. The agent searches before editing unfamiliar code, captures decisions, links related entities, and refreshes the code-structure index — without explicit invocation.

Visible artifacts: `.easymem/graph.jsonl` (append-only entity + relation log) and `.easymem/contradictions.json` (conflict sidecar).

### b. Day-Two — Inspect, Update, Cleanup

```bash
# Search and recall
easymem search "JWT validation"
easymem recall "JWT validation"           # search + 1-hop neighbours

# Capture and pin
easymem decide '{"title":"...", "rationale":"...", "alternatives":[]}'
easymem slots set persona "senior Python engineer"

# Inspect
easymem status                            # graph health + pending decision count
easymem doctor                            # orphans, stale index, contradictions
easymem diff                              # what changed since last session

# Refresh indices
easymem index-code .                      # refresh code-structure entities
easymem rebuild --rebuild-now             # force TF-IDF + vector rebuild

# Update
#   Plugin install: /plugin update easymem    (from within Claude Code)
#   Legacy install: git pull && ./install.sh && ./setup-project.sh /path/to/project

# Sync — portable JSON bundle for cross-machine moves
./export-easymem.sh /path/to/project bundle.json
./import-easymem.sh bundle.json /path/to/target

# Cleanup — prompts before destructive steps
./cleanup.sh project /path/to/project     # Remove .easymem/, settings entries, CLAUDE.md section
./cleanup.sh global                       # Remove ~/.claude/easymem/ + hook wiring
```

---

## 5. Under the Hood

### a. Self-Managed Maintenance

Runs on schedule (and on `easymem rebuild`); you don't invoke these — hooks do.

- **🔄 Hebbian decay** — frequently recalled entities strengthen; untouched ones fade and prune past `MAX_AGE_DAYS`.
- **⚖️ Contradiction auto-resolve** — two same-source episodes that conflict: later supersedes, earlier marked `superseded:` (not deleted).
- **🧩 Workflow extraction** — episodes sharing a neighbour set ≥3 times mint a `workflow:` entity (e.g. "auth refactor: login → token → refresh").
- **🌿 Branch-aware scoring** — relevance rebalances as you switch git branches. `main` is always preserved.
- **📌 Code-stamp watch** — source files past the recorded stamp trigger an index refresh on the next pass.

### b. Fast Hybrid Retrieval

| Stage                                | Effect                                                                |
| ------------------------------------ | --------------------------------------------------------------------- |
| 🔀 **TF-IDF per-observation**        | Indexed at observation level, not per-entity — finer-grained matches. |
| 🧮 **Int8 model2vec vectors**        | ~256 dim — fast cosine, small memory.                                 |
| 🪢 **RRF fusion (k=60)**             | Merges TF-IDF and vector ranks without re-scoring.                    |
| 🎯 **IDF-weighted re-rank (top 30)** | Cheap; lifts R@5 by ~5pp on LongMemEval-S.                            |
| 🪟 **Session diversification**       | Caps hits per source — no single verbose conversation dominates.      |

> LongMemEval-S p50 **116 ms**, p95 **157 ms** end-to-end including maintenance per query.

---

## 6. Compared to the Field

|                        | easymem                           | agentmemory                                | Mem0         | Zep              | Letta             |
| ---------------------- | --------------------------------- | ------------------------------------------ | ------------ | ---------------- | ----------------- |
| LongMemEval-S R@5      | **0.948**                         | 0.952 (claim)                              | —            | —                | —                 |
| External deps          | none (Python pip deps only)       | none (SQLite + iii)                        | vector DB    | Postgres + Neo4j | Postgres + vector |
| Code-structure indexer | **yes**                           | file-access only                           | no           | no               | no                |
| Local-first            | yes                               | yes                                        | optional     | optional         | yes               |
| Memory tiers           | episode → consolidated → workflow | working → episodic → semantic → procedural | flat + facts | temporal graph   | hierarchical      |
| Install                | `/plugin install`                 | `/plugin install`                          | SDK call     | run server       | run server        |

Non-easymem rows are each project's own published claim; verify against the source before citing. `—` indicates no public R@5 on LongMemEval-S found.

---

## 7. How It Works

```text
   ┌─────────────────────┐
   │  Claude Code Agent  │
   └──────────┬──────────┘
              │  hooks  +  Bash CLI
              ▼
   ┌─────────────────────┐   search · recall · decide
   │  easymem CLI        │   slots · index-code · status
   └──────────┬──────────┘
              │  search query
              ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  semantic_server                                                             │
   │    TF-IDF (per-obs) → Vector (int8 model2vec) → RRF fusion → IDF re-rank     │
   └──────────┬───────────────────────────────────────────────────────────────────┘
              │  append-only JSONL
              ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  .easymem/                                                                   │
   │  graph.jsonl · tfidf_index · vectors.bin · slots.json · contradictions.json  │
   └──────────────────────────────────────────────────────────────────────────────┘

   Background: SessionStart / PostToolUse / Stop / PreCompact
   hooks + maintenance + code-indexer keep the graph fresh.
```

### a. Storage

Append-only JSONL at `.easymem/graph.jsonl`. Each line is an entity or a relation. `flock` + `fsync` + `os.replace` make every write atomic — either the old or new graph survives, never a partial.

### b. Code-Structure Indexer

Per source file: emit `file:<rel-path>` with observations for `lang`, `export:`, `import:`, and per-line `doc:`. Symbol-level entities `function:<file>::<name>` and `class:<file>::<name>` link back via `defined_in` relations. Cross-file `imports` relations connect resolved file pairs.

Python uses `ast` for exact extraction. TypeScript / JavaScript handles re-exports and aliased exports via regex. Go, Rust, Ruby use regex — good enough for retrieval, not for static analysis.

### c. Hooks

| Event          | Script                      | Purpose                                          |
| -------------- | ----------------------------| ------------------------------------------------ |
| `SessionStart` |  `prime-easymem.sh`         | Inject recent decisions and top-scored entities  |
| `SessionStart` |  `nudge-setup.sh`           | Auto-init project / first-run hint               |
| `SessionStart` |  `prime-slots.sh`           | Inject pinned slots                              |
| `PostToolUse`  |  `capture-tool-context.sh`  | Capture tool calls as observations               |
| `Stop`         |  `capture-decisions.sh`     | Capture user decisions at end-of-turn            |
| `PreCompact`   |  `prime-on-compact.sh`      | Surface critical context before Claude compacts  |

---

## 8. Project Info

### a. Limitations

- **🔍 TS / JS / Go / Rust / Ruby extractors are regex-based.** They cover common patterns but miss esoteric forms (computed exports, conditional imports, macros). Python is AST-based and exact.
- **📉 LongMemEval-S `single-session-preference` category sits at 0.60** (the other five categories all ≥ 0.94). Short attribute-style queries against many similar sessions are the failure mode.
- **💾 Full 19,195-session global LongMemEval-S** OOMs a typical laptop's vector-index bootstrap. The per-query protocol (the dataset paper's standard) is what produces the published numbers.
- **🔗 No multi-device sync.** Local-first is a feature, not a limitation — but if you want cross-machine memory, this isn't the tool.

### b. Troubleshooting

| Symptom                          | Cause                                    | Fix                                                                  |
| -------------------------------- | ---------------------------------------- | -------------------------------------------------------------------- |
| 🔍 `easymem: command not found`     | PATH not updated                         | `export PATH="$HOME/.claude/easymem-bin:$PATH"`                      |
| 📭 Empty search results             | Index stale                              | `easymem rebuild --rebuild-now`                                      |
| 📁 Search returns chat but not code | Code-stamp not refreshed                 | `easymem index-code .`                                               |
| 🪝 Hooks firing twice               | Plugin AND `install.sh` both registered  | Re-run `install.sh` — it auto-skips hooks when the plugin is present |

### c. Platform & License

macOS · Linux · Windows via WSL (native install has no `fcntl`).

License: MIT. See [LICENSE](LICENSE).
