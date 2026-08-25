#!/usr/bin/env bash
# End-to-end smoke for the local docker stack: ingest → fold → search → as-of → consent purge.
# Requires: docker compose stack up (or any memory-graph at $BASE), curl, jq, python3.
#
#   ./scripts/smoke_local.sh                       # against http://localhost:8000
#   BASE=http://localhost:8000 ./scripts/smoke_local.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
ORG="smoke-org-$$"
H_ALICE=(-H "X-Test-Org-Id: ${ORG}" -H "X-Test-User-Id: alice" -H 'Content-Type: application/json')
PASS=0; FAIL=0

say()  { printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗ %s\033[0m\n' "$*"; FAIL=$((FAIL+1)); }
check(){ if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (want '$2' got '$1')"; fi; }

say "1/7 health"
STATUS=$(curl -sf "${BASE}/health" | jq -r .status)
check "$STATUS" "ok" "service healthy"

say "2/7 ingest episodes (two-timestamped decision pair for as-of) + inline fold"
T1="2026-01-10T00:00:00Z"; T2="2026-06-01T00:00:00Z"
BODY=$(cat <<JSON
{"episodes":[
 {"event_id":"s1-$$","org_id":"${ORG}","occurred_at":"${T1}","actor":{"user_id":"alice"},
  "source":"smoke","action_type":"agent.deployed","scope":"org","scope_id":"${ORG}",
  "subject":{"agent_id":"smoke-bot"},"payload":{"status":"v1"}},
 {"event_id":"s2-$$","org_id":"${ORG}","occurred_at":"${T2}","actor":{"user_id":"alice"},
  "source":"smoke","action_type":"agent.deployed","scope":"org","scope_id":"${ORG}",
  "subject":{"agent_id":"smoke-bot"},"payload":{"status":"v2"}}
]}
JSON
)
ACCEPTED=$(curl -sf -X POST "${BASE}/api/v1/episodes?process=true" "${H_ALICE[@]}" -d "$BODY" | jq -r .accepted)
check "$ACCEPTED" "2" "episodes accepted + folded inline"

say "3/7 derived-tier search finds the agent"
NODES=$(curl -sf -G "${BASE}/api/v1/memory/search" --data-urlencode "q=smoke-bot" "${H_ALICE[@]}" | jq '.nodes | length')
[ "$NODES" -ge 1 ] && ok "search returns nodes (${NODES})" || bad "search returned no nodes"

say "4/7 direct-tier document ingest + passage retrieval"
DOC='{"content":"The smoke runbook: restart the ingest worker before every demo. Secrets live in the vault.","source":"smoke","scope":"user","scope_id":"alice","title":"runbook"}'
DSTATUS=$(curl -sf -X POST "${BASE}/api/v1/documents" "${H_ALICE[@]}" -d "$DOC" | jq -r .status)
check "$DSTATUS" "processed" "document processed through the governed fold"
PASSAGES=$(curl -sf -G "${BASE}/api/v1/memory/search" --data-urlencode "q=restart ingest worker" "${H_ALICE[@]}" | jq '.passages | length')
[ "$PASSAGES" -ge 1 ] && ok "passage retrieved (${PASSAGES})" || bad "no passages retrieved"

say "5/7 bi-temporal as-of read (world-time between the two deploys)"
MID="2026-03-01T00:00:00Z"
AS_OF_EDGES=$(curl -sf -G "${BASE}/api/v1/memory/search" \
  --data-urlencode "q=smoke-bot" --data-urlencode "as_of=${MID}" "${H_ALICE[@]}" | jq '.edges | length')
NOW_EDGES=$(curl -sf -G "${BASE}/api/v1/memory/search" --data-urlencode "q=smoke-bot" "${H_ALICE[@]}" | jq '.edges | length')
ok "as-of=${MID} edges=${AS_OF_EDGES}, now edges=${NOW_EDGES} (point-in-time read served)"

say "6/7 explain trace"
SCOPES=$(curl -sf -G "${BASE}/api/v1/memory/explain" --data-urlencode "q=smoke-bot" "${H_ALICE[@]}" | jq '.accessible_scopes | length')
[ "$SCOPES" -ge 2 ] && ok "explain lists accessible scopes (${SCOPES})" || bad "explain scopes missing"

say "7/7 consent CLEAR purges alice's personal store"
curl -sf -X PUT "${BASE}/api/v1/memory/consent" "${H_ALICE[@]}" \
  -d '{"scope":"user","scope_id":"alice","state":"clear"}' > /dev/null
sleep 1
LEFT=$(curl -sf -G "${BASE}/api/v1/memory/search" --data-urlencode "q=restart ingest worker" \
  --data-urlencode "scope=user" "${H_ALICE[@]}" | jq '.passages | length')
check "$LEFT" "0" "personal passages purged after CLEAR"

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[1;32mSMOKE PASSED (%d checks)\033[0m\n' "$PASS"
else
  printf '\033[1;31mSMOKE FAILED (%d passed, %d failed)\033[0m\n' "$PASS" "$FAIL"
  exit 1
fi
