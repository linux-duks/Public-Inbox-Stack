# Integration tests

End-to-end tests that exercise the whole mirroring → indexing → serving
pipeline with the real controller image.

## What the first test covers

`make integration-test` reproduces the "361 mirrored folders, but only a few
show up in the lore instance" failure:

1. **Upstream** (`seed/`) generates `LIST_COUNT` (default **360**) public-inbox
   v2 inboxes, each with a few synthetic messages and a `refs/meta/origins`
   blob, then serves them with `public-inbox-netd` — so `grok-pull` mirrors it
   exactly like `lore.kernel.org`, through `/manifest.js.gz` and
   `/<inbox>/git/<epoch>.git`.
2. **Controller** (`compose.yaml`) runs the repository's real image with
   `mirroring: [{id: upstream, indexing: true, extindex: true}]`, so it pulls
   every inbox, initializes/indexes it, refreshes the extindex and serves the
   result over HTTP.
3. **Assertions** (`checks.py`, running inside the controller container)
   verify layer by layer that all lists are mirrored, indexed, present in
   `PI_CONFIG`, and reachable over HTTP:

   | Layer | Check |
   | --- | --- |
   | upstream | `manifest.js.gz` advertises an epoch for every list |
   | supervisor | `grokmirror_upstream`, `index_upstream`, `grok_extindex` ran; `public-inbox-netd` is running; nothing `fatal` |
   | mirror | every list has `git/N.git` under `/data/mirror/upstream` |
   | index | every list has `msgmap.sqlite3` and a xapian dir |
   | config | every list has a `publicinbox.<name>.inboxdir` section |
   | http | `/<list>/` returns 200 and its message marker; `/all/` returns 200 |

Failure modes this is designed to catch: a pull that clones but never indexes,
indexing that stops part-way through a large list set, new inboxes never making
it into `PI_CONFIG`, and a served daemon that never picks up newly indexed
inboxes (the controller now SIGHUPs `public-inbox-netd` after indexing).

IMAP, lei, NNTP, the proxy and the Helm chart are deliberately out of scope for
this test.

## Usage

```bash
cd tests/integration

make integration-test          # full run (360 lists)
make integration-test-smoke    # fast run (20 lists)

LIST_COUNT=100 ./run.sh        # custom scale
INDEX_LEVEL=full ./run.sh      # override the index level
KEEP=1 ./run.sh                # leave the stack up for inspection
SKIP_BUILD=1 ./run.sh          # reuse a previously built image

make integration-check         # re-run the assertions on a kept stack
make integration-test-clean    # remove containers and .state/
```

Requirements: `docker compose` (v2) **or** `podman-compose`; `run.sh`
(driven by `make`) detects which one is available. No host ports are
published; everything is driven through `compose exec`.

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
.state/env/        the per-run env file with INDEXING_LEVEL
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

**Where do the progress numbers come from?** The state directories are host
bind mounts, so `run.sh` counts `.state/upstream/list_*` while waiting.
Following the container logs directly works too:
`podman-compose logs -f upstream`.

## Scale and runtime

360 lists with `INDEX_LEVEL=basic` is the default because it is close to the
production lore.kernel.org mirror that surfaced the bug, while staying
CI-friendly (roughly 10 minutes once the image is built). The first build of
the controller image compiles public-inbox and installs its Perl dependencies,
which dominates the very first run.

## CI

`.github/workflows/integration-test.yaml` runs the smoke scale on pull
requests and the full 360-list run on `main`/manual dispatch, uploading
`.state/failure.log` when a run fails.
