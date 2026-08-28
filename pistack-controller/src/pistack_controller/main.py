"""pistack-controller entry point.

Subcommands:
    serve       (default) run the tinySupervisor workflow
    render      render grokmirror + public-inbox configs
    index       one-shot: index cloned repos (+ extindex)
    purge       one-shot: purge indexing data, preserving git clones
    obfuscate   one-shot: add obfuscate = true to public-inbox sections
"""

import argparse
import os
import sys

from tinysupervisor import Job, init_supervisor

from pistack_controller import render
from pistack_controller.config import Config, ConfigError, load_config, PI_CONFIG
from pistack_controller.grokmirror import register_grokmirror_tasks
from pistack_controller.indexer import index_cloned_repos, run_extindex
from pistack_controller.lei import lei_init_job, lei_up_cronjob
from pistack_controller.obfuscate import obfuscate_config
from pistack_controller.public_inbox import ensure_inboxes_job, public_inbox_services
from pistack_controller.purge import purge_indexing


def init_folders(config: Config) -> int:
    """Create the /data subfolders and the extindex description file."""
    for path in (
        config.paths.mirror,
        config.paths.imap,
        config.paths.lei,
        config.paths.extindex,
    ):
        os.makedirs(path, exist_ok=True)
    description_file = os.path.join(config.paths.extindex, "description")
    if not os.path.isfile(description_file):
        with open(description_file, "w") as handle:
            handle.write(os.environ.get("EXTINDEX_DESCRIPTION", "All Lore"))
    return 0


def build_supervisor(config: Config):
    supervisor = init_supervisor(
        verbosity=os.environ.get("TINYSUPERVISOR_VERBOSITY", "info"),
        keep_running=True,
    )

    supervisor.register(
        Job(name="init_folders", executable=lambda: init_folders(config))
    )
    hosting_depends = ["init_folders"]

    if config.lei.enabled:
        lei_init = lei_init_job(config)
        supervisor.register(lei_init)
        supervisor.register(lei_up_cronjob(config))

        hosting_depends.append(lei_init.name)

    if config.mirroring.enabled:
        register_grokmirror_tasks(supervisor, config)

    if config.hosting.enabled:
        supervisor.register(ensure_inboxes_job(config, hosting_depends))
        for service in public_inbox_services(config):
            supervisor.register(service)

    supervisor.set_http_port(int(os.environ.get("TINYSUPERVISOR_HTTP_PORT", "8081")))
    return supervisor


def cmd_serve(config: Config) -> int:
    render.render_all(config)
    return build_supervisor(config).start()


def cmd_render(config: Config) -> int:
    render.render_all(config)
    return 0


def cmd_index(config: Config) -> int:
    rc = index_cloned_repos(config)
    if rc != 0:
        return rc
    if config.mirroring.extindex:
        return run_extindex(config)
    return 0


def cmd_purge(config: Config) -> int:
    return purge_indexing(config)


def cmd_obfuscate(config: Config) -> int:
    return obfuscate_config(PI_CONFIG)


_COMMANDS = {
    "serve": cmd_serve,
    "render": cmd_render,
    "index": cmd_index,
    "purge": cmd_purge,
    "obfuscate": cmd_obfuscate,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pistack-controller")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", "configs.yaml"),
        help="path to the YAML configuration (default: configs.yaml)",
    )
    parser.add_argument(
        "--env",
        default=os.environ.get("ENV_PATH", ".env"),
        help="path to the .env secrets file (default: .env)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=list(_COMMANDS),
        help="subcommand to run (default: serve)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config, args.env)
    except ConfigError as exc:
        print(f"[pistack-controller] {exc}", file=sys.stderr)
        return 1

    return _COMMANDS[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
