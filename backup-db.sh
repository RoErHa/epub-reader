#!/usr/bin/env bash
# =============================================================================
# backup-db.sh — the weekly snapshot of books.db (CLAUDE.md §10.19).
#
# Runs tools/backup_db.py and appends the result to backup-db.log. A failure
# raises a macOS notification: a log nobody opens is a backup nobody knows has
# stopped. A success stays quiet. The message is fixed text, so nothing a run
# produced is ever pasted into AppleScript.
#
# Scheduled by com.example.epub-reader.backup (Sundays 03:47).
# Must stay bash 3.2-compatible: that is what launchd runs on macOS.
# =============================================================================
set -u
cd "$(dirname "$0")"

LOG="backup-db.log"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
    python3 tools/backup_db.py 2>&1
} >> "$LOG"
status=$?

if [ "$status" -ne 0 ]; then
    echo "FAILED: exit $status" >> "$LOG"
    osascript -e 'display notification "The weekly database backup FAILED. Existing snapshots are untouched; see backup-db.log." with title "epub-reader — needs attention"' \
        >/dev/null 2>&1 || true
fi
exit "$status"
