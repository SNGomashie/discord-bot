#!/bin/bash
# Ralph loop: runs fresh Claude Code iterations until all stories in prd.json pass.
# Usage: scripts/ralph/ralph.sh [max_iterations]
set -euo pipefail

MAX=${1:-10}
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
cd "$ROOT"

# Safety: Ralph runs with permissions skipped. Only run against the DEV environment.
if [ ! -f .env ] || ! grep -qE '^ENVIRONMENT=dev\s*$' .env; then
  echo "Refusing to run: .env missing or ENVIRONMENT is not 'dev'." >&2
  exit 1
fi
[ -f "$DIR/prd.json" ] || { echo "Missing $DIR/prd.json (use the ralph skill to create it)." >&2; exit 1; }
[ -f "$DIR/progress.txt" ] || echo "# Ralph progress log — started $(date -I)" > "$DIR/progress.txt"

# Work on the branch named in prd.json
BRANCH=$(jq -r '.branchName' "$DIR/prd.json")
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"

for i in $(seq 1 "$MAX"); do
  echo "=== Ralph iteration $i/$MAX ($(date +%T)) ==="
  OUTPUT=$(claude --dangerously-skip-permissions -p "$(cat "$DIR/prompt.md")" 2>&1 | tee /dev/stderr) || true
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo "Ralph finished: all stories pass."
    exit 0
  fi
  sleep 2
done
echo "Ralph hit max iterations ($MAX) without completing." >&2
exit 1
