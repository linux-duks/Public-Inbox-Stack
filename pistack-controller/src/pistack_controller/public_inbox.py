"""public-inbox long-running serving services.

Port of ``scripts/start-public-inbox.sh``. The multi-protocol
``public-inbox-netd`` daemon (HTTP/IMAP/NNTP) replaces the standalone
``-httpd``/``-nntpd``/``-imapd`` daemons. Each daemon is a tinySupervisor
``Service`` (kept alive, autorestarted), replacing the old PID-juggling loop.
"""

from tinysupervisor import Job, CronJob, Service

from pistack_controller.config import Config
from pistack_controller.reinit import ensure_inboxes
from pistack_controller.indexer import run_extindex

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
        autorestart=True,
    )


def _netd_command(config: Config) -> str:
    # Listener addresses come from the config (hosting.*_port). These are the
    # internal ports nginx proxies to, so they must stay in sync with the
    # proxy config.
    listeners = []
    if config.hosting.http:
        listeners.append(f"http://0.0.0.0:{config.hosting.http_port}")
    if config.hosting.nntp:
        listeners.append(f"nntp://0.0.0.0:{config.hosting.nntp_port}")
    if config.hosting.imap:
        listeners.append(f"imap://0.0.0.0:{config.hosting.imap_port}")

    return "public-inbox-netd " + " ".join(f"-l {listener}" for listener in listeners)


def public_inbox_services(config: Config) -> list[Service | CronJob]:
    depends = ["ensure_inboxes"] if config.hosting.enabled else []
    services: list[Service | CronJob] = []

    if config.imap.spamcheck_enabled:
        services.append(_service("spamd", SPAMD_COMMAND, depends))

    watch_depends = list(depends)
    if config.lei.enabled:
        watch_depends.append("lei_init")

    if config.imap.enabled or config.lei.enabled:
        services.append(
            _service("public-inbox-watch", "public-inbox-watch", watch_depends)
        )

    if config.hosting.http or config.hosting.nntp or config.hosting.imap:
        services.append(_service("public-inbox-netd", _netd_command(config), depends))

    # TODO: check and add configs
    services.append(
        CronJob(
            name="extindex_cr",
            executable=lambda: run_extindex(config),
            depends=depends,
            interval=7200,
        )
    )

    return services


def ensure_inboxes_job(
    config: Config, additional_depends: list[str] | None = []
) -> Job:
    return Job(
        name="ensure_inboxes",
        executable=lambda: ensure_inboxes(config),
        depends=additional_depends,
    )
