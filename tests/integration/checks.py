#!/usr/bin/env python3
"""Assertions for the mirroring/indexing integration test.

This script runs *inside* the controller container (it is mounted at
``/checks/checks.py``), because that container can reach the upstream, the
served lore instance and the data/config directories the controller wrote.

It waits for indexing to settle and then verifies, layer by layer:

  1. upstream   the generated upstream serves a manifest for every inbox
  2. supervisor the tinySupervisor DAG ran the pull/index/extindex tasks
  3. mirror     grok-pull cloned every inbox under /data/mirror/upstream
  4. index      every inbox was initialized and indexed
  5. config     every inbox is in the rendered public-inbox config
  6. http       every inbox is served by the running lore instance

Optional layers for the incremental and concurrency scenarios:

  --baseline-count/--list-count  only the *new* lists added by mutate.pl
  --new-emails                   new messages in pre-existing lists
  --consistency                  mirror refs still match the upstream
  --overlap-detect               a grok-pull ran while an index was running

Exit code 0 means every layer passed; 1 means at least one layer failed (the
failure list is printed at the end, and the layers are printed as they fail so
a timeout still shows what was wrong).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

DEFAULT_UPSTREAM = "http://upstream:8080"
DEFAULT_SUPERVISOR = "http://localhost:8081"
DEFAULT_SERVED = "http://localhost:8080"
DEFAULT_MIRROR = "/data/mirror/upstream"
DEFAULT_PI_CONFIG = "/configs/pi-configs/config"

#: Tasks that must show up in tinySupervisor's /state for this scenario.
REQUIRED_TASKS = (
    "init_folders",
    "grokmirror_upstream",
    "index_upstream",
    "grok_extindex",
    "ensure_inboxes",
    "public-inbox-netd",
)

#: Tasks that must have run at least once.
RUN_AT_LEAST_ONCE = ("grokmirror_upstream", "index_upstream", "grok_extindex")

#: Long-running tasks that must be up.
RUNNING_TASKS = ("public-inbox-netd",)

#: Process states that mean "the task is doing work right now".
BUSY_STATES = ("starting", "running")

#: Refs that must match the upstream after a pull (epoch contents + origins).
CONSISTENT_REF_PREFIXES = ("refs/heads/", "refs/meta/")


class Failure(Exception):
    """A single assertion failure, with the detail to print."""


def http_get(url: str, timeout: float = 30.0) -> tuple[int, bytes]:
    """GET ``url`` and return ``(status, body)``; HTTP errors keep the body."""
    if not url.startswith(("http://", "https://")):
        raise Failure(f"refusing to fetch non-HTTP URL: {url}")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url, headers={"User-Agent": "pistack-itest"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - one failure mode
        return exc.code, exc.read()


def http_json(url: str) -> dict:
    status, body = http_get(url)
    if status != 200:
        raise Failure(f"GET {url} -> HTTP {status}")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise Failure(f"GET {url} returned invalid JSON: {exc}") from exc


def git_config_regexp(config_path: str, pattern: str) -> list[str]:
    """Return the ``key value`` lines matching ``pattern`` in a git config."""
    result = subprocess.run(
        ["git", "config", "-f", config_path, "--get-regexp", pattern],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):  # 1 == no match
        raise Failure(f"git config --get-regexp {pattern!r} failed: {result.stderr}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def config_inbox_names(config_path: str) -> set[str]:
    prefix, suffix = "publicinbox.", ".inboxdir"
    names: set[str] = set()
    for line in git_config_regexp(config_path, r"^publicinbox\..*\.inboxdir$"):
        key = line.split(None, 1)[0]
        if key.startswith(prefix) and key.endswith(suffix):
            names.add(key[len(prefix) : -len(suffix)])
    return names


def supervisor_state(url: str) -> dict:
    payload = http_json(f"{url}/state")
    processes = payload.get("processes")
    if not isinstance(processes, dict):
        raise Failure(f"{url}/state has no 'processes' map: {payload!r}")
    return processes


def describe_fatals(processes: dict) -> str:
    fatals = [
        f"{name}={entry.get('current')}"
        for name, entry in processes.items()
        if entry.get("current") == "fatal"
    ]
    return ", ".join(fatals)


def wait_for_indexing(
    expected: set[str],
    config_path: str,
    supervisor_url: str,
    timeout: float,
    poll: float = 5.0,
    min_cycle: int = 1,
) -> None:
    """Block until every inbox is in PI_CONFIG and the DAG ran min_cycle times."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    last_report = 0.0
    last_count = -1

    while True:
        processes = supervisor_state(supervisor_url)
        missing_tasks = [name for name in REQUIRED_TASKS if name not in processes]
        if missing_tasks:
            raise Failure(f"supervisor is missing tasks: {missing_tasks}")

        fatals = describe_fatals(processes)
        if fatals:
            raise Failure(f"supervisor has fatal task(s): {fatals}")

        try:
            in_config = config_inbox_names(config_path)
        except Failure:
            in_config = set()  # config not rendered yet

        done = len(in_config & expected)
        runs = {
            name: processes[name].get("run_count", 0) for name in RUN_AT_LEAST_ONCE
        }
        # The extindex runs after the index job *completes*, so its run count is
        # the signal that the whole pipeline settled, not just the config.
        runs_ok = all(count >= min_cycle for count in runs.values())
        now = time.monotonic()
        if done == len(expected) and runs_ok:
            print(
                f"[wait] all {len(expected)} inboxes are in {config_path} and the "
                f"DAG settled (>= {min_cycle} cycle(s)) after {int(now - started)}s"
            )
            return

        if now - last_report >= 30 or last_count != done:
            indexes = ", ".join(f"{name}={count}" for name, count in runs.items())
            print(
                f"[wait] {done}/{len(expected)} inboxes indexed ({indexes}), "
                f"needs >= {min_cycle} run(s) each, {int(deadline - now)}s left",
                flush=True,
            )
            last_report = now
            last_count = done

        if now >= deadline:
            raise Failure(
                f"timed out after {int(timeout)}s with {done}/{len(expected)} "
                f"inboxes in {config_path} and run counts {runs} (min_cycle "
                f"{min_cycle}; last state: {json.dumps(processes)})"
            )
        time.sleep(poll)


def check_upstream(upstream_url: str, expected: set[str]) -> str:
    status, body = http_get(f"{upstream_url}/manifest.js.gz")
    if status != 200:
        raise Failure(f"GET {upstream_url}/manifest.js.gz -> HTTP {status}")
    try:
        manifest = json.loads(gzip.decompress(body))
    except (OSError, ValueError) as exc:
        raise Failure(f"upstream manifest is not gzipped JSON: {exc}") from exc
    paths = set(manifest)
    missing = [
        f"/{name}"
        for name in sorted(expected)
        if f"/{name}/git/0.git" not in paths
    ]
    if missing:
        raise Failure(
            f"upstream manifest lists no epoch for {len(missing)} inbox(es), "
            f"e.g. {missing[:5]}"
        )
    return f"{len(expected)} inboxes advertised in manifest.js.gz"


def check_supervisor(processes: dict) -> str:
    missing = [name for name in REQUIRED_TASKS if name not in processes]
    if missing:
        raise Failure(f"missing tasks: {missing}")

    fatals = describe_fatals(processes)
    if fatals:
        raise Failure(f"fatal task(s): {fatals}")

    not_run = [
        name for name in RUN_AT_LEAST_ONCE if processes[name].get("run_count", 0) < 1
    ]
    if not_run:
        raise Failure(f"task(s) never ran: {not_run}")

    not_running = [
        name for name in RUNNING_TASKS if processes[name].get("current") != "running"
    ]
    if not_running:
        raise Failure(f"service(s) not running: {not_running}")

    runs = ", ".join(
        f"{name}#{processes[name]['run_count']}" for name in RUN_AT_LEAST_ONCE
    )
    return f"DAG healthy ({runs}, public-inbox-netd running)"


def check_mirror(mirror_dir: str, expected: set[str]) -> str:
    if not os.path.isdir(mirror_dir):
        raise Failure(f"{mirror_dir} does not exist")

    present: set[str] = set()
    without_epoch: list[str] = []
    for entry in sorted(os.listdir(mirror_dir)):
        git_dir = os.path.join(mirror_dir, entry, "git")
        if not os.path.isdir(git_dir):
            continue
        if any(name.endswith(".git") for name in os.listdir(git_dir)):
            present.add(entry)
        else:
            without_epoch.append(entry)

    missing = sorted(expected - present)
    if missing or without_epoch:
        raise Failure(
            f"{len(missing)} inbox(es) not mirrored "
            f"(e.g. {missing[:5]}), {len(without_epoch)} without an epoch"
        )
    return f"{len(present)} inboxes cloned with git epochs"


def check_index(mirror_dir: str, expected: set[str]) -> str:
    missing: list[str] = []
    no_msgmap: list[str] = []
    no_xapian: list[str] = []
    for name in sorted(expected):
        inbox_dir = os.path.join(mirror_dir, name)
        if not os.path.isdir(inbox_dir):
            missing.append(name)
            continue
        if not os.path.isfile(os.path.join(inbox_dir, "msgmap.sqlite3")):
            no_msgmap.append(name)
        if not any(
            entry.startswith("xap")
            for entry in os.listdir(inbox_dir)
            if os.path.isdir(os.path.join(inbox_dir, entry))
        ):
            no_xapian.append(name)
    if missing or no_msgmap or no_xapian:
        raise Failure(
            f"{len(missing)} inbox(es) not mirrored (e.g. {missing[:5]}), "
            f"{len(no_msgmap)} lack msgmap.sqlite3 (e.g. {no_msgmap[:5]}), "
            f"{len(no_xapian)} lack a xapian index"
        )
    return f"{len(expected)} inboxes initialized and indexed"


def check_config(config_path: str, expected: set[str]) -> str:
    if not os.path.isfile(config_path):
        raise Failure(f"{config_path} does not exist")
    in_config = config_inbox_names(config_path)
    missing = sorted(expected - in_config)
    if missing:
        raise Failure(
            f"{len(missing)} inbox(es) missing from {config_path}: {missing[:5]}"
        )
    return f"{len(expected)} inboxes listed in {os.path.basename(config_path)}"


def fetch_page(served_url: str, name: str, markers: list[bytes]) -> str | None:
    """None when ``/<name>/`` is served with every marker, else a detail."""
    status, body = http_get(f"{served_url}/{name}/")
    if status != 200:
        return f"/{name}/ -> HTTP {status}"
    for marker in markers:
        if marker not in body:
            return f"/{name}/ lacks the '{marker.decode()}' marker"
    return None


def failing_pages(
    served_url: str,
    names: list[str],
    markers_for: Callable[[str], list[bytes]],
    retry_timeout: float,
    poll: float = 1.0,
) -> tuple[dict[str, str], int]:
    """Check every page, retrying failures until ``retry_timeout`` expires.

    Reloading public-inbox-netd respawns its workers, so a request can land on
    a straggler worker that still serves the previous config. That window is
    short and self-healing, while a genuinely unserved inbox stays 404 forever
    (the original bug), so the retry budget is spent on failures only.

    Returns ``({name: detail}, recovered_count)``.
    """
    failures = {
        name: detail
        for name in names
        if (detail := fetch_page(served_url, name, markers_for(name))) is not None
    }
    recovered = 0
    deadline = time.monotonic() + retry_timeout
    while failures and time.monotonic() < deadline:
        time.sleep(poll)
        for name in list(failures):
            detail = fetch_page(served_url, name, markers_for(name))
            if detail is None:
                del failures[name]
                recovered += 1
            else:
                failures[name] = detail
    return failures, recovered


def check_http(args: argparse.Namespace, names: set[str] | None = None) -> str:
    targets = sorted(names if names is not None else expected_names(args))
    failures, recovered = failing_pages(
        args.served,
        targets,
        lambda name: [f"{name} message 0".encode()],
        args.http_retry_timeout,
    )
    if failures:
        details = [failures[name] for name in sorted(failures)]
        raise Failure(
            f"{len(details)} inbox(es) not served properly, e.g. {details[:5]}"
        )

    status, _ = http_get(f"{args.served}/all/")
    if status != 200:
        raise Failure(f"extindex {args.served}/all/ -> HTTP {status}")
    retried = f", {recovered} recovered after a retry" if recovered else ""
    return f"{len(targets)} inboxes served over HTTP (+ /all/ extindex){retried}"


#: (label, check) pairs run against the settled stack. Every check takes the
#: parsed argparse namespace and returns a short success description.
CHECKS = (
    ("upstream", lambda args: check_upstream(args.upstream, expected_names(args))),
    ("supervisor", lambda args: check_supervisor(args.processes)),
    ("mirror", lambda args: check_mirror(args.mirror_dir, expected_names(args))),
    ("index", lambda args: check_index(args.mirror_dir, expected_names(args))),
    ("config", lambda args: check_config(args.pi_config, expected_names(args))),
    ("http", lambda args: check_http(args)),
)


def new_list_names(args: argparse.Namespace) -> set[str]:
    """Lists added by mutate.pl (empty when there was no mutation)."""
    baseline = baseline_count(args)
    return {f"list_{i}" for i in range(baseline, args.list_count)}


def baseline_count(args: argparse.Namespace) -> int:
    return args.baseline_count or args.list_count


def check_new_lists(args: argparse.Namespace) -> str:
    """The lists added after the first indexing must be picked up too."""
    names = new_list_names(args)
    if not names:
        raise Failure("no new lists configured (--baseline-count == --list-count)")
    # Each helper raises a Failure naming the offending inboxes.
    check_mirror(args.mirror_dir, names)
    check_index(args.mirror_dir, names)
    check_config(args.pi_config, names)
    check_http(args, names)
    return f"{len(names)} new list(s) mirrored, indexed, configured and served"


def check_new_emails(args: argparse.Namespace) -> str:
    """New messages in pre-existing lists must reach the served index view."""
    if args.new_emails <= 0:
        raise Failure("--new-emails must be positive for this check")
    baseline = baseline_count(args)
    names = [f"list_{i}" for i in range(baseline)]
    failures, recovered = failing_pages(
        args.served,
        names,
        lambda name: [
            f"{name} new {n}".encode() for n in range(args.new_emails)
        ],
        args.http_retry_timeout,
    )
    if failures:
        details = [failures[name] for name in sorted(failures)]
        raise Failure(
            f"{len(details)} list(s) missing their new messages, "
            f"e.g. {details[:5]}"
        )
    retried = f", {recovered} recovered after a retry" if recovered else ""
    return (
        f"{baseline} list(s) serve {args.new_emails} new message(s) "
        f"after the next index{retried}"
    )


def local_refs(repo: str) -> dict[str, str]:
    """Refs of a local mirror repo (heads and meta only)."""
    result = subprocess.run(
        ["git", "--git-dir", repo, "for-each-ref", "--format=%(objectname) %(refname)"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Failure(f"git for-each-ref failed in {repo}: {result.stderr.strip()}")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        oid, _, ref = line.partition(" ")
        if ref.startswith(CONSISTENT_REF_PREFIXES):
            refs[ref] = oid
    return refs


def remote_refs(url: str) -> dict[str, str]:
    """Refs advertised by an upstream epoch repo over HTTP."""
    result = subprocess.run(
        ["git", "ls-remote", url],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise Failure(f"git ls-remote {url} failed: {result.stderr.strip()}")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        oid, _, ref = line.partition("\t")
        if ref.startswith(CONSISTENT_REF_PREFIXES):
            refs[ref] = oid
    return refs


def ref_mismatches(args: argparse.Namespace) -> list[str]:
    mismatches: list[str] = []
    for name in sorted(expected_names(args)):
        repo = os.path.join(args.mirror_dir, name, "git", "0.git")
        url = f"{args.upstream}/{name}/git/0.git"
        mine = local_refs(repo)
        theirs = remote_refs(url)
        for ref in sorted(set(mine) | set(theirs)):
            if mine.get(ref) != theirs.get(ref):
                mismatches.append(
                    f"{name} {ref}: mirror={mine.get(ref) or '-'} "
                    f"upstream={theirs.get(ref) or '-'}"
                )
    return mismatches


def check_fetch_consistency(args: argparse.Namespace) -> str:
    """Every mirrored ref must equal the upstream's (no partial fetches).

    Retries until the deadline: in the concurrency scenario a pull may be in
    flight while this runs, and the mirror is only expected to converge once
    that fetch finishes.
    """
    deadline = time.monotonic() + args.consistency_timeout
    attempt = 0
    while True:
        attempt += 1
        mismatches = ref_mismatches(args)
        if not mismatches:
            return (
                f"mirror refs match the upstream for {len(expected_names(args))} "
                f"inbox(es) (attempt {attempt})"
            )
        if time.monotonic() >= deadline:
            raise Failure(
                f"{len(mismatches)} ref(s) differ from the upstream after "
                f"{args.consistency_timeout}s, e.g. {mismatches[:5]}"
            )
        time.sleep(2)


def wait_for_overlap(supervisor_url: str, timeout: float, poll: float = 0.5) -> str:
    """Wait until a grok-pull and an index (or extindex) run concurrently."""
    deadline = time.monotonic() + timeout
    seen: set[str] = set()
    while time.monotonic() < deadline:
        processes = supervisor_state(supervisor_url)
        fatals = describe_fatals(processes)
        if fatals:
            raise Failure(f"supervisor has fatal task(s) during overlap: {fatals}")

        pull = processes.get("grokmirror_upstream", {}).get("current")
        for name in ("index_upstream", "grok_extindex"):
            state = processes.get(name, {}).get("current")
            seen.add(f"{pull}/{name}={state}")
            if pull in BUSY_STATES and state in BUSY_STATES:
                return (
                    f"grok-pull ({pull}) ran concurrently with {name} ({state})"
                )
        time.sleep(poll)
    raise Failure(
        f"no grok-pull/index overlap observed within {int(timeout)}s "
        f"(observed states: {sorted(seen)[:8]})"
    )


def expected_names(args: argparse.Namespace) -> set[str]:
    return {f"list_{i}" for i in range(args.list_count)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-count",
        type=int,
        default=int(os.environ.get("LIST_COUNT", "360")),
    )
    parser.add_argument(
        "--upstream", default=os.environ.get("ITEST_UPSTREAM", DEFAULT_UPSTREAM)
    )
    parser.add_argument(
        "--supervisor", default=os.environ.get("ITEST_SUPERVISOR", DEFAULT_SUPERVISOR)
    )
    parser.add_argument(
        "--served", default=os.environ.get("ITEST_SERVED", DEFAULT_SERVED)
    )
    parser.add_argument(
        "--mirror-dir", default=os.environ.get("ITEST_MIRROR", DEFAULT_MIRROR)
    )
    parser.add_argument(
        "--pi-config", default=os.environ.get("PI_CONFIG", DEFAULT_PI_CONFIG)
    )
    parser.add_argument(
        "--baseline-count",
        type=int,
        default=int(os.environ.get("BASELINE_COUNT", "0")),
        help="lists that existed before the mutation (default: --list-count)",
    )
    parser.add_argument(
        "--new-emails",
        type=int,
        default=int(os.environ.get("NEW_EMAILS_PER_LIST", "0")),
        help="new messages per pre-existing list added by mutate.pl",
    )
    parser.add_argument(
        "--min-cycle",
        type=int,
        default=1,
        help="how many pull/index/extindex runs to wait for",
    )
    parser.add_argument(
        "--consistency",
        action="store_true",
        help="also compare every mirrored ref against the upstream",
    )
    parser.add_argument(
        "--consistency-timeout",
        type=float,
        default=float(os.environ.get("ITEST_CONSISTENCY_TIMEOUT", "120")),
        help="seconds to keep retrying the ref comparison",
    )
    parser.add_argument(
        "--http-retry-timeout",
        type=float,
        default=float(os.environ.get("ITEST_HTTP_RETRY_TIMEOUT", "30")),
        help="seconds to retry pages that are not served yet (netd reload window)",
    )
    parser.add_argument(
        "--overlap-detect",
        action="store_true",
        help="only wait for a grok-pull/index overlap and report it",
    )
    parser.add_argument(
        "--overlap-timeout",
        type=float,
        default=float(os.environ.get("ITEST_OVERLAP_TIMEOUT", "180")),
        help="seconds to wait for the overlap to happen",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("ITEST_TIMEOUT", "2400")),
        help="seconds to wait for indexing to settle",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="skip waiting (assert the current state instead)",
    )
    args = parser.parse_args(argv)

    if args.overlap_detect:
        try:
            detail = wait_for_overlap(args.supervisor, args.overlap_timeout)
        except Failure as exc:
            print(f"[FAIL] overlap: {exc}", file=sys.stderr)
            return 1
        print(f"[OK]   overlap: {detail}")
        return 0

    expected = expected_names(args)
    baseline = baseline_count(args)
    print(f"==> Checking {len(expected)} inbox(s)" + (
        f" ({len(expected) - baseline} added after the first cycle)"
        if baseline < len(expected)
        else ""
    ))
    if args.new_emails:
        print(f"    new messages: {args.new_emails} per list, {baseline} list(s)")
    print(f"    upstream:   {args.upstream}")
    print(f"    supervisor: {args.supervisor}")
    print(f"    mirror:     {args.mirror_dir}")
    print(f"    config:     {args.pi_config}")
    print(f"    served:     {args.served}")

    failures: list[str] = []

    if not args.no_wait:
        try:
            wait_for_indexing(
                expected,
                args.pi_config,
                args.supervisor,
                args.timeout,
                min_cycle=args.min_cycle,
            )
        except Failure as exc:
            print(f"[FAIL] waiting for indexing: {exc}", file=sys.stderr)
            # Still run the layers below: their output localizes the breakage.
            failures.append(f"indexing did not settle: {exc}")

    try:
        args.processes = supervisor_state(args.supervisor)
    except Failure as exc:
        print(f"[FAIL] supervisor: {exc}", file=sys.stderr)
        failures.append(f"supervisor /state unreadable: {exc}")
        args.processes = {}

    checks = list(CHECKS)
    if new_list_names(args):
        checks.append(("new-lists", check_new_lists))
    if args.new_emails:
        checks.append(("new-emails", check_new_emails))
    if args.consistency:
        checks.append(("consistency", check_fetch_consistency))

    for label, check in checks:
        try:
            detail = check(args)
        except Failure as exc:
            print(f"[FAIL] {label}: {exc}", flush=True)
            failures.append(f"{label}: {exc}")
        else:
            print(f"[OK]   {label}: {detail}", flush=True)

    if failures:
        print("\n==> FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\n==> PASSED: all {len(expected)} inboxes mirrored, indexed and served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
