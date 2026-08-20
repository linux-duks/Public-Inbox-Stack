"""Configuration loading for pistack-controller.

Reads ``configs.yaml`` (structure/features) and overlays secrets/overrides
from ``.env`` using canonical path-derived environment variable names.

``imap.inboxes.0.url``       ->  ``IMAP_INBOXES_0_URL``
``lei.maildirs.rust.query``  ->  ``LEI_MAILDIRS_RUST_QUERY``
``hosting.http``             ->  ``HOSTING_HTTP``

``.env`` values take precedence over ``configs.yaml`` and are coerced to the
type of the value they override.
"""

import os
from dataclasses import dataclass, field
from pistack_controller.render import PI_CONFIG
from typing import Any, Iterator

import yaml


class ConfigError(Exception):
    """Raised when the configuration cannot be loaded."""


DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {
        "root": "/data",
        "mirror": "/data/mirror",
        "imap": "/data/imap",
        "lei": "/data/lei",
        "extindex": "/data/all",
    },
    "hosting": {
        "enabled": False,
        "http": True,
        "nntp": False,
        "imap": False,
        "serve_host": "localhost",
        "http_port": 8080,
        "https_port": 8443,
        "nntp_port": 8119,
        "imap_port": 8143,
    },
    "indexing": {"level": "full"},
    "mirroring": {
        "enabled": False,
        "upstream_host": "",
        "interval": 7200,
        "indexing": False,
        "extindex": True,
    },
    "imap": {"enabled": False, "spamcheck_enabled": False, "inboxes": []},
    "lei": {
        "enabled": False,
        "check_interval": 360,
        "output_config": "/etc/public-inbox/lei-output.pi.config",
        "default_settings": {"host": "", "threads": True, "obfuscate": True},
        "maildirs": {},
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (returns a new dict)."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# -- typed config ----------------------------------------------------------


@dataclass
class Paths:
    root: str = "/data"
    mirror: str = "/data/mirror"
    imap: str = "/data/imap"
    lei: str = "/data/lei"
    extindex: str = "/data/all"

    @classmethod
    def from_dict(cls, data: dict) -> "Paths":
        return cls(
            **{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        )


@dataclass
class Hosting:
    enabled: bool = False
    http: bool = True
    nntp: bool = False
    imap: bool = False
    serve_host: str = "localhost"
    http_port: int = 8080
    https_port: int = 8443
    nntp_port: int = 8119
    imap_port: int = 8143

    @classmethod
    def from_dict(cls, data: dict) -> "Hosting":
        return cls(
            **{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        )


@dataclass
class Indexing:
    level: str = "full"

    @classmethod
    def from_dict(cls, data: dict) -> "Indexing":
        return cls(
            **{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        )


@dataclass
class Mirroring:
    enabled: bool = False
    upstream_host: str = ""
    interval: int = 7200
    indexing: bool = False
    extindex: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Mirroring":
        return cls(
            **{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        )


@dataclass
class ImapInbox:
    id: str
    address: str
    url: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ImapInbox":
        return cls(
            **{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        )


@dataclass
class Imap:
    enabled: bool = False
    spamcheck_enabled: bool = False
    inboxes: list[ImapInbox] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Imap":
        data = data or {}
        inboxes = [ImapInbox.from_dict(x) for x in data.get("inboxes", [])]
        return cls(
            enabled=data.get("enabled", False),
            spamcheck_enabled=data.get("spamcheck_enabled", False),
            inboxes=inboxes,
        )


@dataclass
class LeiMaildir:
    name: str
    query: str
    host: str = ""
    threads: bool = True
    obfuscate: bool = True


@dataclass
class Lei:
    enabled: bool = False
    check_interval: int = 360
    serving_address: str = "localhost"
    output_config: str = "/etc/public-inbox/lei-output.pi.config"
    default_settings: dict[str, Any] = field(default_factory=dict)
    maildirs: list[LeiMaildir] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Lei":
        data = data or {}
        defaults = dict(data.get("default_settings") or {})
        maildirs: list[LeiMaildir] = []
        for name, settings in (data.get("maildirs") or {}).items():
            merged = deep_merge(defaults, settings or {})
            maildirs.append(
                LeiMaildir(
                    name=name,
                    query=str(merged.get("query", "")),
                    host=str(merged.get("host", "")),
                    threads=_to_bool(merged.get("threads", True)),
                    obfuscate=_to_bool(merged.get("obfuscate", True)),
                )
            )
        return cls(
            enabled=data.get("enabled", False),
            check_interval=int(data.get("check_interval", 360)),
            serving_address=data.get("serving_address", "localhost"),
            output_config=data.get(
                "output_config", f"{PI_CONFIG}/lei-output.pi.config"
            ),
            default_settings=defaults,
            maildirs=maildirs,
        )


@dataclass
class Config:
    paths: Paths = field(default_factory=Paths)
    hosting: Hosting = field(default_factory=Hosting)
    indexing: Indexing = field(default_factory=Indexing)
    mirroring: Mirroring = field(default_factory=Mirroring)
    imap: Imap = field(default_factory=Imap)
    lei: Lei = field(default_factory=Lei)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            paths=Paths.from_dict(data.get("paths")),
            hosting=Hosting.from_dict(data.get("hosting")),
            indexing=Indexing.from_dict(data.get("indexing")),
            mirroring=Mirroring.from_dict(data.get("mirroring")),
            imap=Imap.from_dict(data.get("imap")),
            lei=Lei.from_dict(data.get("lei")),
        )


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# -- .env path-derived overrides -------------------------------------------


def parse_env_file(env_path: str) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file, ignoring comments and blank lines."""
    env: dict[str, str] = {}
    if not os.path.exists(env_path):
        return env
    with open(env_path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            env[key] = value
    return env


def _leaf_paths(node: Any, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_paths(value, (*prefix, str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaf_paths(value, (*prefix, str(index)))
    else:
        yield prefix


def _get_path(node: Any, path: tuple[str, ...]) -> Any:
    for part in path:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _set_path(node: Any, path: tuple[str, ...], value: Any) -> None:
    for part in path[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = path[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _coerce_env(value: str, target: Any) -> Any:
    if isinstance(target, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(target, int):
        return int(value)
    if isinstance(target, float):
        return float(value)
    return str(value)


def apply_env_overrides(config: dict, env: dict[str, str]) -> dict:
    """Override config leaves whose canonical env name is set in ``env``."""
    for path in _leaf_paths(config):
        env_name = "_".join(path).upper()
        if env_name in env:
            target = _get_path(config, path)
            _set_path(config, path, _coerce_env(env[env_name], target))
    return config


# -- loader ----------------------------------------------------------------


def load_config(config_path: str | None = None, env_path: str | None = None) -> Config:
    config_path = config_path or os.environ.get("CONFIG_PATH", "configs.yaml")
    env_path = env_path or os.environ.get("ENV_PATH", ".env")

    if not os.path.exists(config_path):
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path) as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ConfigError(f"Invalid config file (expected a mapping): {config_path}")

    merged = deep_merge(DEFAULT_CONFIG, raw)
    env = parse_env_file(env_path)
    merged = apply_env_overrides(merged, env)
    return Config.from_dict(merged)
