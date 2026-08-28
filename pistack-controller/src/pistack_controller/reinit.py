"""Reinitialize missing inboxes from the rendered public-inbox config.

Port of ``scripts/reinit-from-config.sh``. Ensures every inbox defined in the
config has a non-empty data directory before the serving services start.
"""

import os
import re
import subprocess

from pistack_controller.config import Config, PI_CONFIG


def get_inbox_names(pi_config: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "config",
            "-f",
            pi_config,
            "--get-regexp",
            r"^publicinbox\..*\.inboxdir$",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    names: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.match(r"^publicinbox\.(.+)\.inboxdir\s", line)
        if match:
            names.add(match.group(1))
    return sorted(names)


def git_config_get(pi_config: str, key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", "-f", pi_config, "--get", key], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def git_config_get_all(pi_config: str, key: str) -> list[str]:
    result = subprocess.run(
        ["git", "config", "-f", pi_config, "--get-all", key],
        capture_output=True,
        text=True,
    )
    return result.stdout.split() if result.returncode == 0 else []


def ensure_inboxes(config: Config) -> int:
    pi_config = PI_CONFIG
    if not os.path.isfile(pi_config):
        print("[reinit] No public-inbox config found, skipping")
        return 0

    env = dict(os.environ)
    env["PI_CONFIG"] = pi_config

    for name in get_inbox_names(pi_config):
        inbox_dir = git_config_get(pi_config, f"publicinbox.{name}.inboxdir")
        if not inbox_dir:
            print(f"[reinit] WARN: inboxdir not set for '{name}', skipping")
            continue
        if os.path.isdir(inbox_dir) and os.listdir(inbox_dir):
            continue

        print(f"[reinit] Initializing inbox '{name}' at {inbox_dir}")
        url = (
            git_config_get(pi_config, f"publicinbox.{name}.url")
            or f"https://localhost/{name}"
        )
        addresses = git_config_get_all(pi_config, f"publicinbox.{name}.address")
        if not addresses:
            addresses = [f"{name}@localhost"]

        os.makedirs(inbox_dir, exist_ok=True)
        result = subprocess.run(
            ["public-inbox-init", "-V2", name, inbox_dir, url, *addresses],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[reinit] Failed to init '{name}': {result.stderr.strip()}")
    return 0
