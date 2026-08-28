"""Purge public-inbox indexing data while preserving grokmirror git clones.

Port of ``scripts/purge-indexing.sh``. Removes msgmap.sqlite3, over.sqlite3,
xapian dirs, all.git and description; also resets the external index.
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


def purge_indexing(config: Config) -> int:
    topdir = config.paths.mirror
    if os.path.isdir(topdir):
        for entry in sorted(os.listdir(topdir)):
            inbox_dir = os.path.join(topdir, entry)
            if not os.path.isdir(os.path.join(inbox_dir, "git")):
                continue
            for pattern in _PURGE_PATTERNS:
                target = os.path.join(inbox_dir, pattern)
                if os.path.isdir(target):
                    shutil.rmtree(target)
                elif os.path.lexists(target):
                    os.remove(target)
            for name in list(os.listdir(inbox_dir)):
                if name.startswith("xap"):
                    shutil.rmtree(os.path.join(inbox_dir, name))
            print(f"[purge] Purged indexing data from '{entry}'")

    if os.path.isdir(config.paths.extindex):
        shutil.rmtree(config.paths.extindex)
    os.makedirs(config.paths.extindex, exist_ok=True)
    print(f"[purge] Recreated empty extindex directory: {config.paths.extindex}")
    return 0
