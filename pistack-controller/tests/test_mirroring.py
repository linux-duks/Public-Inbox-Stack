"""Tests for multi-source mirroring.

Run with the project environment::

    cd pistack-controller && uv run python -m unittest discover -s tests

The controller reads ``GROKMIRROR_CONF_DIR``/``PI_CONFIG`` at import time, so
both are pointed at a temporary directory *before* the package is imported.
"""

import os
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


if __name__ == "__main__":
    unittest.main()
