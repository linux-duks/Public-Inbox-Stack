# Public-Inbox-Stack

Scripts and containers for igesting, mirroring and/or hosting mailing lists with [public-inbox](https://public-inbox.org/). Batteries included.

Everything runs inside a **single controller container** (`public-inbox-stack`)
that orchestrates mirroring, indexing, lei imports and the public-inbox serving
daemons with [tinySupervisor](https://gitlab.com/auyer/tinySupervisor).
Nginx (Angie) and Anubis ar optional, and live in a separate proxy compose file.

Supports **mirroring only**, **hosting only**, **ingesting IMAP**, **filtering with lei** or **any combination of the above** :).

## Architecture

```
Upstream (public-inbox.example.org, etc.)
        │
        │  grok-pull (git clone/fetch), CronJob
        ▼
┌──────────────────────────────────────────────────────┐
│ public-inbox-stack  (single container)               │
│   pistack-controller  (tinySupervisor DAG)           │
│     ├─ init_folders   (Job)                          │
│     ├─ grokmirror     (CronJob -> /data/mirror)      │
│     ├─ index/extindex (run_after, optional)          │
│     ├─ lei_init/lei_up (Job + CronJob -> /data/lei)  │
│     └─ public-inbox-httpd/nntpd/imapd/watch/spamd    │
│         (Service)                                    │
└───────────────┬──────────────────────────────────────┘
                │  port 8080 (pi) / 8081 (supervisor)
                ▼
┌────────────────────────────┐
│  proxy.compose.yaml        │
│    nginx (Angie) + anubis  │
└────────────────────────────┘
        │
        ▼  http://your-host:8088
```

## Requirements

- **podman** + **podman-compose**
  _or_
- **docker** + **docker-compose**

Runtime is auto-detected. See [Container Runtime](#container-runtime).

## Quick Start

This repository relies on git submodules. Clone recursively, or run
`git submodule update --init --recursive` after cloning.

```bash
# 1. Create your environment configuration
cp example.configs.yaml configs.yaml

# 2. (Optional) add secrets/overrides to .env, e.g. IMAP credentials
cp .env.example .env
vim .env

# 3. Start everything (setup/rendering happens inside the container)
make run
# or, if you use the proxy stack too:
cp proxy/proxy.config.example proxy/proxy.config
make run-with-proxy
```

Visit `http://localhost:8081/health` for the controller health check and
`http://localhost:8081/state` for the task states.

## Configuration

### `configs.yaml`

All configuration lives in a single YAML file (`configs.yaml`, gitignored,
based on `example.configs.yaml`):

```yaml
paths:
  root: /data
  mirror: /data/mirror
  imap: /data/imap
  lei: /data/lei
  extindex: /data/all

hosting:
  enabled: true
  http: true
  nntp: true
  imap: false
  serve_host: lore.domain.tld
  http_port: 80
  https_port: 8443
  nntp_port: 8119
  imap_port: 143

indexing:
  level: full            # basic | medium | full

mirroring:
  enabled: true
  upstream_host: https://lore.other-domain.tld
  interval: 7200         # seconds between grok-pull runs
  indexing: false        # index after each pull
  extindex: true         # update the cross-inbox index

imap:
  enabled: false
  spamcheck_enabled: false
  inboxes:
    - id: list.name
      address: name.list.domain.tld
      url: imaps://user:pass@imap.server.tld:993/INBOX

lei:
  enabled: false
  check_interval: 360    # minutes between lei up --all runs
  output_config: lei-output.pi.config # will live in the PI_CONFIG folder
  default_settings:
    host: https://lore.kernel.org/all
    threads: true
    obfuscate: true
  maildirs:
    "rust":
      query: '(s:rust OR dfn:rust/*) AND rt:2.weeks.ago..'
```

### Secrets from `.env`

Any leaf value in `configs.yaml` can be overridden from `.env` using the
UPPER_SNAKE_CASE of its dotted path (`.` → `_`, list indices are numeric):

| Config path | Env var |
|-------------|---------|
| `imap.inboxes.0.url` | `IMAP_INBOXES_0_URL` |
| `lei.maildirs.rust.query` | `LEI_MAILDIRS_RUST_QUERY` |
| `hosting.http` | `HOSTING_HTTP` |

```bash
# .env (gitignored) - keep credentials here, not in configs.yaml
IMAP_INBOXES_0_URL=imaps://user:pass@imap.server.tld:993/INBOX
```

`.env` values win over `configs.yaml` and are coerced to the type of the value
they override.

### Data layout

Mirroring, IMAP and lei use separate subfolders of a shared `/data` parent:

| Domain | Path |
|--------|------|
| Mirror (grokmirror toplevel) | `/data/mirror` |
| IMAP watch inboxes | `/data/imap/<inbox-id>` |
| Lei (base-dir) | `/data/lei` |
| extindex (`all`, cross-inbox aggregate) | `/data/all` |

> **Migration:** existing installs should move host `./maildirs/*` →
> `./data/lei/` and `./data/<inbox>` → `./data/mirror/` or `./data/imap/`.

## Makefile Targets

### Lifecycle

| Target | Description |
|--------|-------------|
| `make run` | Start the stack (`docker compose up -d`) |
| `make watch` | Start in the foreground |
| `make stop` | Stop all services |
| `make logs` | Follow logs for all services |
| `make build` | Build the controller image |

### One-shot operations

| Target | Description |
|--------|-------------|
| `make render` | Render grokmirror + public-inbox configs |
| `make proxy-config` | Render `proxy/angie.conf` from the Jinja2 template + `proxy.config` |
| `make index` | One-shot indexing of cloned repos (+ extindex) |
| `make purge` | Purge public-inbox indexing data (preserves git clones) |
| `make obfuscate` | Add `obfuscate = true` to all inbox configs missing it |
| `make help` | Show all available targets |

## Workflows

### Mirroring + Hosting

```bash
cp example.configs.yaml configs.yaml
# set mirroring.enabled: true, upstream_host, hosting.enabled: true
make run
```

### Hosting only (no mirroring)

```bash
# set hosting.enabled: true, mirroring.enabled: false
# add inbox definitions under imap.inboxes or edit the rendered config
make run
```

### Lei: Importing messages via saved searches

1. Set `lei.enabled: true` in `configs.yaml`
2. Add maildirs (name → query) under `lei.maildirs`
3. `make run`

On startup the controller creates `lei q` saved searches, initializes the
public-inbox V2 inboxes, generates `lei-output.pi.config`, then runs
`lei up --all` every `check_interval` minutes.

**Per-maildir keys:**

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `query` | yes | — | Search terms using [lei-q prefixes](https://public-inbox.org/lei-q.html) |
| `host` | no | `default_settings.host` | Remote public-inbox URL to search |
| `threads` | no | `default_settings.threads` | Pull entire threads (`-t` flag) |
| `obfuscate` | no | `default_settings.obfuscate` | Obfuscate addresses in web UI |

## Services Detail

### Controller (`public-inbox-stack`)

Runs `pistack-controller serve` — a tinySupervisor workflow:

- **`init_folders`** (Job) — creates `/data/{mirror,imap,lei,all}` and the extindex description
- **`grokmirror`** (CronJob) — `grok-pull` on `mirroring.interval`
- **`index` / `extindex`** (Jobs, `run_after`) — index cloned repos / update extindex
- **`lei_init`** (Job) + **`lei_up`** (CronJob) — saved searches + periodic updates
- **`ensure_inboxes`** (Job) — reinitialize missing inbox dirs from the config
- **`public-inbox-netd`** (Service) — single multi-protocol daemon serving
  HTTP (`:8080`), NNTP (`:119`) and IMAP (`:143`) per the `hosting.*` flags
- **`public-inbox-watch`** (Service) — when IMAP or lei is enabled
- **`spamd`** (Service) — when `imap.spamcheck_enabled`

Observability: `/health`, `/state`, `/metrics` on port 8081.

### nginx (Angie) + Anubis (`proxy.compose.yaml`)

Angie proxies HTTP/HTTPS to `public-inbox-stack:8080`, streams NNTP to
`:119` and IMAP to `:143`. When Anubis is enabled, traffic first hits Angie,
is forwarded to Anubis for a proof-of-work challenge, then proxied back via a
Unix socket.

**`proxy/angie.conf` is generated** from `proxy/angie.conf.j2` (Jinja2) using
`proxy/proxy.config` (KEY=VALUE, gitignored, see `proxy/proxy.config.example`):

- `make proxy-config` (or `make proxy-config-dry-run` to preview) renders the config.
  It uses `uv` natively when available, otherwise it falls back to running uv
  from the `ghcr.io/astral-sh/uv:debian` container image (no host `uv` needed).
- `make run-with-proxy` renders it and starts the stack with the proxy compose file
- The same `proxy.config` feeds `${...}` external-port interpolation in
  `proxy.compose.yaml` via `--env-file` (`HTTP_PORT`, `HTTPS_PORT`, `NNTP_PORT`,
  `IMAP_PORT`, `ANUBIS_PORT`), so you can publish e.g. HTTP on a non-80 host port
- `HTTP_ENABLED`/`HTTPS_ENABLED`/`NNTP_ENABLED`/`IMAP_ENABLED` toggle whether each
  protocol is served by Angie

Edit directly instead:

- `proxy/botPolicy.yaml` — Anubis bot policy

## Container Runtime

The project auto-detects your container runtime with this priority:

1. **podman** + **podman-compose**
2. **docker compose** (v2 plugin)
3. **docker-compose** (v1 standalone)

Override the detected runtime:

```bash
make run CONTAINER=nerdctl COMPOSE="nerdctl compose"
make run COMPOSE=docker-compose
```

When running with `sudo`, the real user/group ID is preserved so files in
`data/` are owned by your user, not root.

## Troubleshooting

### Config generation

No separate setup step is needed — `pistack-controller serve` renders the
grokmirror and public-inbox configs into the mounted `configs/` directories at
startup. To re-render manually: `make render`.

The public-inbox config is protected by a `base.config` merge: the freshly
rendered output is diffed against the previous render (`/etc/public-inbox/base.config`)
and only the delta is applied to the live config. Runtime additions made by
internal processes (e.g. mirrored inbox sections added by `public-inbox-init`)
are preserved across restarts.

### Missing configs.yaml

```
[ERROR] Config file not found: configs.yaml
```

Run `cp example.configs.yaml configs.yaml` and edit it.

### No v2 inboxes found

The indexer scans `/data/mirror` for directories with `git/N.git` structure. If
grokmirror hasn't finished cloning yet, wait for the next pull cycle or run
`make index` manually.

### Container permission errors

Check ownership of `data/`:

```bash
ls -la data/
```

Fix with: `sudo chown -R $(id -u):$(id -g) data/`

### Purging Indexing Data

```bash
make purge
```

Removes public-inbox indexing artifacts (Xapian, msgmap, over.sqlite3, all.git,
external index) while preserving grokmirror git clones. After purging, run
`make index` to rebuild.

### Email Obfuscation

```bash
make obfuscate
```

Adds `obfuscate = true` to every `[publicinbox "..."]` section in the rendered
public-inbox config that doesn't already have it.

## Developing the controller

```bash
cd pistack-controller
uv venv && uv pip install .
.venv/bin/pistack-controller --help
```

## TODO

- [ ] There is still some behaviours that change the public inbox config files.
   I would like to make the main config link to external files, and make sure they are being changed instead of the main file.
- [ ] add a obfuscate config option, managed automatically. (can the indexers set it by default?)
  - when obsfuscate is on, add a step after grokmirror to set obfuscation in the config file
- [ ] Make grokmirror use a different config file
