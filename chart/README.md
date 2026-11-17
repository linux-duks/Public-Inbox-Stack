# public-inbox-stack Helm Chart

Deploys the [public-inbox-stack](..) controller (and optionally
[Anubis](https://anubis.techaro.lol/)) on Kubernetes.

## Install

Copy [`values.yaml`](values.yaml) and adapt it (config, ports, secrets), then:

```bash
helm install lore ./chart --namespace lore --create-namespace --values my-values.yaml
```

Update an existing install:

```bash
helm upgrade lore ./chart --namespace lore --values my-values.yaml
```

> The `service.http/nntp/imap` ports must match the `hosting.*_port` values in
> `config.yaml`.

### Image

In these values, you should set your image repository/tag. Check the "Publish the image" section in the root README.

### Image pull secrets

If the image lives in a private registry, give the pod credentials with
`imagePullSecrets`. Two mechanisms are supported and can be combined — the
resulting Secret names are merged into the Deployment's `imagePullSecrets`:

**Use an existing Secret** (recommended — keeps credentials out of the values
file). Create a `kubernetes.io/dockerconfigjson` Secret yourself and reference
it by name:

```bash
kubectl create secret docker-registry registry-credentials \
  --docker-server=distribution.example.tld \
  --docker-username=user --docker-password=pass \
  -n lore
```

```yaml
imagePullSecrets:
  existing:
    - registry-credentials
```

**Let the chart create the Secret from values** (handy for testing; the
credentials end up in your values file, so use SOPS/Sealed Secrets/External
Secrets for anything real):

```yaml
imagePullSecrets:
  create:
    enabled: true
    name: ""          # defaults to <fullname>-image-pull
    registry: distribution.example.tld   # must match the image repository host
    username: user
    password: pass
```

The chart renders a `kubernetes.io/dockerconfigjson` Secret for that. `registry`,
`username` and `password` are required when `create.enabled` is true. Note that
this Secret is only attached to the controller Deployment; the optional Anubis
Deployment (`anubis.enabled`) is not modified — add its credentials to the
Anubis image registry separately if needed.

### First run: disable probes

A first install has to clone/mirror the upstream inboxes and build the initial
index. That can take anywhere from minutes to hours, and the controller's
`/health` endpoint is not answering while it does. With probes enabled,
Kubernetes restarts the container once `failureThreshold` is exhausted and the
work starts over.

For that first run, switch the liveness and readiness probes off entirely:

```bash
helm install lore ./chart --namespace lore --create-namespace \
  --values my-values.yaml --set probes.enabled=false
```

```yaml
probes:
  enabled: false
```

When the initial index is built, turn them back on:

```bash
helm upgrade lore ./chart --namespace lore --values my-values.yaml
```

With `probes.enabled: false` nothing restarts a hung controller and the pod
counts as Ready as soon as the container starts, so remember to re-enable them.
If you only want to give startup more room, keep the probes and raise
`probes.startupDelaySeconds` and/or `probes.failureThreshold` instead.

## Secrets

The chart only references Secrets by name — it never creates them (the single
exception is the optional `imagePullSecrets.create` Secret above), so any
encryption method works (plain `kubectl`, SOPS, Sealed Secrets, External
Secrets Operator, ...).

IMAP credentials and other config overrides are read from a `.env` file
(`ENV_PATH`). Create a Secret and mount it as that file:

```bash
# secrets.env:  IMAP_INBOXES_0_URL=imaps://user:pass@imap.server.tld:993/INBOX
kubectl create secret generic public-inbox-secrets --from-file=.env=secrets.env -n lore
```

```yaml
secrets:
  envFile:
    enabled: true
    secretName: public-inbox-secrets
    key: .env
    mountPath: /secrets/.env
```

Every `KEY=VALUE` in the Secret overrides the matching config path
(`imap.inboxes.0.url` → `IMAP_INBOXES_0_URL`). Secrets can also be mounted as
process env vars with `secrets.envFrom` / `secrets.env`.
