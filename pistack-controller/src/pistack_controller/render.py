"""Render runtime configuration files from Jinja2 templates.

Only configs consumed inside the controller container are templated:
the grokmirror config and the public-inbox config. nginx/anubis configs are
static files managed directly by the user.

The public-inbox config is guarded by a ``base.config`` merge: the freshly
rendered output is compared against ``base.config`` (the previous render) and
only the delta is applied to the live config file. Runtime additions made by
internal processes (e.g. ``public-inbox-init`` appending inbox sections) are
preserved across restarts.
"""

import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from pistack_controller.config import Config, PI_CONFIG, GROKMIRROR_CONF

TEMPLATES_DIR = Path(__file__).parent / "templates"


_SECTION_RE = re.compile(r"^\[[^\]\n]*\]\s*$")


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_grokmirror_conf(config: Config) -> str:
    return (
        _environment()
        .get_template("grokmirror.conf.j2")
        .render(paths=config.paths, mirroring=config.mirroring)
    )


def render_pi_config(config: Config) -> str:
    return (
        _environment()
        .get_template("pi-config.j2")
        .render(
            paths=config.paths,
            hosting=config.hosting,
            mirroring=config.mirroring,
            imap=config.imap,
            lei=config.lei,
        )
    )


def write_grokmirror_conf(config: Config) -> str:
    os.makedirs(os.path.dirname(GROKMIRROR_CONF), exist_ok=True)
    Path(GROKMIRROR_CONF).write_text(render_grokmirror_conf(config))
    print(f"[render] Generated {GROKMIRROR_CONF}")
    return GROKMIRROR_CONF


# -- base.config merge for the public-inbox config --------------------------


_COMMENT_RE = re.compile(r"^\s*(?:[#;]|$)")


def _parse_sections(text: str) -> tuple[str, dict[str, str], list[str]]:
    """Split a git-config style file into (preamble, sections, order).

    ``sections`` maps a section header (``[section "sub"]``) to its raw text
    (header + body lines). ``order`` preserves the on-disk order. Comment and
    blank lines between sections attach to the *following* section so they
    stay with their section during merges.
    """
    sections: dict[str, str] = {}
    order: list[str] = []
    preamble: list[str] = []
    current_header: str | None = None
    current_lines: list[str] = []
    trailer: list[str] = []

    for line in text.splitlines(keepends=True):
        if _SECTION_RE.match(line.strip()):
            if current_header is not None:
                sections[current_header] = "".join(current_lines)
                order.append(current_header)
            current_header = line.strip()
            current_lines = trailer + [line]  # comments attach to the following section
            trailer = []
        elif current_header is None:
            preamble.append(line)
        elif _COMMENT_RE.match(line):
            trailer.append(line)
        else:
            current_lines.extend(trailer)
            current_lines.append(line)
            trailer = []

    if current_header is not None:
        sections[current_header] = "".join(current_lines) + "".join(trailer)
        order.append(current_header)
    else:
        preamble.extend(trailer)

    return "".join(preamble), sections, order


def merge_config(fresh: str, base: str | None, live: str | None) -> tuple[str, bool]:
    """3-way merge the freshly rendered config into the live config.

    Sections owned by the template (present in ``base`` or ``fresh``) are
    synchronized to the render; sections present only in ``live`` (added at
    runtime by public-inbox-init) are preserved untouched.

    Returns ``(merged_text, changed)``.
    """
    if live is None:
        return fresh, True

    fresh_preamble, fresh_sections, fresh_order = _parse_sections(fresh)
    _, base_sections, _ = _parse_sections(base) if base else ("", {}, [])
    _, live_sections, live_order = _parse_sections(live)

    managed = set(base_sections) | set(fresh_sections)

    result: list[str] = []
    for header in live_order:
        if header in fresh_sections:
            result.append(fresh_sections[header])
        elif header in base_sections:
            continue  # removed from the template
        else:
            result.append(live_sections[header])  # runtime section, preserve

    for header in fresh_order:
        if header not in live_sections:
            result.append(fresh_sections[header])  # new template section

    merged = fresh_preamble + "".join(result)
    return merged, merged != live


def sync_pi_config(config: Config) -> str:
    """Write the public-inbox config via a base.config merge."""
    fresh = render_pi_config(config)
    base_path = os.path.join(os.path.dirname(PI_CONFIG), "base.config")
    os.makedirs(os.path.dirname(PI_CONFIG), exist_ok=True)

    base = Path(base_path).read_text() if os.path.isfile(base_path) else None
    live = Path(PI_CONFIG).read_text() if os.path.isfile(PI_CONFIG) else None

    if live is None:
        Path(PI_CONFIG).write_text(fresh)
        Path(base_path).write_text(fresh)
        print(f"[render] Generated {PI_CONFIG}")
    elif base is not None and fresh == base:
        Path(base_path).write_text(fresh)
        print(f"[render] No config changes, left {PI_CONFIG} untouched")
    else:
        merged, changed = merge_config(fresh, base, live)
        if changed:
            Path(PI_CONFIG).write_text(merged)
            print(f"[render] Merged changes into {PI_CONFIG}")
        else:
            print(f"[render] No config changes, left {PI_CONFIG} untouched")
        Path(base_path).write_text(fresh)

    print(f"[render] Updated {base_path}")
    return PI_CONFIG


def render_all(config: Config) -> None:
    write_grokmirror_conf(config)
    sync_pi_config(config)
