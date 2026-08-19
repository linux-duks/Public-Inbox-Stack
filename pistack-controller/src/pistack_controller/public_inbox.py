"""public-inbox long-running serving services.

Port of ``scripts/start-public-inbox.sh``. Each daemon is a tinySupervisor
``Service`` (kept alive, autorestarted), replacing the old PID-juggling loop.
"""

from tinysupervisor import Job, Service

from pistack_controller.config import Config
from pistack_controller.reinit import ensure_inboxes

SPAMD_COMMAND = (
    "spamd --username debian-spamd -l --nouser-config "
    "--syslog stderr --pidfile /var/run/spamd.pid "
    "--helper-home-dir /var/lib/spamassassin"
)


def _service(name: str, command: str, depends: list[str]) -> Service:
    return Service(
        name=name,
        command=command,
        depends=depends,
        dependency_mode="completed",
        autorestart=True,
    )


def public_inbox_services(config: Config) -> list[Service]:
    depends = ["ensure_inboxes"] if config.hosting.enabled else []
    services: list[Service] = []

    if config.hosting.http:
        services.append(_service("public-inbox-httpd", "public-inbox-httpd", depends))
    if config.hosting.nntp:
        services.append(_service("public-inbox-nntpd", "public-inbox-nntpd", depends))
    if config.hosting.imap:
        services.append(_service("public-inbox-imapd", "public-inbox-imapd", depends))
    if config.imap.spamcheck_enabled:
        services.append(_service("spamd", SPAMD_COMMAND, depends))

    watch_depends = list(depends)
    if config.lei.enabled:
        watch_depends.append("lei_init")
    if config.imap.enabled or config.lei.enabled:
        services.append(_service("public-inbox-watch", "public-inbox-watch", watch_depends))

    return services


def ensure_inboxes_job(config: Config) -> Job:
    return Job(
        name="ensure_inboxes",
        executable=lambda: ensure_inboxes(config),
        depends=["init_folders"],
        dependency_mode="completed",
    )
