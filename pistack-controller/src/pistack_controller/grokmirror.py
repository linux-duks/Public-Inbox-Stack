# run the GrokMirror tool
# use jinja2 to template the config files
# run grokmirror in one-shot mode, and use tinySupervisor CronJob for scheduling
#

from typing import Any
from tinysupervisor import CronJob


def grokmirror_cronjob(
    config: dict[str, Any] | None = None,
):
    return CronJob(
        name="grokmirror_job",
        interval="TODO get from configs",
        executable="grokmirror ",
    )
