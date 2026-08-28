#!/usr/bin/env python3
"""Render proxy/angie.conf from proxy/angie.conf.j2 and proxy/proxy.config.

Reads KEY=VALUE settings from proxy.config (dotenv), fills the Jinja2
template and writes proxy/angie.conf. The same file feeds ${...}
interpolation in proxy.compose.yaml via `--env-file proxy/proxy.config`
(external host ports). Toggles (http/https/nntp/imap) and ACME/Anubis
flags control which server blocks are emitted.

Usage:
    uv run --project pistack-controller python proxy/render_proxy.py
    uv run --project pistack-controller python proxy/render_proxy.py --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

PROXY_DIR = Path(__file__).parent
DEFAULT_CONFIG = PROXY_DIR / "proxy.config"
DEFAULT_OUTPUT = PROXY_DIR / "angie.conf"

_DEFAULTS = {
    "SERVE_HOST": "localhost",
    "HTTP_ENABLED": "true",
    "HTTP_PORT": "80",
    "HTTPS_ENABLED": "false",
    "HTTPS_PORT": "443",
    "NNTP_ENABLED": "false",
    "NNTP_PORT": "119",
    "IMAP_ENABLED": "false",
    "IMAP_PORT": "143",
    "ACME_ENABLED": "false",
    "ACME_EMAIL": "admin@example.com",
    "ANUBIS_ENABLED": "false",
    "ANUBIS_PORT": "18888",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file, ignoring comments and blank lines."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_context(env: dict[str, str]) -> dict:
    merged = {**_DEFAULTS, **env}
    return {
        "serve_host": merged["SERVE_HOST"],
        "http_enabled": _to_bool(merged["HTTP_ENABLED"]),
        "https_enabled": _to_bool(merged["HTTPS_ENABLED"]),
        "nntp_enabled": _to_bool(merged["NNTP_ENABLED"]),
        "imap_enabled": _to_bool(merged["IMAP_ENABLED"]),
        "acme_enabled": _to_bool(merged["ACME_ENABLED"]),
        "acme_email": merged["ACME_EMAIL"],
        "anubis_enabled": _to_bool(merged["ANUBIS_ENABLED"]),
        "anubis_port": merged["ANUBIS_PORT"],
    }


def render(env: dict[str, str]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(PROXY_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template("angie.conf.j2").render(**build_context(env))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render_proxy.py",
        description="Render proxy/angie.conf from the jinja2 template and proxy.config.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="path to the KEY=VALUE proxy config (default: proxy/proxy.config)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="path to write the rendered config (default: proxy/angie.conf)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rendered config to stdout instead of writing it",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        print(
            f"[render-proxy] proxy config not found: {config_path}",
            file=sys.stderr,
        )
        print(
            "[render-proxy] copy proxy/proxy.config.example to proxy/proxy.config and edit it",
            file=sys.stderr,
        )
        return 1

    rendered = render(parse_env_file(config_path))

    if args.dry_run:
        sys.stdout.write(rendered)
        return 0

    Path(args.output).write_text(rendered)
    print(f"[render-proxy] Generated {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
