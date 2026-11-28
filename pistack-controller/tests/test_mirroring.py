"""Tests for multi-source mirroring.

Run with the project environment::

    cd pistack-controller && uv run python -m unittest discover -s tests

The controller reads ``GROKMIRROR_CONF_DIR``/``PI_CONFIG`` at import time, so
both are pointed at a temporary directory *before* the package is imported.
"""

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_WORK = tempfile.mkdtemp(prefix="pistack-tests-")
os.environ["GROKMIRROR_CONF_DIR"] = str(Path(_WORK, "grokmirror"))
os.environ["PI_CONFIG"] = str(Path(_WORK, "pi-config", "config"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pistack_controller import config as cfg  # noqa: E402
from pistack_controller import indexer, render  # noqa: E402
from pistack_controller.config import Config, ConfigError, load_config  # noqa: E402
from pistack_controller.main import build_supervisor, init_folders  # noqa: E402
from pistack_controller.purge import purge_indexing  # noqa: E402

#: argv shapes used by the /proc fixtures in NetdReloadTests.
NETD_ARGV = ("/usr/local/bin/public-inbox-netd", "-l", "http://0.0.0.0:8080")
PYTHON_ARGV = ("python", "-m", "pistack_controller")


def record_cmds(cmds: list[list[str]], returncode: int = 0):
    """A ``run_cmd`` stand-in that records commands and returns ``returncode``."""

    class Result:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = "fatal: Needed a single revision" if returncode else ""

    def run(cmd, env=None):
        cmds.append(cmd)
        return Result()

    return run


def mirroring_config(sources: str, data: Path) -> str:
    """A minimal configs.yaml with the given ``mirroring:`` block body."""
    return textwrap.dedent(
        f"""\
        paths:
          root: {data}
          mirror: {data}/mirror
          imap: {data}/imap
          lei: {data}/lei
          extindex: {data}/all
        hosting:
          enabled: true
          serve_host: lore.test
        mirroring:
        """
    ) + textwrap.indent(textwrap.dedent(sources), "  ")


class MirroringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="case-", dir=_WORK))
        self.data = self.dir / "data"
        self.env = self.dir / ".env"
        self.env.write_text("")

    def write_config(self, body: str, name: str = "configs.yaml") -> str:
        path = self.dir / name
        path.write_text(mirroring_config(body, self.data))
        return str(path)

    def load(self, body: str, name: str = "configs.yaml") -> Config:
        return load_config(self.write_config(body, name), str(self.env))


class ConfigTests(MirroringTestCase):
    def test_multiple_sources_are_loaded(self) -> None:
        config = self.load(
            """\
            - id: lore.kernel.org
              upstream_host: https://lore.kernel.org/
            - id: lore.debian.org
              upstream_host: https://lore.debian.org
              interval: 60
              indexing: true
              extindex: false
            """
        )
        self.assertEqual([s.id for s in config.mirroring], ["lore.kernel.org", "lore.debian.org"])
        self.assertEqual(config.mirroring[0].origin, "https://lore.kernel.org")
        self.assertEqual(config.mirroring[0].interval, 7200)
        self.assertFalse(config.mirroring[0].indexing)
        self.assertTrue(config.mirroring[0].extindex)
        self.assertEqual(config.mirroring[1].interval, 60)
        self.assertTrue(config.mirroring[1].indexing)
        self.assertFalse(config.mirroring[1].extindex)
        self.assertTrue(config.mirroring.enabled)
        self.assertTrue(config.mirroring.extindex_enabled)

    def test_mirror_dirs_are_per_source(self) -> None:
        config = self.load(
            """\
            - id: one.example
              upstream_host: https://one.example
            - id: two.example
              upstream_host: https://two.example
            """
        )
        dirs = [s.mirror_dir(config.paths) for s in config.mirroring]
        self.assertEqual(
            dirs,
            [
                f"{config.paths.mirror}/one.example",
                f"{config.paths.mirror}/two.example",
            ],
        )

    def test_empty_list_disables_mirroring(self) -> None:
        path = self.dir / "empty.yaml"
        path.write_text("mirroring: []\n")
        config = load_config(str(path), str(self.env))
        self.assertFalse(config.mirroring.enabled)
        self.assertFalse(config.mirroring.extindex_enabled)
        self.assertEqual(config.mirroring.sources, [])

    def test_legacy_mapping_is_rejected_with_migration_hint(self) -> None:
        path = self.dir / "legacy.yaml"
        path.write_text(
            "mirroring:\n  enabled: true\n  upstream_host: https://lore.example\n"
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(str(path), str(self.env))
        message = str(ctx.exception)
        self.assertIn("list of named sources", message)
        self.assertIn("upstream_host", message)

    def test_invalid_sources_are_rejected(self) -> None:
        cases = {
            "path traversal": "- id: ../evil\n  upstream_host: https://x\n",
            "absolute path": "- id: /evil\n  upstream_host: https://x\n",
            "hidden dot": "- id: .hidden\n  upstream_host: https://x\n",
            "missing id": "- upstream_host: https://x\n",
            "missing upstream": "- id: one.example\n",
            "duplicate id": (
                "- id: one.example\n"
                "  upstream_host: https://x\n"
                "- id: one.example\n"
                "  upstream_host: https://y\n"
            ),
        }
        for label, body in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                self.load(body)

    def test_env_override_by_list_index(self) -> None:
        self.env.write_text(
            "MIRRORING_0_UPSTREAM_HOST=https://mirror.example\n"
            "MIRRORING_1_UPSTREAM_HOST=https://two.example\n"
        )
        config = self.load(
            """\
            - id: one.example
              upstream_host: https://one.example
            - id: two.example
              upstream_host: https://two.example
            """
        )
        self.assertEqual(config.mirroring[0].origin, "https://mirror.example")
        self.assertEqual(config.mirroring[1].origin, "https://two.example")


class RenderTests(MirroringTestCase):
    def setUp(self) -> None:
        super().setUp()
        for directory in (cfg.GROKMIRROR_CONF_DIR, Path(cfg.PI_CONFIG).parent):
            for entry in Path(directory).glob("*") if Path(directory).is_dir() else []:
                entry.unlink()

    def test_one_grokmirror_config_per_source(self) -> None:
        config = self.load(
            """\
            - id: lore.kernel.org
              upstream_host: https://lore.kernel.org
            - id: lore.debian.org
              upstream_host: https://lore.debian.org
            """
        )
        written = render.write_grokmirror_confs(config)
        self.assertEqual(
            sorted(Path(p).name for p in written),
            [
                "grokmirror-lore.debian.org.conf",
                "grokmirror-lore.kernel.org.conf",
            ],
        )
        for source, path in zip(config.mirroring.sources, written, strict=True):
            text = Path(path).read_text()
            self.assertIn(f"toplevel = {source.mirror_dir(config.paths)}", text)
            self.assertIn(f"site = {source.origin}", text)

    def test_stale_source_config_is_removed(self) -> None:
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        render.write_grokmirror_confs(config)
        stale = Path(cfg.GROKMIRROR_CONF_DIR, "grokmirror-gone.example.conf")
        stale.write_text("stale\n")

        render.write_grokmirror_confs(config)
        self.assertFalse(stale.exists())

    def test_pi_config_extindex_follows_sources(self) -> None:
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        self.assertIn('[extindex "all"]', render.render_pi_config(config))

        disabled = self.load(
            "- id: one.example\n"
            "  upstream_host: https://one.example\n"
            "  extindex: false\n",
            name="no-extindex.yaml",
        )
        self.assertFalse(disabled.mirroring.extindex_enabled)
        self.assertNotIn('[extindex "all"]', render.render_pi_config(disabled))


class SupervisorTests(MirroringTestCase):
    def test_tasks_per_source_and_extindex_dependencies(self) -> None:
        config = self.load(
            """\
            - id: one.example
              upstream_host: https://one.example
            - id: two.example
              upstream_host: https://two.example
              indexing: true
              extindex: false
            """
        )
        supervisor = build_supervisor(config)
        entries = supervisor._state.entries
        self.assertIn("grokmirror_one.example", entries)
        self.assertIn("grokmirror_two.example", entries)
        self.assertIn("index_two.example", entries)
        self.assertNotIn("index_one.example", entries)
        # only the source that opted in feeds the extindex
        self.assertEqual(
            entries["grok_extindex"].task.depends, ["grokmirror_one.example"]
        )

    def test_no_mirroring_tasks_when_list_is_empty(self) -> None:
        config = self.load("[]\n")
        supervisor = build_supervisor(config)
        names = set(supervisor._state.entries)
        self.assertFalse([n for n in names if n.startswith("grokmirror_")])
        self.assertNotIn("grok_extindex", names)

    def test_init_folders_creates_source_dirs(self) -> None:
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        self.assertEqual(init_folders(config), 0)
        self.assertTrue(Path(config.paths.mirror, "one.example").is_dir())
        self.assertTrue(Path(config.paths.extindex, "description").is_file())


class IndexerTests(MirroringTestCase):
    def make_inbox(self, config: Config, source_id: str, inbox: str) -> Path:
        inbox_dir = Path(config.paths.mirror, source_id, inbox)
        Path(inbox_dir, "git", "0.git").mkdir(parents=True)
        return inbox_dir


    def test_scoped_indexing_uses_the_source_origin(self) -> None:
        config = self.load(
            """\
            - id: one.example
              upstream_host: https://one.example
            - id: two.example
              upstream_host: https://two.example
            """
        )
        for source in config.mirroring:
            self.make_inbox(config, source.id, f"list-{source.id}")

        inits: list[list[str]] = []
        origins: list[tuple[str, str]] = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, env=None):
            inits.append(cmd)
            return Result()

        def fake_fetch(inbox_name, origin):
            origins.append((inbox_name, origin))
            return f"{inbox_name}@example", ""

        with (
            mock.patch.object(indexer, "run_cmd", fake_run),
            mock.patch.object(indexer, "git_config_get", lambda *a: None),
            mock.patch.object(indexer, "fetch_remote_config", fake_fetch),
        ):
            self.assertEqual(indexer.index_cloned_repos(config, [config.mirroring[1]]), 0)
            self.assertEqual(
                [cmd[3] for cmd in inits if cmd[0] == "public-inbox-init"],
                [f"{config.paths.mirror}/two.example/list-two.example"],
            )
            self.assertEqual(origins, [("list-two.example", "https://two.example")])

            inits.clear()
            origins.clear()
            self.assertEqual(indexer.index_cloned_repos(config), 0)
            self.assertEqual(len([c for c in inits if c[0] == "public-inbox-init"]), 2)
            self.assertEqual(
                sorted(origins),
                [
                    ("list-one.example", "https://one.example"),
                    ("list-two.example", "https://two.example"),
                ],
            )

    def test_purge_walks_per_source_layout(self) -> None:
        config = self.load(
            """\
            - id: one.example
              upstream_host: https://one.example
            - id: two.example
              upstream_host: https://two.example
            """
        )
        for source in config.mirroring:
            inbox_dir = self.make_inbox(config, source.id, "list")
            for name in ("msgmap.sqlite3", "over.sqlite3", "description"):
                Path(inbox_dir, name).write_text("x")
            Path(inbox_dir, "xapian0").mkdir()
            Path(inbox_dir, "all.git").mkdir()

        self.assertEqual(purge_indexing(config), 0)
        for source in config.mirroring:
            inbox_dir = Path(config.paths.mirror, source.id, "list")
            self.assertEqual(sorted(p.name for p in inbox_dir.iterdir()), ["git"])
            self.assertTrue(Path(inbox_dir, "git", "0.git").is_dir())

    def test_reload_netd_runs_when_inboxes_are_initialized(self) -> None:
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        self.make_inbox(config, "one.example", "list")
        calls: list[str] = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        with (
            mock.patch.object(indexer, "run_cmd", lambda cmd, env=None: Result()),
            mock.patch.object(indexer, "git_config_get", lambda *a: None),
            mock.patch.object(
                indexer, "fetch_remote_config", lambda name, origin: ("a@b", "")
            ),
            mock.patch.object(
                indexer, "reload_netd", lambda *a, **kw: calls.append("reload")
            ),
        ):
            self.assertEqual(indexer.index_cloned_repos(config), 0)
            self.assertEqual(calls, ["reload"])

            # Already in PI_CONFIG and already v2-initialized: re-indexed, so
            # the config is untouched and no netd reload is needed.
            calls.clear()
            inbox_dir = Path(config.paths.mirror, "one.example", "list")
            Path(inbox_dir, "all.git").mkdir()
            Path(inbox_dir, "msgmap.sqlite3").write_text("x")
            with mock.patch.object(
                indexer, "git_config_get", lambda *a: str(inbox_dir)
            ):
                self.assertEqual(indexer.index_cloned_repos(config), 0)
            self.assertEqual(calls, [])

    def test_initialized_inbox_is_reindexed_not_skipped(self) -> None:
        """New messages in an existing list must reach the served index."""
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        inbox_dir = self.make_inbox(config, "one.example", "list")
        Path(inbox_dir, "all.git").mkdir()
        Path(inbox_dir, "msgmap.sqlite3").write_text("x")

        cmds: list[list[str]] = []

        with (
            mock.patch.object(indexer, "run_cmd", record_cmds(cmds)),
            mock.patch.object(indexer, "git_config_get", lambda *a: str(inbox_dir)),
        ):
            self.assertEqual(indexer.index_cloned_repos(config), 0)

        inbox_cmds = [cmd[0] for cmd in cmds if cmd[0].startswith("public-inbox-")]
        self.assertEqual(inbox_cmds, ["public-inbox-index"])
        self.assertEqual(cmds[-1][-1], str(inbox_dir))

    def test_inbox_locked_by_a_pull_is_deferred(self) -> None:
        """A concurrent grok-pull must not be indexed mid-fetch."""
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        self.make_inbox(config, "one.example", "list")

        cmds: list[list[str]] = []

        with (
            mock.patch.object(indexer, "run_cmd", record_cmds(cmds)),
            mock.patch.object(indexer, "git_config_get", lambda *a: None),
            mock.patch.object(indexer, "repo_locked_by_pull", lambda repo: True),
            mock.patch.object(
                indexer, "reload_netd", lambda *a, **kw: self.fail("unexpected reload")
            ),
        ):
            # Deferred inboxes are neither initialized nor counted as failures.
            self.assertEqual(indexer.index_cloned_repos(config), 0)
        self.assertEqual(cmds, [])

    def test_inbox_without_head_is_deferred(self) -> None:
        """A half-cloned epoch repo (no HEAD yet) is left for the next pass."""
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        self.make_inbox(config, "one.example", "list")

        cmds: list[list[str]] = []

        with (
            mock.patch.object(indexer, "run_cmd", record_cmds(cmds, returncode=1)),
            mock.patch.object(indexer, "git_config_get", lambda *a: None),
        ):
            self.assertEqual(indexer.index_cloned_repos(config), 0)
        self.assertEqual([cmd[0] for cmd in cmds], ["git"])
        self.assertEqual(cmds[0][-2:], ["--verify", "HEAD"])

    def test_repo_locked_by_pull_detects_another_process(self) -> None:
        """The probe must interlock with grok-pull's fcntl record lock."""
        repo = str(self.dir / "repo.git")
        Path(repo).mkdir()

        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl, os, sys, time\n"
                "fh = open(sys.argv[1], 'w')\n"
                "fcntl.lockf(fh, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(30)\n",
                indexer.repo_lock_path(repo),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            self.assertTrue(indexer.repo_locked_by_pull(repo))
        finally:
            holder.kill()
            holder.wait(timeout=10)
            if holder.stdout is not None:
                holder.stdout.close()

        # Once the holder is gone the repo is free again.
        self.assertFalse(indexer.repo_locked_by_pull(repo))

    def test_repo_locked_by_pull_without_a_lock_file(self) -> None:
        repo = str(self.dir / "never-pulled.git")
        Path(repo).mkdir()
        self.assertFalse(indexer.repo_locked_by_pull(repo))
        # Probing must not litter the mirror with lock files.
        self.assertFalse(os.path.exists(indexer.repo_lock_path(repo)))

    def test_run_extindex_reloads_netd(self) -> None:
        config = self.load(
            "- id: one.example\n  upstream_host: https://one.example\n"
        )
        calls: list[str] = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        with (
            mock.patch.object(indexer, "run_cmd", lambda cmd, env=None: Result()),
            mock.patch.object(
                indexer, "reload_netd", lambda *a, **kw: calls.append("reload")
            ),
        ):
            self.assertEqual(indexer.run_extindex(config), 0)
            self.assertEqual(calls, ["reload"])


class NetdReloadTests(MirroringTestCase):
    def make_proc(self, processes: dict[int, tuple[tuple[str, ...], int]]) -> str:
        root = self.dir / "proc"
        root.mkdir()
        for pid, (argv, ppid) in processes.items():
            pid_dir = root / str(pid)
            pid_dir.mkdir()
            (pid_dir / "cmdline").write_bytes(
                b"\0".join(arg.encode() for arg in argv) + b"\0"
            )
            # comm is parenthesized and may contain spaces, like the real file.
            (pid_dir / "stat").write_text(
                f"{pid} (public-inbox netd) S {ppid} {pid} {pid} 0 -1\n"
            )
        return str(root)

    def test_only_the_daemon_master_is_signalled(self) -> None:
        proc = self.make_proc(
            {
                10: (NETD_ARGV, 1),
                11: (NETD_ARGV, 10),
                12: (NETD_ARGV, 10),
                20: (PYTHON_ARGV, 1),
            }
        )
        with mock.patch.object(indexer.os, "kill") as kill:
            self.assertEqual(indexer.reload_netd(proc), [10])
        self.assertEqual(kill.call_args_list, [mock.call(10, signal.SIGHUP)])

    def test_no_netd_running_is_a_noop(self) -> None:
        proc = self.make_proc({20: (PYTHON_ARGV, 1)})
        with mock.patch.object(indexer.os, "kill") as kill:
            self.assertEqual(indexer.reload_netd(proc), [])
        self.assertEqual(kill.call_args_list, [])

    def test_orphaned_worker_is_signalled_when_the_master_is_gone(self) -> None:
        proc = self.make_proc({11: (NETD_ARGV, 10)})
        with mock.patch.object(indexer.os, "kill") as kill:
            self.assertEqual(indexer.reload_netd(proc), [11])
        self.assertEqual(kill.call_args_list, [mock.call(11, signal.SIGHUP)])

    def test_missing_proc_root_is_a_noop(self) -> None:
        self.assertEqual(indexer.reload_netd(str(self.dir / "no-such-proc")), [])


if __name__ == "__main__":
    unittest.main()
