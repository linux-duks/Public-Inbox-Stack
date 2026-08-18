from typing import Any
from tinysupervisor import CronJob

# TODO: read configs, render lei commands
# for each element in the lei.maildirs list, one command should be run
# inspiration from the scrips/lei-daemon

def lei_cronjob(
    config: dict[str, Any] | None = None,
):
    return CronJob(
        name="lei_job",
        interval="TODO get from configs",
        executable="lei ",
    )
