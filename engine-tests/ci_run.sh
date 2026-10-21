#!/usr/bin/env bash
# Run engine tests in CI: each one's output is shown as usual, and when one fails its
# failing checks (and the end of its output) become GitHub annotations, readable on
# the run's summary without opening the log.
#   bash engine-tests/ci_run.sh run_relocate.py run_reach.py ...
set -u
here="$(cd "$(dirname "$0")" && pwd)"
status=0
for t in "$@"; do
  out="$(mktemp)"
  if python "$here/$t" 2>&1 | tee "$out"; [ "${PIPESTATUS[0]}" -ne 0 ]; then
    grep -E "^\s*FAIL|Traceback|Error" "$out" | head -15 | while IFS= read -r l; do echo "::error title=$t::$l"; done
    tail -4 "$out" | while IFS= read -r l; do echo "::error title=$t (end)::$l"; done
    status=1
  fi
  rm -f "$out"
done
exit $status
