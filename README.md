# odoo-cloud — a mini SaaS control plane for Odoo on Kubernetes

Provision an **isolated Odoo per customer** on Kubernetes, driven from Odoo itself. Create a record and a full tenant is born — Odoo, its database and its storage — in its own namespace. Archive it and it is torn down. Clone a running tenant into a testing copy, take backups, and restore any backup either in place or into an isolated copy.

> ⚠️ Meant to run **locally on minikube**. It is a compact model of how a namespace-per-customer SaaS works, not a production-ready product.

---

## What it does

- **Create** a `cloud.instance` record → Helm deploys a tenant: Odoo + PostgreSQL (CloudNativePG) + Ingress routing.
- **Archive** → full namespace teardown, and its backups are deleted with it (the record is archived, not deleted).
- **Clone** a *production* instance → creates `test.<name>` (then `test2`, `test3`…) copying **the database and the filestore** from the origin.
- **Backups & restore** → on-demand and scheduled base backups to S3 (MinIO) via CloudNativePG; every backup is tracked and can be restored **in place** or into an **isolated test copy** for inspection.
- **Neutralized test copies** → every testing instance (clone or restore-to-test) is run through `odoo neutralize`, so it can't send real emails or fire crons.
- **Roles** → two groups, *User* and *Manager*: only a Manager can archive a production instance or restore it in place; any User can spin up a test copy from a backup.
- **Versions** → each instance runs a selectable Odoo image (`cloud.odoo.version`).
- **Async operations** → provision / deprovision / restore run through a task queue (`cloud.task` + cron), so the UI never blocks on long Kubernetes operations.
- Subdomain routing: the main at `19.localhost`, each tenant at `<name>.19.localhost`.

## Architecture

```
  browser  ──►  Ingress NGINX  ──►  Main Odoo (ns: odoo)
                                      │  "cloud" module
                                      │  talks to the k8s API (ServiceAccount + RBAC)
                                      ▼
                                   helm upgrade --install
                                      ▼
               ┌───────────────── namespace per customer ────────────────┐
               │  Odoo (stateless)   +   PostgreSQL (CloudNativePG)       │
               └──────────────────────────────────────────────────────────┘
                                      │ attachments
                                      ▼
                                MinIO (S3) shared  →  Odoo fully stateless
```

- **Odoo is stateless**: sessions in the database (`session_db`), attachments in MinIO (`fs_attachment`), nothing on the pod's local disk.
- **Postgres per customer**, managed by the **CloudNativePG** operator (`kind: Cluster`), which also handles WAL archiving, backups and restore.
- **Shared MinIO**, one prefix per customer for attachments and one for backups.
- **The main is just another tenant**: it is rendered by the same Helm chart with `isMain=true`, which additionally gives it MinIO, the provisioner RBAC and the `cloud` module.

## Stack

| Piece | Role |
|-------|------|
| Kubernetes (minikube) | Orchestration |
| Helm | One chart for both main and tenants (`infra/charts/tenant`) |
| CloudNativePG | PostgreSQL operator (clusters, backups, restore) |
| MinIO | S3 object storage for attachments and backups |
| Ingress NGINX | Host-based HTTP routing |
| Odoo 19 | The app + the `cloud` control-plane module |
| OCA `fs_attachment`, `session_db` | Statelessness |

## Repository layout

```
.
├── Makefile              # up / down / open / proxy / status / tenants …
├── scripts/              # up.sh / down.sh (driven by the Makefile)
├── infra/
│   └── charts/tenant/    # Helm chart: renders both the main (isMain=true) and each tenant
└── image/                # the Odoo image
    ├── Dockerfile
    ├── addons/           # fs_minio (S3 config) + cloud (control plane)
    ├── container/        # database init entrypoint
    └── requirements.in
```

## Requirements

- Docker Desktop (~8 GB for minikube; more if you plan to run several tenants)
- [minikube](https://minikube.sigs.k8s.io/), [kubectl](https://kubernetes.io/docs/tasks/tools/), [helm](https://helm.sh/)

## Usage

```bash
# starts minikube, the CNPG operator, builds the image and deploys the main stack
make up

# exposes the Ingress on localhost:8090 (leave it running in another terminal)
make proxy
```

Then, in the browser (`admin` / `admin`):

- **Main**: http://19.localhost:8090 → **Cloud → Instances** menu
- **Tenant**: http://\<name\>.19.localhost:8090

> Browsers resolve `*.localhost` to `127.0.0.1` on their own — no DNS setup needed.

From **Cloud → Instances** you can create an instance (**New**), archive it (⚙️ Archive → teardown), unarchive it (recreate), clone a production one to testing (**Clone to test**), take a backup (**Backup now**) and, from the **Backups** list, restore any backup **in place** or **into a test copy**.

### Roles

| Action | User | Manager |
|--------|:----:|:-------:|
| Create instances, clone, backup | ✅ | ✅ |
| Restore a backup **into a test copy** (incl. the main) | ✅ | ✅ |
| Archive a **production** instance | — | ✅ |
| Restore a **production** instance **in place** | — | ✅ |

The **main** instance can be backed up and restored into a test copy, but never restored in place — it is the instance running the operation, so it can't tear itself down mid-restore.

### Configuration

A single system parameter defines the base domain, and it works for both local and production:

| Parameter | Local | Production |
|-----------|-------|------------|
| `cloud.base_domain` | `19.localhost:8090` | `example.com` |

The `:port` is only used for the link; the Ingress host is always without a port.

## Useful commands

```bash
make status     # pods of the main stack
make tenants    # list tenants (helm releases)
make logs       # main Odoo logs
make down       # tear down the stack (keeps data; --purge deletes everything)
```
