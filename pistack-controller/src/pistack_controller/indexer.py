"""Index cloned grokmirror repos for public-inbox hosting.

Scans ``paths.mirror`` for v2 inboxes, initializes them with
``public-inbox-init -V2``, indexes with ``public-inbox-index`` and updates the
external index with ``public-inbox-extindex``. Python port of
``scripts/index-cloned-repos.sh`` (scheduling now handled by tinySupervisor).
"""

import os
import re
import signal
import subprocess
import time
import urllib.request

from pistack_controller.config import PI_CONFIG, Config, MirrorSource

GROKMIRROR_UA = "grokmirror/2.1.0"
HTTP_MINIMUM_INTERVAL = 15.0

#: Process name of the multi-protocol public-inbox daemon.
NETD_NAME = "public-inbox-netd"

_last_http: list[float] = [0.0]


def pi_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PI_CONFIG"] = PI_CONFIG
    return env


def run_cmd(
    cmd: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env or os.environ, capture_output=True, text=True)


def git_config_get(config_file: str, key: str) -> str | None:
    result = run_cmd(["git", "config", "-f", config_file, "--get", key])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def build_index_flags(config: Config, jobs: int = 4) -> list[str]:
    flags = [f"--jobs={jobs}"]
    if config.indexing.level != "full":
        flags += ["-L", config.indexing.level]
    return flags


# -- public-inbox-netd reload --------------------------------------------
#
# ``public-inbox-init`` appends a ``[publicinbox "<name>"]`` section to
# PI_CONFIG, but the long-running ``public-inbox-netd`` service keeps the
# config it loaded at startup.  Without a reload, newly initialized inboxes
# are indexed on disk yet never served over HTTP/NNTP/IMAP until the
# controller restarts.  SIGHUP makes the daemon respawn its workers, which
# re-read PI_CONFIG (PublicInbox::Daemon::master_loop).


def _proc_argv(pid_dir: str) -> list[str]:
    """Return the argv of a ``/proc/<pid>`` process (empty when unreadable)."""
    try:
        with open(os.path.join(pid_dir, "cmdline"), "rb") as handle:
            raw = handle.read()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def _proc_ppid(pid_dir: str) -> int | None:
    """Return the parent PID of a ``/proc/<pid>`` process."""
    try:
        with open(os.path.join(pid_dir, "stat"), encoding="utf-8") as handle:
            data = handle.read()
    except OSError:
        return None
    # ``comm`` (field 2) is parenthesized and may itself contain spaces and
    # parentheses, so split after the final ')' instead of by whitespace.
    _, _, tail = data.rpartition(")")
    fields = tail.split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def find_netd_pids(proc_root: str = "/proc") -> dict[int, int | None]:
    """Map every running ``public-inbox-netd`` PID to its parent PID."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return {}

    pids: dict[int, int | None] = {}
    for entry in entries:
        if not entry.isdigit():
            continue
        pid_dir = os.path.join(proc_root, entry)
        argv = _proc_argv(pid_dir)
        if not any(os.path.basename(arg) == NETD_NAME for arg in argv):
            continue
        pids[int(entry)] = _proc_ppid(pid_dir)
    return pids


def reload_netd(proc_root: str = "/proc") -> list[int]:
    """SIGHUP the running ``public-inbox-netd`` so it re-reads PI_CONFIG.

    Workers forked by the daemon master inherit its cmdline, so only the
    master (the netd PID whose parent is not itself a netd) is signalled;
    restarting its workers is enough and avoids killing a worker mid-request.

    Returns the list of signalled PIDs (empty when netd is not running).
    """
    pids = find_netd_pids(proc_root)
    if not pids:
        return []

    masters = [pid for pid, ppid in pids.items() if ppid not in pids]
    targets = sorted(masters or pids)

    signalled: list[int] = []
    for pid in targets:
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            print(f"[indexer] Cannot reload public-inbox-netd (pid {pid}): {exc}")
            continue
        signalled.append(pid)

    if signalled:
        print(f"[indexer] Reloaded public-inbox-netd (pids: {signalled})")
    return signalled


def find_v2_inboxes(topdir: str) -> list[str]:
    """Find v2 inboxes (directories with ``git/N.git`` structure)."""
    inboxes: list[str] = []
    if not os.path.isdir(topdir):
        return inboxes
    for entry in sorted(os.listdir(topdir)):
        git_dir = os.path.join(topdir, entry, "git")
        if not os.path.isdir(git_dir):
            continue
        if any(
            os.path.isdir(os.path.join(git_dir, name))
            for name in os.listdir(git_dir)
            if name.endswith(".git")
        ):
            inboxes.append(entry)
    return inboxes


def needs_init(inbox_dir: str) -> bool:
    """Return True when the directory is a grokmirror clone lacking v2 structure."""
    git_dir = os.path.join(inbox_dir, "git")
    if not os.path.isdir(git_dir):
        return False
    if not any(
        os.path.isdir(os.path.join(git_dir, name))
        for name in os.listdir(git_dir)
        if name.endswith(".git")
    ):
        return False
    # Missing the v2 wrapper (all.git / msgmap) means it still needs init.
    return not os.path.isdir(os.path.join(inbox_dir, "all.git")) or not os.path.isfile(
        os.path.join(inbox_dir, "msgmap.sqlite3")
    )


def extract_addresses_from_git(inbox_dir: str) -> str:
    """Read addresses from the latest epoch's ``refs/meta/origins:i``."""
    git_dir = os.path.join(inbox_dir, "git")
    if not os.path.isdir(git_dir):
        return ""
    repos = sorted(
        (
            os.path.join(git_dir, name)
            for name in os.listdir(git_dir)
            if name.endswith(".git")
        ),
        reverse=True,
    )
    for repo in repos:
        result = run_cmd(["git", "-C", repo, "show", "refs/meta/origins:i"])
        if result.returncode != 0:
            continue
        addresses = re.findall(
            r"^\s*address\s*=\s*(.+?)\s*$", result.stdout, re.MULTILINE
        )
        if addresses:
            return " ".join(addresses)
    return ""


def fetch_remote_config(inbox_name: str, origin: str) -> tuple[str, str]:
    """Fetch (addresses, description) from the upstream inbox config."""
    global _last_http
    elapsed = time.time() - _last_http[0]
    if elapsed < HTTP_MINIMUM_INTERVAL:
        time.sleep(HTTP_MINIMUM_INTERVAL - elapsed)
    _last_http[0] = time.time()

    url = f"{origin}/{inbox_name}/_/text/config/raw"
    if not url.startswith(("http://", "https://")):
        print(f"[indexer] unsupported origin for '{inbox_name}': {origin}")
        return "", ""

    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url,
        headers={"User-Agent": GROKMIRROR_UA, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            body = response.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[indexer] failed to fetch remote config for '{inbox_name}': {exc}")
        return "", ""

    addresses = re.findall(r"^\s*address\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    description = re.findall(r"^\s*description\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    return " ".join(addresses), (description[0] if description else "")


def init_inbox(config: Config, source: MirrorSource, inbox_name: str) -> int:
    """Init/index one inbox of ``source``. Returns 0=ok, 1=failed, 2=skipped."""
    inbox_dir = os.path.join(source.mirror_dir(config.paths), inbox_name)
    origin = source.origin
    pi_config = PI_CONFIG
    env = pi_env()

    if git_config_get(pi_config, f"publicinbox.{inbox_name}.inboxdir") is not None:
        if not needs_init(inbox_dir):
            print(f"[indexer] Skipping '{inbox_name}' - already initialized")
            return 2
        print(
            f"[indexer] Reinitializing '{inbox_name}' - in config but lacks v2 structure"
        )
        url = git_config_get(pi_config, f"publicinbox.{inbox_name}.url") or inbox_name
        address = (
            git_config_get(pi_config, f"publicinbox.{inbox_name}.address")
            or f"{inbox_name}@localhost"
        )
        description = ""
    else:
        print(f"[indexer] Initializing inbox '{inbox_name}'")
        address = extract_addresses_from_git(inbox_dir)
        description = ""
        if not address:
            address, description = fetch_remote_config(inbox_name, origin)
        if not address:
            address = f"{inbox_name}@localhost"
            print(f"[indexer] Using fallback address: {address}")
        url = inbox_name

    init_cmd = ["public-inbox-init", "-V2"]
    if config.indexing.level != "full":
        init_cmd += ["-L", config.indexing.level]
    init_cmd += [inbox_name, inbox_dir, url, address]

    result = run_cmd(init_cmd, env)
    if result.returncode != 0:
        print(f"[indexer] Failed to initialize '{inbox_name}': {result.stderr.strip()}")
        return 1

    if description:
        with open(os.path.join(inbox_dir, "description"), "w") as handle:
            handle.write(description)

    print(f"[indexer] Indexing '{inbox_name}'")
    result = run_cmd(["public-inbox-index", *build_index_flags(config), inbox_dir], env)
    if result.returncode != 0:
        print(f"[indexer] Failed to index '{inbox_name}': {result.stderr.strip()}")
        return 1

    print(f"[indexer] Indexed '{inbox_name}'")
    return 0


def index_cloned_repos(
    config: Config, sources: list[MirrorSource] | None = None
) -> int:
    """Index cloned v2 inboxes for the given sources (default: all).

    Each source is scanned under its own toplevel
    (``<paths.mirror>/<source id>``) and inboxes are initialized with that
    source's upstream host as the origin. Returns an exit code.
    """
    selected = list(config.mirroring.sources if sources is None else sources)
    if not selected:
        print("[indexer] No mirroring sources configured")
        return 0

    counts: dict[int, int] = {0: 0, 1: 0, 2: 0}
    total = 0
    for source in selected:
        topdir = source.mirror_dir(config.paths)
        print(f"[indexer] Scanning for v2 inboxes in {topdir} (source '{source.id}')")
        inboxes = find_v2_inboxes(topdir)
        if not inboxes:
            print(f"[indexer] No v2 inboxes found for source '{source.id}'")
            continue
        total += len(inboxes)
        for inbox_name in inboxes:
            rc = init_inbox(config, source, inbox_name)
            counts[rc] = counts.get(rc, 0) + 1

    print(
        f"[indexer] Summary: {total} inboxes, "
        f"{counts[0]} initialized, {counts[2]} skipped, {counts[1]} failed"
    )
    if counts[0] > 0:
        # public-inbox-init added sections to PI_CONFIG; make the running
        # daemon pick them up instead of waiting for a controller restart.
        reload_netd()
    return 0 if counts[1] == 0 else 1


def run_extindex(config: Config) -> int:
    """Update the cross-inbox external index at paths.extindex."""
    os.makedirs(config.paths.extindex, exist_ok=True)
    cmd = ["public-inbox-extindex", "--all", "--jobs=4"]
    if config.indexing.level != "full":
        cmd += ["-L", config.indexing.level]
    cmd.append(config.paths.extindex)

    print("[indexer] Running extindex on all inboxes")
    result = run_cmd(cmd, pi_env())
    if result.returncode != 0:
        print(f"[indexer] Extindex failed: {result.stderr.strip()}")
        return 1
    print("[indexer] Extindex complete")
    # The extindex is read by the serving daemons; reload so a refreshed or
    # newly populated extindex is visible without a container restart.
    reload_netd()
    return 0
