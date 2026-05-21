# easymem 🧠

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20WSL-lightgrey.svg)](https://github.com/locx/easymem)

**Hardened, high-performance, self-governing memory for Claude Code agents.**

Stop starting every session from scratch. **easymem** provides a persistent knowledge graph that captures decisions, remembers buggy patterns, and strengthens with every turn.

### 🚀 Hybrid Retrieval · Self-Governing · Pure Python + numpy

```
Session 1: "Use LWW for SyncManager" ──▶ Capture Decision + Context
Session 2: "How does sync work?"     ──▶ Hybrid recall (TF-IDF ⊕ vector RRF) + Graph Neighbors
```

---

## Contents

|      # | Section | Key Focus |
| -----: | --------------------------------------------------- | --------------------------------------------------------------------------- |
|  **1** | [**Memory Advantage**](#1-memory-advantage)         | Hybrid · Branch-aware · Hebbian · Atomic Resilience |
|  **2** | [**How It Works**](#2-how-it-works)                 | Graph · Tools · Hybrid Search · Maintenance · Hooks · Write Path |
|  **3** | [**Getting Started**](#3-getting-started)           | Install · Flags · Slash Commands |
|  **4** | [**Operations & Usage**](#4-operations--usage)       | Zero Effort · Session Walkthrough · Cleanup |
|  **5** | [**Decision Tracking**](#5-decision-tracking)       | Record · List · Update Outcome |
|  **6** | [**Configuration**](#6-configuration)               | Limits · Tuning · Default Values |
|  **7** | [**Architecture & Design**](#7-architecture--design)| Design Philosophy · Layout · Package Internals |
|  **8** | [**Project Info**](#8-project-info)                  | Troubleshooting · Limits · Platform · License |

---

## 1. Memory Advantage

Architecture-aware memory with semantic recall, not just flat files.

| Feature                  | Impact                                                                                    |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| **Hybrid Retrieval**     | 🔀 TF-IDF ⊕ vector (model2vec int8) fused via Reciprocal Rank Fusion. Lexical precision + semantic recall. |
| **Branch-aware**         | 🌿 Scores rebalance automatically as you switch git branches. `main` is always preserved. |
| **Hebbian Recall**       | 🧠 Frequently searched knowledge is reinforced; untouched data fades out.                 |
| **Self-Regulating**      | 🔄 Daily maintenance scores, prunes, and consolidates. Zero intervention required.        |
| **Structured Decisions** | 📝 Captures rationale, chosen approach, and alternatives. Linked directly to components.  |
| **Contradiction Watch**  | ⚖️  Pairwise scan flags entities with conflicting observations (sidecar JSON).            |
| **Secret Scrubbing**     | 🔒 `AKIA…`, `ghp_…`, `sk-…`, `Bearer …`, `PRIVATE KEY` redacted before persistence.       |
| **Atomic Resilience**    | 🛡️  `flock` + `fsync` + `os.replace`. Old or new survives; the graph never corrupts.      |
| **Incremental I/O**      | ⚡ Only scales with changes, not graph size; reads stay sub-second.                       |

> **CLAUDE.md** defines your static rules; the **Knowledge Graph** tracks your architectural evolution.

---

## 2. How It Works

<div align="center">
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                        SESSION LIFECYCLE                            │
│                                                                     │
│  ┌──────────────┐    ┌────────────────┐    ┌─────────────────────┐  │
│  │ Session Start│    │ During Session │    │ Session End         │  │
│  │              │    │                │    │                     │  │
│  │ prime-       │    │  CLI Bridge    │    │ capture-decisions   │  │
│  │ easymem.sh   │──▶ │ (9 cmds + mint)│──▶ │ reminds Claude to   │  │
│  │              │    │                │    │ persist decisions   │  │
│  └──────┬───────┘    └───────┬────────┘    └─────────────────────┘  │
│         │                    │                                      │
│         ▼                    ▼                                      │
│  ┌──────────────┐    ┌────────────────┐                             │
│  │ maintenance  │    │ capture-tool   │                             │
│  │ .py (1x/day) │    │ -context       │                             │
│  │              │    │ (scrub + mint) │                             │
│  └──────┬───────┘    └───────┬────────┘                             │
│         │                    │                                      │
│         │            ┌───────▼────────┐                             │
│         │            │ prime-on-      │                             │
│         │            │ compact.sh     │                             │
│         │            │ (PreCompact)   │                             │
│         │            └───────┬────────┘                             │
│         │                    │                                      │
│         ▼                    ▼                                      │
│  ┌──────────────────────────────────────────────┐                   │
│  │           .easymem/graph.jsonl               │                   │
│  │    entities + relations + observations       │                   │
│  │     + _branch · _source · _created stamps    │                   │
│  │           (append-only writes)               │                   │
│  └──────────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
</pre>
</div>

### a. Knowledge Graph

One `graph.jsonl` per project. Two object types: **Entities** (nodes) and **Relations** (edges).

```json
{"type":"entity","name":"SyncManager","entityType":"component",
 "observations":["Uses LWW resolution"],
 "_branch":"feature/sync","_source":"episode:Claude"}

{"type":"relation","from":"SyncManager","to":"ProviderRegistry","relationType":"uses"}
```

Each entity carries facts, `_branch`, `_created`, and `_source` provenance. Claude traverses relations to surface knowledge it wasn't told to look for.

**Episodes** (`entityType: "episode"`) auto-record what happened — three kinds minted by PostToolUse:

- `episode:err:*` — errors
- `episode:churn:*` — high-frequency file edits
- `episode:commit:*` — git commits

They flow through the same write path, scoring, and pruning as any entity.

### b. Tool Reference

All access via the `easymem` CLI wrapper (backed by `easymem-cli.py`).

| Command           | Role                                                                          | Complexity |
| ----------------- | ----------------------------------------------------------------------------- | ---------- |
| `easymem search`  | Hybrid lookup: TF-IDF postings + dense dot-product, fused via RRF             | O(k + V)   |
| `easymem recall`  | Smart context: search + 1-hop neighbors via cached adjacency                  | O(V + E)   |
| `easymem write`   | Append-only create/merge of entities, relations, observations (flock-guarded) | O(1)       |
| `easymem decide`  | Append-only architectural trade-off + rationale entry                         | O(1)       |
| `easymem remove`  | Atomic deletion or renaming — locked full-graph rewrite                       | O(n)       |
| `easymem status`  | Real-time health, stats, and pending decision nudges                          | O(n)       |
| `easymem diff`    | Entities/relations changed since last session marker                          | O(n)       |
| `easymem doctor`  | Locate orphans, technical debt, and stale indices                             | O(n)       |
| `easymem rebuild` | Sorted-merge consolidation + full TF-IDF/vector rebuild                       | O(n log n) |

### c. How Search Works — Hybrid Retrieval

Two independent rankers, fused by **Reciprocal Rank Fusion (RRF)**:

**Lexical (TF-IDF)** — High-speed cosine over a positional inverted index.

- 🧩 **Stemming**: Integrated Porter Stemmer matches variants (e.g., `running` → `run`).
- 📂 **Inverted Index**: Only entities with query terms are scored.
- ⚖️ **Distinctive Weights**: BM25-style scaling prioritizes rare terms over boilerplate.

**Vector (model2vec, int8)** — Static embeddings, no GPU, no inference server.

- 🧠 **Semantic match**: catches paraphrases, synonyms, conceptual neighbors TF-IDF misses.
- 🪶 **Quantized**: int8 storage; query and corpus quantized identically.
- ⚡ **Sub-ms**: numpy dot-product over the in-memory corpus matrix.

**RRF fusion** — rank-based, scale-free. `score(d) = Σ 1 / (k + rank_i(d))`. No tuning.

Disable vectors with `install.sh --no-vector` to keep lexical-only deployment (zero pip deps).

### d. Maintenance — Self-Cleaning Pipeline

Runs at session start, ≤1x/24h.

```
      graph.jsonl ──▶ backup ──▶ stamp ──▶ score ──▶ prune ──▶ consolidate
                                                                    │
            graph.jsonl ◀── promote ◀── index ◀── prune recall ◀── cap obs
                              │
                              ▼
                ~/.claude/projects/<slug>/memory/MEMORY.md
                  (top-10 entities, between markers)
```

| Phase             | Logic                                            |
| ----------------- | ------------------------------------------------ |
| **Backup**        | O(1) hard-link before any mutation               |
| **Stamping**      | Automatic `_branch`, `_created`, `_source` tags  |
| **Scoring**       | `obs_count × recency_weight × log(recall_count)` |
| **Pruning**       | Removes score < 0.1 unless bound by relations    |
| **Consolidating** | Merges near-duplicate entities (Jaccard + name)  |
| **Indexing**      | Full TF-IDF + vector rebuild                     |
| **Promotion**     | Top-10 entities written to Claude's `MEMORY.md`  |
| **Contradiction** | Sidecar `.easymem/contradictions.json` flagged   |

### e. Lifecycle Hooks

Five hooks, CLI only. VSCode detected and skipped in <1ms — `CLAUDE.md` handles it.

| Hook                      | Event        | Action                                                                |
| ------------------------- | ------------ | --------------------------------------------------------------------- |
| `prime-easymem.sh`        | SessionStart | Maintenance + scored recall with 1-hop relations + pending decisions  |
| `prime-on-compact.sh`     | PreCompact   | Inject compact recall into post-compaction context (survives compact) |
| `capture-tool-context.sh` | PostToolUse  | Surface file warnings from graph; auto-mint episodes (throttled)      |
| `capture-decisions.sh`    | Stop         | Persist-decisions reminder                                            |
| `nudge-setup.sh`          | SessionStart | One-time setup notice (no `.easymem/`)                                |

### f. Write Path

Every persisted entity flows through the same pipeline:

```text
  caller (CLI / hook / episode mint)
        │
        ▼
  ┌──────────────────────────────────────────────┐
  │ 1. Scrub   AKIA… · ghp_… · sk-… · Bearer …   │
  │            · PRIVATE KEY  → redacted         │
  │ 2. Stamp   _branch · _created · _source      │
  │ 3. Lock    flock(.easymem/graph.jsonl)       │
  │ 4. Append  JSONL line → fsync                │
  │ 5. Replace (rewrites only) os.replace atomic │
  └──────────────────────────────────────────────┘
        │
        ▼
  graph.jsonl  (durable, branch-aware, scrubbed)
```

Manual `easymem write`, hook-driven episode auto-mint, and consolidation during maintenance all use this path.

---

## 3. Getting Started

Requires Python 3.10+ and git. Vector retrieval pulls 3 pip deps (`requirements.txt`); `--no-vector` skips.

### a. First Run

```bash
git clone https://github.com/locx/easymem.git
cd easymem
./install.sh                  # Deploys runtime + hooks + venv (with model2vec/numpy/orjson)
./setup-project.sh /path      # Injects CLAUDE.md bridge + bootstraps graph

# Use the 'easymem' wrapper globally
export PATH="$HOME/.claude/easymem:$PATH"
easymem status
```

Installer flags:

| Flag           | Effect                                                  |
| -------------- | ------------------------------------------------------- |
| `--no-vector`  | Skip pip deps; lexical (TF-IDF) only                    |
| `--no-hooks`   | Skip `settings.json` wiring (manual hook activation)    |
| `--minimal`    | Both of the above                                       |

### b. Slash Commands

Quick access from any conversation:

| Command            | Action                                       |
| ------------------ | -------------------------------------------- |
| `/easymem-recall`  | Hybrid search + 1-hop neighbors              |
| `/easymem-status`  | Health, pending decisions, index age         |
| `/easymem-decide`  | Capture a structured architectural decision  |

---

## 4. Operations & Usage

### a. Zero Effort Usage

Hooks + `CLAUDE.md` drive everything (timing: §2.e). During work, Claude silently searches before editing unfamiliar code, records decisions, flags fragile code, links related entities; PostToolUse auto-mints episode entities with `_source` provenance.

Visible operator artifacts:

- `~/.claude/projects/<slug>/memory/MEMORY.md` — top-10 entities auto-promoted between markers each maintenance run.
- `.easymem/contradictions.json` — sidecar flags entities with conflicting observations.
- Slash commands `/easymem-recall`, `/easymem-status`, `/easymem-decide` — usable from any conversation.

### b. A Session in Action

```
1. USER: "How does our sync handle conflicts?"
   Claude → easymem search "sync conflict" → SyncManager (lexical 0.87, vector 0.71)
                                            → RRF rank 1
   Claude → easymem recall "SyncManager" → ProviderRegistry, ConfigStore

2. USER: "Switch from LWW to CRDT"
   Claude edits code
   Claude silently: easymem decide '{"title":"Switch from LWW to CRDT",
     "chosen":"CRDT merge","rationale":"Preserve concurrent edits",
     "alternatives":["LWW — simpler but loses writes"]}'
```

### c. Day-Two — Inspect, Update, Cleanup

```bash
# Update — refresh runtime + bridge
./install.sh && ./setup-project.sh /path/to/project

# Inspect — orphans, stale index, contradictions, pending decisions
easymem doctor

# Sync — portable JSON bundle for cross-machine moves
./export-easymem.sh /path/to/project bundle.json
./import-easymem.sh bundle.json /path/to/target

# Cleanup — prompts before destructive steps
./cleanup.sh project /path      # Remove one project graph
./cleanup.sh global             # Remove runtime + hooks
```

---

## 5. Decision Tracking

Decisions are **structured graph entities** — not notes, not comments.

### a. Record a Decision

```bash
easymem decide '{
  "title": "Use PostgreSQL over MongoDB for user data",
  "rationale": "ACID for billing. Relational model fits user/org hierarchy.",
  "chosen": "PostgreSQL",
  "alternatives": ["MongoDB — no multi-doc txns", "CockroachDB — too much ops"]
}'
```

### b. List Decisions

```bash
easymem status
```

Returns all decisions sorted by recency with their current outcome status. Pending decisions older than 2 days are flagged.

### c. Update the Outcome

```bash
easymem decide '{"action":"resolve","title":"Use PostgreSQL over MongoDB","outcome":"successful","lesson":"ACID saved us during billing migration"}'
```

Outcomes: `pending` · `successful` · `failed` · `revised` · `adopted` · `rejected` · `deferred`

---

## 6. Configuration

`.easymem/config.json` — delete any key for default:

| Key                  | Default                       | Effect                                |
| -------------------- | ----------------------------- | ------------------------------------- |
| `decay_threshold`    | 0.1                           | Score floor for pruning               |
| `max_age_days`       | 90                            | Age penalty ceiling                   |
| `throttle_hours`     | 24                            | Maintenance frequency                 |
| `min_merge_name_len` | 4                             | Exact-match threshold for short names |
| `embed_model`        | `minishlab/potion-retrieval-32M` | Static embedding model (model2vec)    |

---

## 7. Architecture & Design

### a. Design Philosophy

1. **Agent-Centric**: Terse outputs and graph guidance built for LLMs.
2. **Lean Stack**: Pure Python + numpy + model2vec + orjson. No DB, no inference server.
3. **Implicit Growth**: Memory is a side-effect of work, not a workflow tax.
4. **Hardened IO**: Atomic replace + fsync. No partial writes.

### b. Layout

```
~/.claude/                              GLOBAL RUNTIME
  hooks/
    prime-easymem.sh                    SessionStart → maintenance + recall
    prime-on-compact.sh                 PreCompact → context injection
    capture-tool-context.sh/.py         PostToolUse → file warnings + episodes
    capture-decisions.sh                Stop → decision reminder
    nudge-setup.sh                      SessionStart → first-run notice
  easymem/
    easymem                             Wrapper binary (resolves project root)
    easymem-cli.py                      CLI bridge
    maintenance.py                      Decay / prune / merge / index / promote
    semantic_server/                    Engine package (18 modules)
    venv/                               Vector runtime (model2vec/numpy/orjson)

<project>/                              PER-PROJECT
  CLAUDE.md                             Bridge instructions (auto-injected)
  .easymem/
    graph.jsonl                         The graph (append-only)
    tfidf_index.json                    Lexical index
    vector_index.npy                    Quantized embedding matrix (int8)
    recall_counts.json                  Hebbian frequencies
    contradictions.json                 Conflict sidecar (auto-generated)
    config.json                         Project-local overrides
```

### c. Under the Hood — Package Internals

Modular, low-latency engine core in `semantic_server/` (18 modules):

| Component        | Support Modules                       | Role                                            |
| ---------------- | ------------------------------------- | ----------------------------------------------- |
| **Persistence**  | `graph`, `_json`, `io_utils`          | Atomic I/O, byte-offset reads, orjson recovery  |
| **Intelligence** | `search`, `recall`, `vector`, `stem`  | TF-IDF + vector RRF, Hebbian LRU, Porter Stemmer |
| **Mechanics**    | `text`, `traverse`                    | Tokenization, synonyms, BFS-cached adjacency    |
| **Maintenance**  | `maintenance_utils`                   | Pruning, consolidation, contradiction detection |
| **Interface**    | `tools`, `cache`, `protocol`, `server`| Command logic, tiered eviction, JSON-RPC        |
| **Lifecycle**    | `bootstrap`, `config`                 | Project init, env-driven settings               |

---

## 8. Project Info

### a. Troubleshooting

| Symptom                  | Fix                                                                         |
| ------------------------ | --------------------------------------------------------------------------- |
| Tools missing            | Re-run `install.sh`                                                         |
| Search empty             | Rebuild index: `easymem rebuild`                                            |
| Vector results missing   | Check `model2vec` in venv; re-run `install.sh` without `--no-vector`        |
| Graph too large          | Raise `decay_threshold` or run maintenance                                  |
| Agent asks permission    | Re-run `setup-project.sh` — refreshes CLAUDE.md bridge instructions         |
| Maintenance stuck        | Delete `.easymem/.last-maintenance`                                         |

### b. Known Limitations

- **Lexical + static embeddings** — no live inference; semantic recall bounded by the chosen model2vec checkpoint.
- **No read locking** — concurrent reads see the prior atomic snapshot; mid-maintenance partial state is invisible.
- **ASCII name splitting** — CJK/non-Latin merges on exact match only.
- **Single machine** — no automatic sync; use `./export-easymem.sh` + `./import-easymem.sh` for portable JSON bundle moves.
- **Engine ceilings** (hardcoded in `semantic_server/config.py`) — graph ≤50MB · ≤100K entities · 50MB combined cache · 10K LRU recall · 20 obs/entity cached · 256 embed dim.

### c. Platform & License

macOS (ARM64/x86) · Linux (x86/ARM) · Windows via WSL (native: no `fcntl`).

**License**: [MIT](LICENSE)
