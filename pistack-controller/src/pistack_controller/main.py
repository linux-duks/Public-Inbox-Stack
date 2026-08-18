import os
import sys

from tinysupervisor import CronJob, Job, Process, Service, init_supervisor

from config import load_config
from lei import lei_cronjob
from public_inbox import public_inbox_server
from grokmirror import grokmirror_cronjob


def main() -> int:
    configs = load_config(os.environ.get("CONFIG_PATH", "configs.yaml"))

    supervisor = init_supervisor(
        verbosity=os.environ.get("TINYSUPERVISOR_VERBOSITY", "info")
    )

    # Check if data folder is created
    supervisor.register(Job(name="init_folder", command=""))

    # if lei enabled,
    if configs["lei"]["enabled"]:
        lei_runner = lei_cronjob(configs)
        supervisor.register(lei_runner)

    if configs["hosting"]["enabled"]:
        supervisor.register(public_inbox_server(configs))

    if configs["mirroring"]["enabled"]:
        supervisor.register(grokmirror_cronjob(configs))

    supervisor.set_http_port(int(os.environ.get("TINYSUPERVISOR_HTTP_PORT", "8081")))

    return supervisor.start()


if __name__ == "__main__":
    sys.exit(main())
