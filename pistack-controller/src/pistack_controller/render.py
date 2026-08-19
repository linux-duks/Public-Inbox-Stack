"""Render runtime configuration files from Jinja2 templates.

Only configs consumed inside the controller container are templated:
the grokmirror config and the public-inbox config. nginx/anubis configs are
static files managed directly by the user.
"""

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from pistack_controller.config import Config

TEMPLATES_DIR = Path(__file__).parent / "templates"

GROKMIRROR_CONF = os.environ.get("GROKMIRROR_CONF", "/config/grokmirror.conf")
PI_CONFIG = os.environ.get("PI_CONFIG", "/etc/public-inbox/config")


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_grokmirror_conf(config: Config) -> str:
    return _environment().get_template("grokmirror.conf.j2").render(
        paths=config.paths, mirroring=config.mirroring
    )


def render_pi_config(config: Config) -> str:
    return _environment().get_template("pi-config.j2").render(
        paths=config.paths,
        hosting=config.hosting,
        mirroring=config.mirroring,
        imap=config.imap,
        lei=config.lei,
    )


def write_grokmirror_conf(config: Config) -> str:
    os.makedirs(os.path.dirname(GROKMIRROR_CONF), exist_ok=True)
    Path(GROKMIRROR_CONF).write_text(render_grokmirror_conf(config))
    print(f"[render] Generated {GROKMIRROR_CONF}")
    return GROKMIRROR_CONF


def write_pi_config(config: Config) -> str:
    os.makedirs(os.path.dirname(PI_CONFIG), exist_ok=True)
    Path(PI_CONFIG).write_text(render_pi_config(config))
    print(f"[render] Generated {PI_CONFIG}")
    return PI_CONFIG


def render_all(config: Config) -> None:
    write_grokmirror_conf(config)
    write_pi_config(config)
