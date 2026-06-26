#!/usr/bin/env bash
# Canonical EasyMem privacy/durability smoke gate (Appendix A, verbatim). No
# `set -e`: normal operation relies on commands (grep -Ev with no egress) exiting
# nonzero, so set -e would false-trip the gate. Hard gates exit 1 explicitly.

# EasyMem has no server/port; it targets a project's .easymem/, overridable via EASYMEM_DIR. The
# wrapper isn't on PATH in a worktree, so call $REPO/easymem; a non-read-only cmd auto-bootstraps.
REPO=$(git rev-parse --show-toplevel); EM="$REPO/easymem"
SMOKE="/tmp/em-smoke-$$"; export EASYMEM_DIR="$SMOKE/.easymem"
GRAPH="$EASYMEM_DIR/graph.jsonl"; INPUT_JSON="/tmp/em-toolctx-$$.json"
mkdir -p "$SMOKE"; cd "$SMOKE" || exit 1
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
