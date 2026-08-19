"""Grokmirror scheduling task.

Runs ``grok-pull`` as a one-shot job on an interval; tinySupervisor handles
the scheduling (replacing grokmirror's own ``--continuous`` daemon mode).
"""

from tinysupervisor import CronJob

from pistack_controller.config import Config
from pistack_controller.render import GROKMIRROR_CONF


def grokmirror_cronjob(config: Config) -> CronJob:
    return CronJob(
        name="grokmirror",
        interval=float(config.mirroring.interval),
        command=f"grok-pull -v -c {GROKMIRROR_CONF}",
        depends=["init_folders"],
    )
