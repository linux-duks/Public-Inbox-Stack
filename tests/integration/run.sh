#!/usr/bin/env bash
# End-to-end integration test: mirror a large generated public-inbox upstream
# and assert every list is indexed and served.
#
#   ./run.sh                      # 360 lists (default)
#   LIST_COUNT=20 ./run.sh        # small/fast run
#   KEEP=1 ./run.sh               # leave the stack up afterwards
#   SKIP_BUILD=1 ./run.sh         # reuse an existing image
#   COMPOSE="podman-compose" ./run.sh   # force a specific compose command
#
# Works with `docker compose` (v2) and `podman-compose`; the podman-specific
# overrides are added automatically (see compose.podman.yaml).
#
# The heavy lifting (waiting + assertions) lives in checks.py, which runs
# inside the controller container so it sees the same files and endpoints the
# controller does.
set -euo pipefail

cd "$(dirname "$0")"

LIST_COUNT=${LIST_COUNT:-360}
MESSAGES_PER_LIST=${MESSAGES_PER_LIST:-3}
INDEX_LEVEL=${INDEX_LEVEL:-basic}
ITEST_TIMEOUT=${ITEST_TIMEOUT:-2400}
KEEP=${KEEP:-0}
SKIP_BUILD=${SKIP_BUILD:-0}
STATE_DIR=${STATE_DIR:-$PWD/.state}
ITEST_IMAGE=${ITEST_IMAGE:-pistack-integration:local}
# Seconds to wait for the upstream to seed+serve and for the controller to
# expose /state. Overridable for slow machines / large LIST_COUNT.
UPSTREAM_TIMEOUT=${UPSTREAM_TIMEOUT:-1800}
CONTROLLER_TIMEOUT=${CONTROLLER_TIMEOUT:-600}
POLL_INTERVAL=${POLL_INTERVAL:-5}
STATUS_INTERVAL=${STATUS_INTERVAL:-30}

GID=${GID:-$(id -g)}
export LIST_COUNT MESSAGES_PER_LIST INDEX_LEVEL ITEST_TIMEOUT STATE_DIR ITEST_IMAGE
export POLL_INTERVAL STATUS_INTERVAL
export UID GID

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# -- compose command -------------------------------------------------------

if [[ -z ${COMPOSE:-} ]]; then
	if docker compose version >/dev/null 2>&1; then
		COMPOSE="docker compose"
	elif command -v docker-compose >/dev/null 2>&1; then
		COMPOSE="docker-compose"
	elif command -v podman-compose >/dev/null 2>&1; then
		COMPOSE="podman-compose"
	else
		die "no usable compose command (need 'docker compose' v2, docker-compose or podman-compose)"
	fi
fi
read -r -a COMPOSE_CMD <<<"$COMPOSE"

# Compose files: the base file plus the podman-only overrides. Rootless podman
# needs `userns_mode: keep-id` so the in-container user can write the
# bind-mounted state dirs; Docker and rootful podman reject that key, hence the
# separate file.
podman_is_rootful() {
	# `--userns=keep-id` is rootless-only, and podman cannot change mode per
	# invocation: a rootful podman is one started by root, i.e. this test
	# running as root.
	[[ $(id -u) -eq 0 ]]
}

COMPOSE_FILES=(-f compose.yaml)
if [[ $COMPOSE == *podman* ]]; then
	if podman_is_rootful; then
		log "podman runs as root: not using compose.podman.yaml (keep-id is rootless-only)"
	else
		COMPOSE_FILES+=(-f compose.podman.yaml)
	fi
fi
if [[ -n ${COMPOSE_EXTRA_FILES:-} ]]; then
	read -r -a _extra <<<"$COMPOSE_EXTRA_FILES"
	COMPOSE_FILES+=("${_extra[@]}")
fi

compose() { "${COMPOSE_CMD[@]}" "${COMPOSE_FILES[@]}" "$@"; }

# `podman-compose ps` always lists exited containers itself and rejects `-a`,
# while `docker compose ps` needs it to show them.
PS_FLAGS=()
if [[ $COMPOSE != *podman* ]]; then
	PS_FLAGS=(-a)
fi

# -- state ----------------------------------------------------------------

prepare_state() {
	log "Preparing $STATE_DIR"
	rm -rf "$STATE_DIR"
	mkdir -p "$STATE_DIR/upstream" "$STATE_DIR/data" "$STATE_DIR/configs" "$STATE_DIR/env"
	printf 'INDEXING_LEVEL=%s\n' "$INDEX_LEVEL" >"$STATE_DIR/env/integration.env"
}

# One line per container of the service, e.g. "pistack-integration_upstream_1 Up 3 seconds".
service_status() { # service_status <service>
	compose ps "${PS_FLAGS[@]}" --format '{{.Names}} {{.Status}}' 2>/dev/null |
		grep -F -- "$1" || true
}

# True when the service's container is no longer running (exited/dead/missing).
service_dead() { # service_dead <service>
	local line
	line=$(service_status "$1")
	[[ -z $line ]] && return 0
	grep -Eq 'Exited|Dead' <<<"$line"
}

# Seeding takes a while (a few hundred inboxes are created on disk), and its
# progress only shows up in the container logs. The state dir is a host bind
# mount, so count the generated inboxes directly to keep the wait informative.
seed_progress() {
	local total=${LIST_COUNT:-0} done_count
	[[ -d $STATE_DIR/upstream ]] || return 0
	done_count=$(find "$STATE_DIR/upstream" -maxdepth 1 -type d -name 'list_*' \
		2>/dev/null | wc -l | tr -d ' ')
	printf ' · %s/%s lists generated' "${done_count:-0}" "$total"
}

wait_for() { # wait_for <label> <timeout-seconds> <service> <command...>
	local label=$1 timeout=$2 service=$3
	shift 3
	local started deadline now last status progress
	started=$(date +%s)
	deadline=$((started + timeout))
	last=$started

	until "$@" >/dev/null 2>&1; do
		# A container that died will never become ready: stop waiting and let
		# the EXIT trap dump its logs instead of burning the whole timeout.
		if service_dead "$service"; then
			status=$(service_status "$service")
			printf 'ERROR: %s container is not running: %s\n' \
				"$service" "${status:-<missing>}" >&2
			return 1
		fi

		now=$(date +%s)
		if ((now >= deadline)); then
			printf 'ERROR: timed out after %ss waiting for %s\n' "$timeout" "$label" >&2
			return 1
		fi
		if ((now - last >= STATUS_INTERVAL)); then
			status=$(service_status "$service")
			progress=""
			if [[ $service == upstream ]]; then
				progress=$(seed_progress)
			fi
			log "still waiting for $label ($((deadline - now))s left) [${status:-container missing}${progress}]"
			last=$now
		fi
		sleep "$POLL_INTERVAL"
	done
	log "$label ready after $(( $(date +%s) - started ))s"
}

upstream_ready() {
	compose exec -T upstream curl -fsS -o /dev/null http://localhost:8080/manifest.js.gz
}

supervisor_ready() {
	compose exec -T public-inbox-stack curl -fsS -o /dev/null http://localhost:8081/state
}

# -- diagnostics / teardown ------------------------------------------------

collect_diagnostics() {
	local out="$STATE_DIR/failure.log"
	log "Collecting diagnostics into $out"
	{
		echo "=== compose command: $COMPOSE ${COMPOSE_FILES[*]}"
		echo "=== containers"
		compose ps "${PS_FLAGS[@]}" --format '{{.Names}} {{.Status}}' || true
		echo
		echo "=== supervisor /state"
		compose exec -T public-inbox-stack curl -sS http://localhost:8081/state || true
		echo
		echo "=== rendered public-inbox config (inbox sections)"
		compose exec -T public-inbox-stack \
			git config -f /configs/pi-configs/config --get-regexp \
			'^publicinbox\..*\.inboxdir$' || true
		echo
		echo "=== mirror toplevel"
		compose exec -T public-inbox-stack sh -c \
			'ls /data/mirror/upstream 2>/dev/null | wc -l' || true
		echo
		echo "=== tinysupervisor task logs (last 60 lines each)"
		compose exec -T public-inbox-stack sh -c \
			'for f in /tmp/tinysup/log/*/*.log; do echo "--- $f"; tail -n 60 "$f"; done' || true
		echo
		echo "=== controller logs (tail 200)"
		compose logs --no-color --tail 200 public-inbox-stack || true
		echo
		echo "=== upstream logs (tail 200)"
		compose logs --no-color --tail 200 upstream || true
	} >"$out" 2>&1 || true
}

cleanup() {
	local rc=$?
	if ((rc != 0)); then
		collect_diagnostics || true
		echo
		log "Last 80 lines of $STATE_DIR/failure.log:"
		tail -n 80 "$STATE_DIR/failure.log" 2>/dev/null || true
	fi

	if [[ $KEEP == 1 ]]; then
		log "KEEP=1: leaving the stack running"
		log "  re-run assertions: (cd tests/integration && make integration-check)"
		log "  inspect:           $COMPOSE ${COMPOSE_FILES[*]} exec -T public-inbox-stack sh"
		log "  logs:              $COMPOSE ${COMPOSE_FILES[*]} logs -f"
		log "  stop:              (cd tests/integration && make integration-test-clean)"
	else
		log "Tearing down"
		compose down --remove-orphans >/dev/null 2>&1 || true
	fi
	exit $rc
}

main() {
	trap cleanup EXIT

	prepare_state

	if [[ $SKIP_BUILD != 1 ]]; then
		log "Building $ITEST_IMAGE"
		compose build
	else
		log "Skipping image build (SKIP_BUILD=1)"
	fi

	log "Starting upstream ($LIST_COUNT lists x $MESSAGES_PER_LIST messages)"
	log "The upstream generates all inboxes first (a few minutes for $LIST_COUNT);"
	log "follow its progress with: $COMPOSE ${COMPOSE_FILES[*]} logs -f upstream"
	compose up -d --remove-orphans upstream
	wait_for "upstream manifest" "$UPSTREAM_TIMEOUT" upstream upstream_ready

	log "Starting the controller (mirror + index $LIST_COUNT lists)"
	compose up -d --remove-orphans public-inbox-stack
	wait_for "controller /state" "$CONTROLLER_TIMEOUT" public-inbox-stack supervisor_ready

	log "Asserting mirroring, indexing and serving"
	compose exec -T public-inbox-stack python3 /checks/checks.py \
		--list-count "$LIST_COUNT" \
		--timeout "$ITEST_TIMEOUT"
}

# Re-run the assertions against a stack left up by `KEEP=1 run`.
check_only() {
	log "Re-checking $LIST_COUNT lists (no waiting)"
	compose exec -T public-inbox-stack python3 /checks/checks.py \
		--list-count "$LIST_COUNT" \
		--no-wait
}

# Remove the stack and its state (containers are not deleted on KEEP=1 runs).
clean_all() {
	log "Removing the integration-test stack and $STATE_DIR"
	compose down --remove-orphans >/dev/null 2>&1 || true
	rm -rf "$STATE_DIR"
}

# Allow the helpers to be sourced by tests without running the whole suite.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	case ${1:-run} in
		run) main "$@" ;;
		check) check_only ;;
		clean) clean_all ;;
		*) die "unknown mode '${1}' (expected: run, check or clean)" ;;
	esac
fi
