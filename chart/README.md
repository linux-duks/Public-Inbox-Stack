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

## Secrets

The chart only references Secrets by name — it never creates them, so any
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
