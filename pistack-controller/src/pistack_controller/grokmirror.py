"""Grokmirror scheduling tasks.

Runs ``grok-pull`` as a one-shot job per mirror source on an interval;
tinySupervisor handles the scheduling (replacing grokmirror's own
``--continuous`` daemon mode). Every source has its own rendered grokmirror
config (``grokmirror-<id>.conf``) and its own toplevel directory
(``<paths.mirror>/<id>``).
"""

from tinysupervisor import CronJob, RecurrentJob, Supervisor

from pistack_controller.config import Config, MirrorSource, grokmirror_conf_path
from pistack_controller.indexer import index_cloned_repos, run_extindex


def grokmirror_cronjob(source: MirrorSource) -> CronJob:
    return CronJob(
        name=f"grokmirror_{source.id}",
        interval=float(source.interval),
        command=f"grok-pull -v -c {grokmirror_conf_path(source.id)}",
        depends=["init_folders"],
    )


def register_grokmirror_tasks(supervisor: Supervisor, config: Config):
    """Register one pull (+ optional index) job per source, plus extindex."""

    extindex_depends: list[str] = []

    for source in config.mirroring.sources:
        pull_job = grokmirror_cronjob(source)
        supervisor.register(pull_job)
        source_depends = [pull_job.name]

        if source.indexing:
            index_job = RecurrentJob(
                name=f"index_{source.id}",
                executable=lambda src=source: index_cloned_repos(config, [src]),
                depends=[pull_job.name],
            )
            supervisor.register(index_job)
            # also wait for internal indexing
            source_depends.append(index_job.name)

        if source.extindex:
            extindex_depends.extend(source_depends)

    if extindex_depends:
        supervisor.register(
            RecurrentJob(
                name="grok_extindex",
                executable=lambda: run_extindex(config),
                depends=extindex_depends,
            )
        )
