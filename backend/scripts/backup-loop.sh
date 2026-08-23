#!/bin/sh
# §10's schedule (I9). A loop, not host cron: the deployment has to be one
# directory and one `docker compose up`, and a crontab on the host is a
# second thing to install and a second thing to forget.
#
# Two cadences. Backups are cheap and frequent; a drill restores a whole
# database and is worth an interval of its own.
set -eu

BACKUP_DIR="${BUDGE_BACKUP_DIR:-/backups}"
BACKUP_INTERVAL="${BUDGE_BACKUP_INTERVAL:-3600}"
DRILL_INTERVAL="${BUDGE_DRILL_INTERVAL:-86400}"

since_drill=0

echo "budge backup loop: every ${BACKUP_INTERVAL}s, drill every ${DRILL_INTERVAL}s, into ${BACKUP_DIR}"

while true; do
	# `|| true` on the backup, deliberately: one failed run must not stop
	# the loop, or a transient database blip would end all backups until
	# somebody noticed the container had exited. The failure is on stdout
	# either way, and the drill is what turns a run of bad backups into a
	# loud signal.
	if podvinsya backup --to "${BACKUP_DIR}"; then
		echo "backup ok"
	else
		echo "BACKUP FAILED (exit $?)" >&2
	fi

	since_drill=$((since_drill + BACKUP_INTERVAL))
	if [ "${since_drill}" -ge "${DRILL_INTERVAL}" ]; then
		since_drill=0
		if podvinsya restore-drill --from "${BACKUP_DIR}"; then
			echo "restore drill ok"
		else
			# The one message an operator must never learn to ignore.
			echo "RESTORE DRILL FAILED — the backups are not restorable" >&2
		fi
	fi

	sleep "${BACKUP_INTERVAL}"
done
