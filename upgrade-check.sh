#!/usr/bin/env bash
# =============================================================================
# upgrade-check.sh — the weekly "is anything outdated or known-vulnerable?"
#
# Runs `tools/upgrade.py --check`, which REPORTS and never applies (an
# upgrade rebuilds the venv and restarts the service — a person's decision),
# and appends the report to upgrade-check.log. The report ends in an
# Attention list (DO / DECIDE / WAIT / NOTE). A macOS notification is posted
# when there is a DO, or a DECIDE/WAIT that was not there last week: a log
# nobody opens is a finding nobody acts on, and a ping that repeats the same
# thing every week is one nobody reads. CLAUDE.md §10.17.
#
# Scheduled by com.example.epub-reader.upgrade-check (Saturdays 05:37).
# Must stay bash 3.2-compatible: that is what launchd runs on macOS.
# =============================================================================
set -u
cd "$(dirname "$0")"

LOG="upgrade-check.log"
KEEP_LINES=3000

{
    echo
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
    python3 tools/upgrade.py --check 2>&1
} >> "$LOG"
status=$?

if [ "$status" -ne 0 ]; then
    # Read THIS run only — a crashed run must not borrow last week's summary.
    last=$(awk '/^===== /{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG")
    summary=$(printf '%s' "$last" | grep '^Attention:' | tail -1 | sed 's/^Attention: //')
    fresh=$(printf '%s' "$last" | grep -c '^NEW since last check:')
    msg="${summary:-check failed — see upgrade-check.log}"
    [ "$fresh" -gt 0 ] && msg="$fresh new item(s). $msg"
    osascript -e "display notification \"$msg — see the Attention list in upgrade-check.log\" with title \"epub-reader — needs attention\"" \
        >/dev/null 2>&1 || true
fi

if [ "$(wc -l < "$LOG")" -gt "$KEEP_LINES" ]; then
    tail -n "$KEEP_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exit 0
