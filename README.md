# Kedge (⚓) — Autonomous GitOps & Resilient State Fabric Engine

[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![Zero External Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#zero-dependencies)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Kedge** is an autonomous, zero-dependency GitOps and state fabric engine for containerized Linux hosts and homelabs. It unifies declarative infrastructure management and outbound stateful disaster recovery into a single closed loop:

- **Inbound GitOps**: Synchronizes container compose stacks via Git submodules and provides pull-on-push webhooks via Komodo Periphery.
- **Outbound State Fabric**: Non-blocking atomic SQLite hot backups (`WAL` mode safe), host OS configuration capture (crontabs, kernel sysctl tunings, shell environments, systemd units, package BOM), AES-256 GPG encryption, and quota-enforced remote publishing (GitHub Releases, S3, or Local).

---

## Architecture & How It Works

```
                        ┌────────────────────────────────────────┐
                        │            GitHub Ecosystem            │
                        │                                        │
                        │   ┌────────────────────────────────┐   │
                        │   │  Orchestrator Parent Monorepo  │   │
                        │   │    (Submodule Commit Hashes)   │   │
                        │   └───┬────────────────────────┬───┘   │
                        │       │ Encrypted              │ Git   │
                        │       │ Release Assets         │ Push  │
                        │       │ (AES-256 GPG)          │       │
                        │       ▼                        ▼       │
                        │   ┌───────────────┐  ┌─────────────┐   │
                        │   │ State Backups │  │ Stack Repos │   │
                        │   │ (1.8 GB Guard)│  │ (Compose)   │   │
                        │   └───────┬───────┘  └──────┬──────┘   │
                        └───────────┼─────────────────┼──────────┘
                                    │                 │
             Outbound State Restore │                 │ Inbound GitOps Sync
             via `kedge restore`    │                 │ via Webhook Trigger
                                    │                 │
                        ┌───────────┼─────────────────┼──────────┐
                        │           ▼                 ▼          │
                        │   ┌───────────────┐  ┌─────────────┐   │
                        │   │  State & DBs  │  │ Komodo Core │   │
                        │   │  Host Configs │  │ /Periphery  │   │
                        │   │  (Crons/Envs) │  │  (Deployer) │   │
                        │   └───────▲───────┘  └──────┬──────┘   │
                        │           │                 │          │
                        │           │ Atomic Snapshot │ Spawns   │
                        │           │ (Magic Byte DB) │          │
                        │   ┌───────┴───────┐  ┌──────▼──────┐   │
                        │   │     kedge     │  │ Live Docker │   │
                        │   │  Engine (CLI) │  │  Containers │   │
                        │   └───────────────┘  └─────────────┘   │
                        │                                        │
                        │           Target Host Machine          │
                        └────────────────────────────────────────┘
```

### Key Technical Pillars

1. **Atomic SQLite Hot Snapshots (Zero Downtime)**:
   Services running SQLite in Write-Ahead Logging (`WAL`) mode cannot be safely backed up via raw `tar` or `rsync` without risk of database corruption. Kedge sniffs the 16-byte SQLite header signature (`SQLite format 3\0`) and uses Python's native SQLite Online Backup API (`src.backup(dest)`) with `PRAGMA integrity_check;` validation.
2. **Host OS Configuration Capture**:
   Preserves critical host settings outside containers: kernel sysctl configurations, cron jobs, environment files (`/etc/environment`, `/etc/profile.d/`), systemd units, udev rules, and installed package manifests. Generates an executable `restore-host.sh` for bare-metal reconstitution.
3. **Multi-Root & Tree Agnostic**:
   Works across non-flat filesystem hierarchies (CasaOS apps, multi-disk mounts, scattered AppData folders). Preserves external mount paths under unique volume mappings and isolates container secrets (`.env`) across all stack working directories.
4. **Intelligent Bulk Media Filtering**:
   Automatically skips bulk storage mounts (e.g. movies, downloads, camera photo libraries) from the state backup queue, ensuring archives focus strictly on state and remain well within size quotas.
5. **Zero External Dependencies**:
   Built entirely on the Python standard library. No `pip install` or external virtualenvs required.

---

## Installation

### 1-Line Installer
```bash
./install.sh
```
Installs the `kedge` binary to `/usr/local/bin/kedge` (or `~/.local/bin/kedge`).

### Direct Python Execution
```bash
python3 -m kedge --help
```

---

## Quickstart

Run the interactive onboarding wizard to configure the orchestrator:

```bash
kedge init
```

The wizard guides you through:
1. **Toolchain Verification**: Audits required tools (`git`, `sqlite3`, `gpg`, `tar`, `docker`, `gh`).
2. **Workspace Directory**: Sets your parent orchestrator directory.
3. **Remote Git Repository**: Sets up the origin repository URL (e.g. `git@github.com:user/orchestrator.git`).
4. **Container Tracking**: Discovers running Compose stacks and tracks them via Git submodules.
5. **AES-256 Key**: Generates a secure cryptographic key with `0600` permissions.
6. **Inbound GitOps**: Optionally deploys a local Komodo Periphery agent for webhooks.
7. **Automated Schedule**: Installs a rate-guarded backup cron job ($\ge 1\text{ hour}$).

---

## CLI Reference

### `kedge discover` (or `kedge scan`)
Audits active container stacks, persistent mounts, SQLite databases, and host OS state.
```bash
kedge scan
kedge scan --json
kedge scan --save   # Persists discovered paths into .kedge.yaml
```

### `kedge backup`
Executes an end-to-end state capture, hot database dumps, secret isolation, AES-256 encryption, and upload:
```bash
kedge backup
kedge backup --dry-run   # Run discovery and staging checks without uploading
```

### `kedge restore`
Restores state from remote or local storage:
```bash
# Preview restored destinations without writing files:
kedge restore --dry-run

# Restore into an isolated sandbox directory (for safe validation):
kedge restore --target-dir /tmp/sandbox_restore

# Restore directly to the host machine:
kedge restore
```

### `kedge config`
Inspects and updates configuration parameters dynamically:
```bash
kedge config show
kedge config get remote
kedge config set schedule "0 3 * * *"
kedge config set retention 21
kedge config reset
```

### `kedge status`
Displays an overview of tracked stacks, storage quota usage, encryption key status, and remote snapshots:
```bash
kedge status
```

### `kedge verify`
Executes read-only `PRAGMA integrity_check;` on all live databases and validates decryption keys:
```bash
kedge verify
```

### `kedge schedule`
Manages automated cron schedules:
```bash
kedge schedule install --cron "0 3 * * *"
kedge schedule status
kedge schedule remove
```

---

## Limitations & Quotas

- **GitHub Release Quota Guard**: Enforces an asset size ceiling of **1,800 MB** (below GitHub's 2,000 MB limit). Backups exceeding this limit abort cleanly before upload.
- **Minimum Schedule Interval**: Enforces a minimum interval of **60 minutes** to prevent API throttling and release exhaustion. Sub-hourly expressions (`* * * * *`, `*/15 * * * *`) are rejected.
- **Database Engine Scope**: Online atomic hot dumps are natively built for SQLite. Relational engines (PostgreSQL, MariaDB) should use standard volume persistence or containerized dump sidecars.

---

## Frequently Asked Questions (FAQ)

#### Does Kedge take containers offline during backup?
No. Kedge leverages the non-blocking SQLite Online Backup API. Databases in active Write-Ahead Logging (`WAL`) mode can receive continuous concurrent writes while Kedge streams a consistent snapshot.

#### How does Kedge handle multi-disk or non-flat directories?
Kedge does not assume a single flat directory. It sniffs container working directories and bind mounts dynamically. Stacks and volumes on separate disks are preserved under distinct `volumes/<path>` hierarchies in the archive and mapped back to their original disk paths on restoration.

#### Can I test a restore without overwriting production files?
Yes. Use `kedge restore --dry-run` to inspect what would be restored, or use `kedge restore --target-dir /tmp/sandbox` to extract and verify all databases and configurations in complete isolation.

#### What storage backends are supported?
- **GitHub Releases** (via GitHub CLI `gh` or direct GitHub REST API using `GITHUB_TOKEN`).
- **Local / Network Storage** (local paths, NFS, SMB/CIFS mounts).
- **S3 Object Storage** (AWS S3, MinIO, Cloudflare R2).

---

## Disaster Recovery Runbook

To recover a completely destroyed host on a fresh Linux server:

```bash
# 1. Clone your orchestrator repository:
git clone --recurse-submodules git@github.com:<user>/<orchestrator>.git /opt/orchestrator
cd /opt/orchestrator

# 2. Restore state, databases, and secrets:
kedge restore

# 3. Apply host system configs (crons, sysctls):
sudo /opt/orchestrator/manifest/host/restore-host.sh

# 4. Bring up container stacks:
find containers -name "compose*.y*ml" | xargs -I {} docker compose -f {} up -d
```

---

## License

This project is licensed under the [MIT License](LICENSE).
