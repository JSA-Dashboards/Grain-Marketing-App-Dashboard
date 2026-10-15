#!/usr/bin/env bash
# run_job.sh <futures|bids|weather> -- one Grain Marketing Desk nightly job on the droplet,
# invoked by cron through /opt/alerting/cron-alert (which emails on a non-zero exit).
#
#   futures + bids  6:10 / 6:15 PM Central, Monday to Friday (after JSA's evening futures load)
#   weather         5:30 AM Central, every day
#
# Each job rewrites its own market/* document in Snowflake JSA.GRAIN_DESK, so a missed run costs nothing.
set -uo pipefail

JOB="${1:?usage: run_job.sh futures|bids|weather}"
APP_DIR="/opt/grain-desk"
LOG_DIR="$APP_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${JOB}_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

# Single-instance guard per job
exec 9>"$LOG_DIR/.$JOB.lock"
if ! flock -n 9; then
    echo "$(date -Is) $JOB: another run is in progress -- skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== $JOB start $(date -Is) ==="
    git pull --quiet
    set -a; . "$APP_DIR/.env"; set +a
    cd "$APP_DIR/hosted/server" && "$APP_DIR/.venv/bin/python" -m app.jobs.scheduler "$JOB"
    rc=$?
    echo "=== $JOB end rc=$rc $(date -Is) ==="
} >>"$LOG" 2>&1

# keep two weeks of logs
find "$LOG_DIR" -name '*.log' -mtime +14 -delete 2>/dev/null
exit $rc
