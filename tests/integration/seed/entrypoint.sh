#!/bin/bash
# Entrypoint of the integration-test "upstream" container.
#
# Seeds a throwaway public-inbox instance with LIST_COUNT generated v2 inboxes
# (once per /data volume) and then serves it with public-inbox-netd, so the
# stack under test can mirror it exactly like lore.kernel.org.
set -euo pipefail

LIST_COUNT=${LIST_COUNT:-360}
MESSAGES_PER_LIST=${MESSAGES_PER_LIST:-3}
SEED_ROOT=${SEED_ROOT:-/data}
LISTEN_PORT=${LISTEN_PORT:-8080}
export LIST_COUNT MESSAGES_PER_LIST SEED_ROOT

mkdir -p "$SEED_ROOT"

# Fail with an actionable message instead of a stray EACCES deep inside perl:
# the bind-mounted state dir must be writable by the uid this container runs
# as.  Rootless podman maps that uid to a subordinate host uid unless
# userns_mode: keep-id is set (run.sh adds compose.podman.yaml for podman).
if ! { : >"$SEED_ROOT/.write-test"; } 2>/dev/null; then
	cat >&2 <<-EOM
		ERROR: ${SEED_ROOT} is not writable as uid $(id -u):$(id -g).
		       The integration test bind-mounts the host state dir into the
		       container, so the container uid must match the owner of
		       tests/integration/.state (run.sh creates it).
		       With rootless podman this needs userns_mode: keep-id -- run
		       ./run.sh (it adds compose.podman.yaml automatically) instead
		       of calling podman-compose up directly.
	EOM
	exit 1
fi
rm -f "$SEED_ROOT/.write-test"

if [[ ! -f "$SEED_ROOT/.seeded" ]]; then
	echo "==> Seeding ${LIST_COUNT} inboxes in ${SEED_ROOT}"
	perl /seed/generate_lists.pl
	touch "$SEED_ROOT/.seeded"
else
	echo "==> Re-using ${SEED_ROOT} (already seeded)"
fi

echo "==> Serving ${SEED_ROOT} on http://0.0.0.0:${LISTEN_PORT}"
exec public-inbox-netd -l "http://0.0.0.0:${LISTEN_PORT}"
