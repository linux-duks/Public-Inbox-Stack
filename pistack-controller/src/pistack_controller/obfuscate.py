"""Add ``obfuscate = true`` to public-inbox sections missing it.

Port of ``scripts/obfuscate.sh``.
"""

import os
import re

_SECTION_RE = re.compile(r'^\[publicinbox "')
_SECTION_END_RE = re.compile(r"^\[[^]]*\]")
_OBFUSCATE_RE = re.compile(r"^[ \t]*obfuscate\b")


def obfuscate_config(pi_config: str) -> int:
    if not os.path.isfile(pi_config):
        print(f"[obfuscate] Config file not found: {pi_config}")
        return 1

    with open(pi_config) as handle:
        lines = handle.readlines()

    out: list[str] = []
    in_named_section = False
    has_obfuscate = False
    indent = "\t"
    got_indent = False

    for line in lines:
        stripped = line.strip()
        if _SECTION_RE.match(stripped):
            if in_named_section and not has_obfuscate:
                out.append(indent + "obfuscate = true\n")
            in_named_section = True
            has_obfuscate = False
            got_indent = False
            indent = "\t"
            out.append(line)
            continue
        if _SECTION_END_RE.match(stripped):
            if in_named_section and not has_obfuscate:
                out.append(indent + "obfuscate = true\n")
            in_named_section = False
            out.append(line)
            continue
        if in_named_section:
            if not got_indent and line[:1] in (" ", "\t"):
                indent = line[: len(line) - len(line.lstrip())]
                got_indent = True
            if _OBFUSCATE_RE.match(line):
                has_obfuscate = True
            out.append(line)
            continue
        out.append(line)

    if in_named_section and not has_obfuscate:
        out.append(indent + "obfuscate = true\n")

    with open(pi_config, "w") as handle:
        handle.writelines(out)
    print(f"[obfuscate] Updated {pi_config}")
    return 0
