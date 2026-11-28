# Integration tests

End-to-end tests that exercise the whole mirroring → indexing → serving
pipeline with the real controller image, in three scenarios.

## How every scenario is built

1. **Upstream** (`seed/`) generates public-inbox v2 inboxes, each with a few
   synthetic messages and a `refs/meta/origins` blob, then serves them with
   `public-inbox-netd` — so `grok-pull` mirrors it exactly like
   `lore.kernel.org`, through `/manifest.js.gz` and
   `/<inbox>/git/<epoch>.git`.
2. **Controller** (`compose.yaml`) runs the repository's real image with
   `mirroring: [{id: upstream, indexing: true, extindex: true}]`, so it pulls
   every inbox, initializes/indexes it, refreshes the extindex and serves the
   result over HTTP.
3. **Assertions** (`checks.py`, running inside the controller container) verify
   the result layer by layer:

   | Layer | Check |
   | --- | --- |
   | upstream | `manifest.js.gz` advertises an epoch for every list |
   | supervisor | `grokmirror_upstream`, `index_upstream`, `grok_extindex` ran; `public-inbox-netd` is running; nothing `fatal` |
   | mirror | every list has `git/N.git` under `/data/mirror/upstream` |
   | index | every list has `msgmap.sqlite3` and a xapian dir |
   | config | every list has a `publicinbox.<name>.inboxdir` section |
   | http | `/<list>/` returns 200 and its message marker; `/all/` returns 200 |

## Scenarios

| Scenario | What it proves |
| --- | --- |
| `baseline` | N lists are mirrored, indexed, in `PI_CONFIG` and served. Covers the "361 mirrored folders but only a few show up" bug (a served daemon that never reloads, indexing that stops part-way). |
| `incremental` | After the first cycle, the upstream gains **new lists** and **new messages in existing lists**. A second grok-pull + index + extindex must pick both up (`run_count >= 2`), the new lists must be served, each existing list must serve its `list_i new 0` marker, and every mirrored ref must still equal the upstream's (`--consistency`). |
| `concurrency` | With a 3s pull interval, grok-pull runs while an index or extindex pass is still running (`--overlap-detect` waits for that window). The stack must not go `fatal`, must converge, and the mirror must stay ref-consistent with the upstream. This is where a pull holding a repository lock meets the indexer. |

The incremental scenario needs controller behaviour that the baseline does not:
already-initialized inboxes are **re-indexed** (not skipped), so messages that
grok-pull brings into an existing epoch become searchable/visible. The
concurrency scenario needs the indexer to **defer** inboxes whose epoch repo is
locked by an in-flight pull (or not fully cloned), instead of indexing a
truncated history or reporting a failure.

## Usage

```bash
cd tests/integration

make integration-test              # all three scenarios (large scale)
make integration-test-smoke        # all three scenarios (small scale)
make integration-test-baseline     # one scenario at a time
make integration-test-incremental
make integration-test-concurrency

make integration-check             # re-run assertions on a KEEP=1 stack
make integration-test-clean        # remove containers and .state/
```

Scale knobs (all overridable):

| Scenario | Default (full) | Default (smoke) | Variables |
| --- | --- | --- | --- |
| baseline | 360 lists | 20 | `LIST_COUNT` |
| incremental | 40 lists, +10 lists, +2 msgs/list, 60s interval | 20, +5, +2, 60s | `INC_LIST_COUNT`, `ADD_LIST_COUNT`, `NEW_EMAILS_PER_LIST`, `INC_INTERVAL` |
| concurrency | 150 lists, 3s interval | 60, 3s | `CONC_LIST_COUNT`, `CONC_INTERVAL`, `CONC_NEW_EMAILS` |

Global knobs: `INDEX_LEVEL` (default `basic`), `ITEST_TIMEOUT` (settle
timeout), `ITEST_OVERLAP_TIMEOUT`, `MESSAGES_PER_LIST`, `KEEP=1`,
`SKIP_BUILD=1`.

```bash
LIST_COUNT=100 ./run.sh baseline
INC_LIST_COUNT=20 ADD_LIST_COUNT=5 NEW_EMAILS_PER_LIST=2 ./run.sh incremental
CONC_LIST_COUNT=30 CONC_INTERVAL=3 ./run.sh concurrency
KEEP=1 ./run.sh concurrency        # leave it up and poke at it
```

`./run.sh` with no argument runs the baseline scenario, so
`LIST_COUNT=20 ./run.sh` keeps working.

## Re-checking a kept stack

`KEEP=1` leaves the last scenario running. `make integration-check` re-runs the
assertions without waiting; use the `CHECK_*` variables to match the scenario:

```bash
CHECK_BASELINE_COUNT=40 CHECK_LIST_COUNT=50 CHECK_NEW_EMAILS=2 \
  CHECK_CONSISTENCY=1 make integration-check
```

## Requirements

`docker compose` (v2) **or** `podman-compose`; `run.sh` (driven by `make`)
detects which one is available. No host ports are published; everything is
driven through `compose exec`.

podman-specific configuration lives in `compose.podman.yaml` and is added
automatically when the compose command is podman-compose. It sets
`userns_mode: keep-id`, which rootless podman needs so the container user can
write the bind-mounted `.state/` directories (Docker rejects that key, hence
the separate file).

`run.sh` keeps its working data under `.state/` (gitignored):

```text
.state/upstream/   generated inboxes served by the fake upstream
.state/data/       the controller's /data (mirror, extindex, ...)
.state/configs/    the controller's /configs (rendered configs)
.state/env/        per-scenario env (INDEXING_LEVEL, MIRRORING_0_INTERVAL)
.state/failure.log diagnostics collected when a run fails
```

## Troubleshooting

**`still waiting for upstream manifest`.** This is expected for the first
minute or two: the upstream container generates every inbox on disk before it
starts serving. The wait line shows what is happening, for example:

```text
==> still waiting for upstream manifest (1738s left) [pistack-integration_upstream_1 Up 35 seconds · 214/360 lists generated]
```

If the container is not `Up`, `run.sh` stops waiting immediately, prints its
state and dumps `compose logs` (plus `/state`, the rendered config, the mirror
count and per-task supervisor logs) to `.state/failure.log`. Common causes:

- `ERROR: /data is not writable as uid …` — the container uid does not own the
  state directory. With rootless podman this means the podman overrides were
  not applied: run `./run.sh` (or `make integration-test`) rather than
  `podman-compose up` directly, since run.sh adds `compose.podman.yaml`
  (`userns_mode: keep-id`, skipped automatically for rootful podman).
- `Permission denied` while reading `/seed/entrypoint.sh` — SELinux is
  enforcing and the bind mount is not labelled; the compose files already use
  the `z` label, so this only happens with a hand-written override.

**`unrecognized arguments: -a`** comes from an old `podman-compose` when `ps`
is called with `-a`. `run.sh` handles this (podman-compose always lists exited
containers itself); calling `podman-compose ps -a` by hand does not.

**Incremental: `timed out … requires >= 2 run(s)`.** The second cycle starts
after `INC_INTERVAL` (60s by default); raise `ITEST_TIMEOUT` if the second
index pass is slow, and check `index_upstream`/`grok_extindex` run counts in
`.state/failure.log`.

**Concurrency: `no grok-pull/index overlap observed`.** The pull interval was
too long relative to the index pass, or the index ran out of work. Lower
`CONC_INTERVAL` or raise `CONC_LIST_COUNT` so an index pass outlasts one pull
interval; `ITEST_OVERLAP_TIMEOUT` bounds the wait.

**Where do the progress numbers come from?** The state directories are host
bind mounts, so `run.sh` counts `.state/upstream/list_*` while waiting.
Following the container logs directly works too:
`podman-compose logs -f upstream`.

## Scale and runtime

360 lists with `INDEX_LEVEL=basic` is the baseline default because it is close
to the production lore.kernel.org mirror that surfaced the original bug. The
incremental and concurrency scenarios use smaller sets so the whole `full`
profile stays CI-friendly; the first build of the controller image (compiling
public-inbox and its Perl dependencies) dominates the very first run.

## CI

Both forges run the same profiles:

| Pipeline | Profile | Job |
| --- | --- | --- |
| `.github/workflows/integration-test.yaml` | `smoke` on pull requests, `full` on `main`/manual dispatch | GitHub-hosted runner |
| `.gitlab-ci/integration-test.yml` | `smoke` on merge requests, `full` on the default branch, manual otherwise | self-hosted runner, docker socket mounted |

Both upload `.state/failure.log` (the collected diagnostics) when a run fails.

The GitLab job drives the runner's docker daemon through
`/var/run/docker.sock` instead of a `docker:dind` service: the compose files
bind-mount `seed/`, `configs.yaml`, `checks.py` and `.state/` from the
checkout, so the daemon must see the same filesystem paths as the job. That is
why the job sets a per-pipeline `COMPOSE_PROJECT_NAME` (concurrent pipelines
share the daemon) and why the jobs are serialized with `resource_group` (they
build the same image tag).
