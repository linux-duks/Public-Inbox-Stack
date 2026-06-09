#!/bin/bash
# Add "obfuscate = true" to all [publicinbox "..."] sections that are missing it.
set -euo pipefail

CONFIG_FILE="${1:-configs/pi-configs/config}"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Config file not found: $CONFIG_FILE" >&2
    exit 1
fi

awk '
BEGIN { indent = "\t" }
/^\[publicinbox "/ {
    if (in_named_section && !has_obfuscate) {
        print indent "obfuscate = true"
    }
    in_named_section = 1
    has_obfuscate = 0
    got_indent = 0
    indent = "\t"
    print
    next
}
/^\[/ {
    if (in_named_section && !has_obfuscate) {
        print indent "obfuscate = true"
    }
    in_named_section = 0
    print
    next
}
in_named_section {
    if (!got_indent && $0 ~ /^[ \t]+/) {
        match($0, /^[ \t]+/)
        indent = substr($0, RSTART, RLENGTH)
        got_indent = 1
    }
    if ($0 ~ /^[ \t]*obfuscate/) {
        has_obfuscate = 1
    }
    print
    next
}
{ print }
END {
    if (in_named_section && !has_obfuscate) {
        print indent "obfuscate = true"
    }
}
' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
