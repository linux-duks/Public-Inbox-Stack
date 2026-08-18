from typing import Any
from tinysupervisor import Service

# TODO: render the Public inbox configs with Jinja
#
# WARNING: public-inbox may alter the configuration files.
# The configs for the mirrored lists, lei and imap sources need to ne present

def public_inbox_server(config: dict[str, Any] | None = None,):
    return Service(
        name="public-inbox",
    )
    
