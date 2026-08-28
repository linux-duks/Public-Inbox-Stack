"""Grokmirror scheduling task.

Runs ``grok-pull`` as a one-shot job on an interval; tinySupervisor handles
the scheduling (replacing grokmirror's own ``--continuous`` daemon mode).
"""

from tinysupervisor import CronJob, RecurrentJob, supervisor

from pistack_controller.config import Config
from pistack_controller.indexer import index_cloned_repos, run_extindex
from pistack_controller.render import GROKMIRROR_CONF


def grokmirror_cronjob(config: Config) -> CronJob:
    return CronJob(
        name="grokmirror",
        interval=float(config.mirroring.interval),
        command=f"grok-pull -v -c {GROKMIRROR_CONF}",
        depends=["init_folders"],
    )


def register_grokmirror_tasks(supervisor: supervisor, config: Config):
    supervisor.register(grokmirror_cronjob(config))

    extindex_depends = ["grokmirror"]

    if config.mirroring.indexing:
        supervisor.register(
            RecurrentJob(
                name="index",
                executable=lambda: index_cloned_repos(config),
                depends=["grokmirror"],
            )
        )
        # also wait for internal indexing
        extindex_depends.append("index")

    if config.mirroring.extindex:
        supervisor.register(
            RecurrentJob(
                name="grok_extindex",
                executable=lambda: run_extindex(config),
                depends=extindex_depends,
            )
        )
