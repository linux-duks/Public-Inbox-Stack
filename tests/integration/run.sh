#!/usr/bin/env bash
# End-to-end integration tests for mirroring, indexing and serving.
#
# Scenarios (all use the real controller image against a generated upstream):
#
#   baseline      N lists mirrored -> indexed -> served
#   incremental   after the first cycle, add lists and messages to the
#                 upstream; the second pull/index cycle must pick both up and
#                 the mirror must stay consistent with the upstream
#   concurrency   a short pull interval makes grok-pull run while an index or
#                 extindex is still running; the stack must stay healthy and
#                 converge
#
# Usage:
#
#   ./run.sh                      # baseline only (LIST_COUNT, default 360)
#   ./run.sh baseline             # same
#   ./run.sh incremental          # incremental scenario
#   ./run.sh concurrency          # concurrency scenario
#   ./run.sh full                 # all three (large scale)
#   ./run.sh smoke                # all three (small scale)
#   ./run.sh check                # re-run assertions on a KEEP=1 stack
#   ./run.sh clean                # remove containers and state
#
#   LIST_COUNT=20 ./run.sh        # smaller baseline
#   INC_LIST_COUNT=20 ADD_LIST_COUNT=5 NEW_EMAILS_PER_LIST=2 ./run.sh incremental
#   CONC_LIST_COUNT=30 CONC_INTERVAL=3 ./run.sh concurrency
#   KEEP=1 ./run.sh               # leave the last stack up
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

# -- scenario scale --------------------------------------------------------
# Per scenario and profile: "<lists> <added lists> <new messages> <interval>".
# Any of them can be overridden with the env variables listed below.
profile_values() { # profile_values <scenario>
	case "${PROFILE}:$1" in
		full:baseline) echo "360 0 0 3600" ;;
		smoke:baseline) echo "20 0 0 3600" ;;
		full:incremental) echo "40 10 2 60" ;;
		smoke:incremental) echo "20 5 2 60" ;;
		full:concurrency) echo "150 0 0 3" ;;
		smoke:concurrency) echo "60 0 0 3" ;;
		*) return 1 ;;
	esac
}

scenario_scale() { # scenario_scale <scenario>; sets SCALE_LISTS/_ADD/_NEW/_INTERVAL
	local values
	values=$(profile_values "$1") ||
		die "unknown scenario '$1' (expected baseline, incremental or concurrency)"
	read -r SCALE_LISTS SCALE_ADD SCALE_NEW SCALE_INTERVAL <<<"$values"

	case "$1" in
		baseline)
			SCALE_LISTS=${LIST_COUNT:-$SCALE_LISTS}
			;;
		incremental)
			SCALE_LISTS=${INC_LIST_COUNT:-$SCALE_LISTS}
			SCALE_ADD=${ADD_LIST_COUNT:-$SCALE_ADD}
			SCALE_NEW=${NEW_EMAILS_PER_LIST:-$SCALE_NEW}
			SCALE_INTERVAL=${INC_INTERVAL:-$SCALE_INTERVAL}
			;;
		concurrency)
			SCALE_LISTS=${CONC_LIST_COUNT:-$SCALE_LISTS}
			SCALE_INTERVAL=${CONC_INTERVAL:-$SCALE_INTERVAL}
			;;
	esac
}

INDEX_LEVEL=${INDEX_LEVEL:-basic}
ITEST_TIMEOUT=${ITEST_TIMEOUT:-2400}
ITEST_OVERLAP_TIMEOUT=${ITEST_OVERLAP_TIMEOUT:-180}
ITEST_HTTP_RETRY_TIMEOUT=${ITEST_HTTP_RETRY_TIMEOUT:-30}
KEEP=${KEEP:-0}
SKIP_BUILD=${SKIP_BUILD:-0}
STATE_DIR=${STATE_DIR:-$PWD/.state}
ITEST_IMAGE=${ITEST_IMAGE:-pistack-integration:local}
MESSAGES_PER_LIST=${MESSAGES_PER_LIST:-3}
# Seconds to wait for the upstream to seed+serve and for the controller to
# expose /state. Overridable for slow machines / large LIST_COUNT.
UPSTREAM_TIMEOUT=${UPSTREAM_TIMEOUT:-1800}
CONTROLLER_TIMEOUT=${CONTROLLER_TIMEOUT:-600}
POLL_INTERVAL=${POLL_INTERVAL:-5}
STATUS_INTERVAL=${STATUS_INTERVAL:-30}

# Image is built once per run.sh invocation, not once per scenario.
IMAGE_BUILT=0

GID=${GID:-$(id -g)}
export MESSAGES_PER_LIST INDEX_LEVEL ITEST_TIMEOUT ITEST_OVERLAP_TIMEOUT
export ITEST_HTTP_RETRY_TIMEOUT

export STATE_DIR ITEST_IMAGE POLL_INTERVAL STATUS_INTERVAL
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

prepare_state() { # prepare_state <pull-interval-seconds>
	log "Preparing $STATE_DIR"
	rm -rf "$STATE_DIR"
	mkdir -p "$STATE_DIR/upstream" "$STATE_DIR/data" "$STATE_DIR/configs" "$STATE_DIR/env"
	{
		printf 'INDEXING_LEVEL=%s\n' "$INDEX_LEVEL"
		printf 'MIRRORING_0_INTERVAL=%s\n' "$1"
	} >"$STATE_DIR/env/integration.env"
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
	local total=${SCALE_LISTS:-0} done_count
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

# -- stack lifecycle -------------------------------------------------------

ensure_image() {
	if ((IMAGE_BUILT)); then
		return 0
	fi
	if [[ $SKIP_BUILD == 1 ]]; then
		log "Skipping image build (SKIP_BUILD=1)"
	else
		log "Building $ITEST_IMAGE"
		compose build
	fi
	IMAGE_BUILT=1
}

# Start the upstream and the controller with the given pull interval.
stack_up() { # stack_up <lists> <interval>
	local lists=$1 interval=$2
	prepare_state "$interval"

	log "Starting upstream ($lists lists x $MESSAGES_PER_LIST messages)"
	log "The upstream generates all inboxes first (a few minutes for $lists);"
	log "follow its progress with: $COMPOSE ${COMPOSE_FILES[*]} logs -f upstream"
	compose up -d --remove-orphans upstream
	wait_for "upstream manifest" "$UPSTREAM_TIMEOUT" upstream upstream_ready

	log "Starting the controller (mirror + index $lists lists, pull every ${interval}s)"
	compose up -d --remove-orphans public-inbox-stack
	wait_for "controller /state" "$CONTROLLER_TIMEOUT" public-inbox-stack supervisor_ready
}

# Assertions run inside the controller container, where the data, configs and
# both HTTP endpoints are reachable.
checks() {
	compose exec -T public-inbox-stack python3 /checks/checks.py "$@"
}

# -- scenarios -------------------------------------------------------------

scenario_baseline() {
	scenario_scale baseline
	log "Scenario 'baseline': $SCALE_LISTS lists"
	ensure_image
	stack_up "$SCALE_LISTS" 3600
	log "Asserting mirroring, indexing and serving"
	checks --list-count "$SCALE_LISTS" --timeout "$ITEST_TIMEOUT"
}

scenario_incremental() {
	scenario_scale incremental
	local total=$((SCALE_LISTS + SCALE_ADD))
	log "Scenario 'incremental': $SCALE_LISTS lists, then +$SCALE_ADD lists and"
	log "  +$SCALE_NEW message(s) per existing list (pull every ${SCALE_INTERVAL}s)"
	ensure_image
	stack_up "$SCALE_LISTS" "$SCALE_INTERVAL"

	log "Phase 1: the initial lists must be served before mutating"
	checks --list-count "$SCALE_LISTS" --timeout "$ITEST_TIMEOUT"

	log "Phase 2: mutating the upstream (+$SCALE_ADD lists, +$SCALE_NEW msgs/list)"
	compose exec -T upstream perl /seed/mutate.pl \
		--baseline-count "$SCALE_LISTS" \
		--add-lists "$SCALE_ADD" \
		--add-emails "$SCALE_NEW"

	log "Phase 3: waiting for the second pull/index/extindex cycle"
	checks --list-count "$total" \
		--baseline-count "$SCALE_LISTS" \
		--new-emails "$SCALE_NEW" \
		--min-cycle 2 \
		--consistency \
		--timeout "$ITEST_TIMEOUT"
}

scenario_concurrency() {
	scenario_scale concurrency
	local new=${CONC_NEW_EMAILS:-2}
	log "Scenario 'concurrency': $SCALE_LISTS lists, pull every ${SCALE_INTERVAL}s"
	ensure_image
	stack_up "$SCALE_LISTS" "$SCALE_INTERVAL"

	log "Phase 1: waiting for a grok-pull to overlap an index/extindex run"
	checks --overlap-detect --overlap-timeout "$ITEST_OVERLAP_TIMEOUT"

	# Mutating while pulls keep running forces the pull to actually fetch (it
	# holds the repository locks) during an index pass, which is the window the
	# indexer has to survive.
	log "Phase 2: mutating the upstream (+$new messages per list) under load"
	compose exec -T upstream perl /seed/mutate.pl \
		--baseline-count "$SCALE_LISTS" \
		--add-lists 0 \
		--add-emails "$new"

	log "Phase 3: the stack must still converge, re-index and match the upstream"
	checks --list-count "$SCALE_LISTS" \
		--new-emails "$new" \
		--consistency \
		--timeout "$ITEST_TIMEOUT"
}

run_scenarios() { # run_scenarios <profile> <scenario...>
	PROFILE=$1
	shift
	(( $# > 0 )) || die "no scenarios requested"

	local scenario index=0
	for scenario in "$@"; do
		if ((index > 0)); then
			log "Tearing down before scenario '$scenario'"
			compose down --remove-orphans >/dev/null 2>&1 || true
		fi
		"scenario_$scenario"
		index=$((index + 1))
	done
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

# Re-run the assertions against a stack left up by `KEEP=1 run`.
#
# CHECK_* mirror the scenario knobs so a kept incremental/concurrency stack can
# be re-checked without restarting it:
#   CHECK_LIST_COUNT, CHECK_BASELINE_COUNT, CHECK_NEW_EMAILS, CHECK_CONSISTENCY
check_only() {
	local lists=${CHECK_LIST_COUNT:-${LIST_COUNT:-360}}
	local args=(--list-count "$lists" --no-wait)
	[[ -n ${CHECK_BASELINE_COUNT:-} ]] && args+=(--baseline-count "$CHECK_BASELINE_COUNT")
	[[ -n ${CHECK_NEW_EMAILS:-} ]] && args+=(--new-emails "$CHECK_NEW_EMAILS")
	[[ ${CHECK_CONSISTENCY:-0} == 1 ]] && args+=(--consistency)
	log "Re-checking $lists lists (no waiting)"
	checks "${args[@]}"
}

# Remove the stack and its state (containers are not deleted on KEEP=1 runs).
clean_all() {
	log "Removing the integration-test stack and $STATE_DIR"
	compose down --remove-orphans >/dev/null 2>&1 || true
	rm -rf "$STATE_DIR"
}

# Allow the helpers to be sourced by tests without running the whole suite.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	trap cleanup EXIT
	case ${1:-run} in
		run | baseline) run_scenarios full baseline ;;
		incremental) run_scenarios full incremental ;;
		concurrency) run_scenarios full concurrency ;;
		full) run_scenarios full baseline incremental concurrency ;;
		smoke) run_scenarios smoke baseline incremental concurrency ;;
		check) check_only ;;
		clean) clean_all ;;
		*) die "unknown mode '${1}' (expected: run, baseline, incremental, concurrency, full, smoke, check or clean)" ;;
	esac
fi
