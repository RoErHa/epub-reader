#!/usr/bin/env bash
# =============================================================================
# run.sh — start / stop / restart the epub-reader service.
#
# Every start and restart FIRST scans the library folder and reports what has
# appeared, changed or vanished since the last run, so books dropped into
# LIBRARY_ROOT are picked up without you having to remember a separate step.
# Set REPAIR_ON_SCAN=true in .env and that scan also checks the authors and
# titles of what it found.
#
# All configuration comes from the .env file — this script sets nothing itself
# and passes no settings to the app. Secrets never appear here.
#
# It is launchd-aware. When com.roha.epub-reader is loaded, start/stop/restart
# drive launchctl so this script and the LaunchAgent can never both bind :8770.
# Otherwise it manages a background process with a pidfile under .run/.
#
# Usage:
#   ./run.sh start              # scan, then start in the background
#   ./run.sh start --foreground # scan, then run in this terminal (Ctrl-C quits)
#   ./run.sh restart            # scan, then stop and start again
#   ./run.sh stop
#   ./run.sh status
#   ./run.sh scan               # scan only, don't touch the service
#   ./run.sh repair             # check authors and titles, change nothing
#   ./run.sh repair --apply     # ...and fix what it can prove
#   ./run.sh --env .env.test start
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

LABEL="com.roha.epub-reader"
PYTHON=".venv/bin/python"
RUN_DIR=".run"
ENV_FILE=".env"
PID_FILE=""   # derived from the env file once arguments are parsed

# ── Argument handling ────────────────────────────────────────────────────────
FOREGROUND=0
COMMAND=""
APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env)        ENV_FILE="${2:?--env needs a path}"; shift 2 ;;
    --foreground|-f) FOREGROUND=1; shift ;;
    --apply)      APPLY=1; shift ;;
    start|stop|restart|status|scan|repair|help)
                  COMMAND="$1"; shift ;;
    -h|--help)    COMMAND="help"; shift ;;
    *)            echo "✗  Unknown command or option: $1" >&2
                  echo "   Run './run.sh help' for usage." >&2
                  exit 2 ;;
  esac
done

# One pidfile per configuration: a --env pointing at a different port is
# a different service, and must not see (or stop) the live one.
PID_FILE="${RUN_DIR}/$(basename "${ENV_FILE}").pid"

usage() {
  sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'
}

if [ -z "${COMMAND}" ]; then
  echo "✗  No command given." >&2
  echo >&2
  usage >&2
  exit 2
fi

if [ "${COMMAND}" = "help" ]; then
  usage
  exit 0
fi

# ── Preconditions ────────────────────────────────────────────────────────────
if [ ! -x "${PYTHON}" ]; then
  echo "✗  ${PYTHON} not found." >&2
  echo "   Create the virtualenv first:" >&2
  echo "     python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -f "${ENV_FILE}" ]; then
  echo "✗  Configuration file not found: ${ENV_FILE}" >&2
  echo "   Copy .env.example to .env and set LIBRARY_ROOT." >&2
  exit 1
fi

launchd_loaded() {
  launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1
}

running_pid() {
  [ -f "${PID_FILE}" ] || return 1
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [ -n "${pid}" ] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  echo "${pid}"
}

# ── Commands ─────────────────────────────────────────────────────────────────
do_scan() {
  echo "▶  Scanning library…"
  "${PYTHON}" epub_gateway.py --env "${ENV_FILE}" --scan
}

do_repair() {
  # Looking is the default; --apply is what changes anything.
  if [ "${APPLY}" -eq 1 ]; then
    echo "▶  Checking authors and titles, and fixing what is safe to fix…"
    "${PYTHON}" epub_gateway.py --env "${ENV_FILE}" --repair --apply
  else
    echo "▶  Checking authors and titles (nothing will be changed)…"
    "${PYTHON}" epub_gateway.py --env "${ENV_FILE}" --repair
  fi
}

do_start() {
  if launchd_loaded; then
    echo "▶  ${LABEL} is managed by launchd — starting it there."
    launchctl kickstart "gui/$(id -u)/${LABEL}"
    echo "✓  Started under launchd."
    return 0
  fi

  if pid="$(running_pid)"; then
    echo "✗  Already running (pid ${pid}). Use './run.sh restart'." >&2
    return 1
  fi

  if [ "${FOREGROUND}" -eq 1 ]; then
    echo "▶  Starting in the foreground — Ctrl-C to quit."
    exec "${PYTHON}" epub_gateway.py --env "${ENV_FILE}"
  fi

  mkdir -p "${RUN_DIR}"
  nohup "${PYTHON}" epub_gateway.py --env "${ENV_FILE}" >>"${RUN_DIR}/stdout.log" 2>&1 &
  echo $! >"${PID_FILE}"
  sleep 1
  if pid="$(running_pid)"; then
    echo "✓  Started (pid ${pid}). Logs: ${RUN_DIR}/stdout.log"
  else
    echo "✗  Failed to start — see ${RUN_DIR}/stdout.log" >&2
    rm -f "${PID_FILE}"
    return 1
  fi
}

do_stop() {
  if launchd_loaded; then
    echo "▶  Stopping the launchd job."
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    echo "✓  Stopped."
    return 0
  fi

  if ! pid="$(running_pid)"; then
    # Already stopped is the desired end state, not an error.
    echo "✓  Not running."
    rm -f "${PID_FILE}"
    return 0
  fi

  kill "${pid}" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "${pid}" 2>/dev/null; then
    echo "   Still up after 5 s — sending SIGKILL."
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
  echo "✓  Stopped."
}

do_status() {
  if launchd_loaded; then
    echo "✓  Running under launchd (${LABEL})."
    return 0
  fi
  if pid="$(running_pid)"; then
    echo "✓  Running (pid ${pid})."
    return 0
  fi
  echo "✗  Not running."
  return 3
}

case "${COMMAND}" in
  scan)    do_scan ;;
  repair)  do_repair ;;
  start)   do_scan; do_start ;;
  restart) do_stop;  do_scan; do_start ;;
  stop)    do_stop ;;
  status)  do_status ;;
esac
