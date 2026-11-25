"""Purge public-inbox indexing data while preserving grokmirror git clones.

Port of ``scripts/purge-indexing.sh``. Removes msgmap.sqlite3, over.sqlite3,
xapian dirs, all.git and description; also resets the external index.

Mirror sources are stored one directory below the mirror root
(``<paths.mirror>/<source id>/<inbox>``), so every source toplevel is walked.
"""

import os
import shutil

from pistack_controller.config import Config

_PURGE_PATTERNS = (
    "msgmap.sqlite3",
    "msgmap.sqlite3-journal",
    "over.sqlite3",
    "over.sqlite3-journal",
    "description",
    "all.git",
)


def _listdir(path: str) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError as exc:
        raise RuntimeError(f"Cannot list {path}: {exc}") from exc


def _remove(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError as exc:
        raise RuntimeError(f"Cannot remove {path}: {exc}") from exc


def _purge_inbox_dir(inbox_dir: str) -> None:
    for pattern in _PURGE_PATTERNS:
        _remove(os.path.join(inbox_dir, pattern))
    for name in _listdir(inbox_dir):
        if name.startswith("xap"):
            _remove(os.path.join(inbox_dir, name))


def purge_indexing(config: Config) -> int:
    """Purge indexing data for every inbox of every mirror source."""
    for source in config.mirroring.sources:
        topdir = source.mirror_dir(config.paths)
        if not os.path.isdir(topdir):
            continue
        for entry in _listdir(topdir):
            inbox_dir = os.path.join(topdir, entry)
            if not os.path.isdir(os.path.join(inbox_dir, "git")):
                continue
            _purge_inbox_dir(inbox_dir)
            print(f"[purge] Purged indexing data from '{source.id}/{entry}'")

    if os.path.isdir(config.paths.extindex):
        _remove(config.paths.extindex)
    try:
        os.makedirs(config.paths.extindex, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot recreate extindex directory {config.paths.extindex}: {exc}"
        ) from exc
    print(f"[purge] Recreated empty extindex directory: {config.paths.extindex}")
    return 0
