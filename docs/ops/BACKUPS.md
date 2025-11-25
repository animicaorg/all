# Backups & Restores — DB / Keys / Seeds

This guide describes what to back up, how often, how to encrypt and verify, and how to restore safely. It covers **node data stores** (SQLite/RocksDB), **DA store metadata**, and **sensitive keys** (wallet mnemonics, node identity keys, update-feed keys). All examples assume Linux hosts; adapt paths for your OS.

> TL;DR (policy): follow **3–2–1** — 3 copies, on 2 media, 1 off-site — with **encryption-at-rest**, **immutable retention** for critical snapshots, and **verified restores** on a schedule.

---

## 1) Scope & Definitions

### Data classes

| Class | Examples | Sensitivity | Notes |
|---|---|---|---|
| **Hot DBs** | Node state DB (SQLite/RocksDB), mempool state, P2P peerstore | Medium | Reproducible from network at the cost of time; still back up for fast RTO. |
| **DA Store** | Blob metadata/index, content-addressed files | Low/Medium | Contents may be reconstructable from peers, but local cache improves recovery. |
| **Operational DBs** | studio-services SQLite, AICF queue/state | Medium/High | Required for service continuity; back up frequently. |
| **Keys** | Wallet mnemonics, node identity keys (PQ), update feed (Sparkle) keys | **High** | Treat as secrets; encrypt, split, and test recovery. |
| **Configs** | Chain params, allowlists, CORS, rate limits | Low | Checked into repo; still capture current deployed versions. |

### RPO/RTO targets

- **Mainnet nodes**: RPO ≤ 5 min (hot snapshots), RTO ≤ 30 min (restore + catch-up).
- **Public services (RPC/Studio/DA)**: RPO ≤ 1 min (WAL/incremental), RTO ≤ 15–30 min.
- **Signing/seed keys**: RPO = 0 (no data loss acceptable), RTO ≤ 1 business day with multi-party process.

---

## 2) Inventory & Paths (defaults)

- Node DB (SQLite): `/var/lib/animica/node/animica.db`
- Node DB (RocksDB): `/var/lib/animica/node/rocksdb/`
- P2P peerstore/keys: `/var/lib/animica/p2p/` and `/var/lib/animica/keys/`
- DA store (files+index): `/var/lib/animica/da/`
- Services DB (studio-services): `/var/lib/animica/studio-services/app.db`
- Update feed signing keys (macOS Sparkle, optional): secured off-host (HSM/KMS), **never** on production nodes.

> Adjust to your deployment conventions (systemd units may chroot into `/srv/animica/...`).

---

## 3) Backup Strategy

### Frequencies & retention

| Item | Method | Frequency | Retention | Verify |
|---|---|---|---|---|
| SQLite DBs | Online `.backup` (or fs snapshot) | 5–15 min | 7–30 days | `PRAGMA integrity_check`, test restore |
| RocksDB | FS snapshot (ZFS/LVM/btrfs) or rsync while stopped | 5–15 min | 7–30 days | Open DB, list column families |
| DA store | Incremental file-level (restic/borg) | hourly | 14–30 days | Random sample restore |
| Keys/seeds | Encrypted (sops/age or KMS), plus Shamir (M-of-N) | on change | immutably 1–3 yrs | Dry-run decrypt + checksum |
| Configs | Git repo + nightly artifact | daily | 90 days | Hash compare with live |

### Immutable, encrypted off-site
- Use object storage with **Object Lock (WORM)** or **Bucket Lock** and **server-side encryption**; add **client-side** encryption too (age/GPG).
- Example: restic → S3 with `--repo s3:s3.amazonaws.com/bucket` and `--option s3.object_lock=true`.

---

## 4) Procedures

### 4.1 SQLite — hot backup (no downtime)

> Works for: node SQLite DB, studio-services DB.

```bash
# Create a consistent backup file while the service runs
sqlite3 /var/lib/animica/node/animica.db ".timeout 5000" ".backup /tmp/animica_node_$(date +%F_%H%M).sqlite"

# Optional: vacuum and integrity check (on a copy!)
sqlite3 /tmp/animica_node_*.sqlite "PRAGMA integrity_check;"

Ship off-site (restic example):

export RESTIC_PASSWORD_COMMAND="pass animica/restic"
restic -r s3:s3.amazonaws.com/animica-backups/node backup /tmp/animica_node_*.sqlite
restic -r s3:s3.amazonaws.com/animica-backups/node forget --keep-hourly 48 --keep-daily 14 --prune
shred -u /tmp/animica_node_*.sqlite

4.2 RocksDB — snapshot or cold copy

Option A — Filesystem snapshot (ZFS):

zfs snapshot pool/animica@$(date +%F-%H%M)
zfs send -v pool/animica@<snap> | zstd -T0 | aws s3 cp - s3://animica-backups/rocksdb/<snap>.zst

Option B — Brief stop + rsync:

systemctl stop animica-node
rsync -a --delete /var/lib/animica/node/rocksdb/ /tmp/rocksdb_backup/
systemctl start animica-node
restic -r s3:s3.amazonaws.com/animica-backups/node backup /tmp/rocksdb_backup/
rm -rf /tmp/rocksdb_backup/

If using RocksDB BackupableDB mode in your deployment, prefer its native backup directory—treat it like a snapshot and sync it.

4.3 DA store — incremental files

restic -r s3:s3.amazonaws.com/animica-backups/da backup /var/lib/animica/da
restic -r s3:s3.amazonaws.com/animica-backups/da forget --keep-hourly 48 --keep-daily 14 --prune

4.4 Keys & seeds — secure storage

Wallet mnemonics (PQ-compatible)
	•	Generate offline; write to metal or archival paper.
	•	Encrypt digital copy with age and optional Shamir splitting.

Example (age + 3-of-5 Shamir using ssss):

# Encrypt mnemonic.txt → mnemonic.txt.age
age -R recipients.txt -o mnemonic.txt.age mnemonic.txt

# Split an additional symmetric key (optional layer)
head -c 32 /dev/urandom | tee master.key | ssss-split -t 3 -n 5 > shares.txt

Node identity keys (P2P Dilithium/SPHINCS+)
	•	Location: /var/lib/animica/keys/node_identity/ (0600).
	•	Encrypt with sops (KMS) or age and keep one sealed copy per site.
	•	Do not reuse across environments; rotating this key changes your peer_id and reputation.

Update-feed (Sparkle) keys
	•	Keep in HSM/KMS only; never on app hosts. Store public key in repo; private key split across custodians.

⸻

5) Restore Procedures

Always restore into an isolated staging host first, verify integrity and state, then cut over.

5.1 SQLite restore (node)

systemctl stop animica-node
cp /restore/animica_node_YYYY-mm-dd_HHMM.sqlite /var/lib/animica/node/animica.db
chown animica:animica /var/lib/animica/node/animica.db
sqlite3 /var/lib/animica/node/animica.db "PRAGMA integrity_check;"  # expect 'ok'
systemctl start animica-node

Verify:

curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq .
# Confirm chainId & policy roots match expected; node should re-sync recent blocks.

5.2 RocksDB restore

systemctl stop animica-node
rm -rf /var/lib/animica/node/rocksdb/*
rsync -a /restore/rocksdb_snapshot/ /var/lib/animica/node/rocksdb/
chown -R animica:animica /var/lib/animica/node/rocksdb
systemctl start animica-node

Verify column families open in logs; node resumes from last consistent point.

5.3 DA store restore

restic -r s3:s3.amazonaws.com/animica-backups/da restore latest --target /var/lib/animica
# or rsync a snapshot back to /var/lib/animica/da

Verify: retrieve a recent blob by commitment and run a light DA proof check.

5.4 Keys & seeds restore
	1.	Coordinate custodians to reassemble Shamir shares (if used).
	2.	Decrypt age/sops payload on an offline host; compare checksum with recorded hash.
	3.	For node identity, place key material under /var/lib/animica/keys/ with chmod 600 and chown animica.
	4.	Verify peer identity:

animica-p2p peer --show-id   # or your CLI: prints peer_id = sha3_256(pubkey|alg_id)


	5.	For wallet mnemonics, import on an air-gapped device first; derive addresses and verify against expected watch-only list before moving funds.

Never restore production secrets into test environments, and never copy secrets between regions without approval.

⸻

6) Automation (systemd timers / cron)

Example: SQLite online backup every 10 minutes

/usr/local/bin/animica-sqlite-backup.sh

#!/usr/bin/env bash
set -euo pipefail
stamp=$(date +%F_%H%M)
db="/var/lib/animica/node/animica.db"
out="/var/backups/animica/sqlite/animica_${stamp}.sqlite"
mkdir -p "$(dirname "$out")"
sqlite3 "$db" ".timeout 5000" ".backup '$out'"
sqlite3 "$out" "PRAGMA integrity_check;" | grep -q '^ok$'
restic -r s3:s3.amazonaws.com/animica-backups/node backup "$out"
find /var/backups/animica/sqlite -type f -mtime +2 -delete

Create a systemd timer to run this script, or a cron entry (*/10 * * * *).

⸻

7) Verification & Drills
	•	Weekly: automated test restore of latest SQLite backup to staging; node must sync to head within SLA.
	•	Monthly: full disaster drill — restore RocksDB/DA and roll traffic (blue/green or DNS).
	•	Every key change: dry-run decrypt and checksum compare; record in secrets ledger.

Checksums registry
Maintain a checksums.json (signed) per backup epoch to detect tampering:

{ "animica_node_2025-10-10_0130.sqlite": "sha256:abcd...", "keys.tgz.age": "sha256:..." }


⸻

8) Security & Compliance
	•	Encrypt in transit and at rest (TLS, SSE-KMS + client-side).
	•	Use least privilege service accounts; narrow S3 bucket policies to append-only for backup writers.
	•	Enable Object Lock (compliance mode) for critical snapshots (e.g., monthlies).
	•	Maintain audit logs for access to keys and backups.
	•	Rotate backup credentials on a schedule; verify no orphaned keys exist.

⸻

9) Troubleshooting
	•	database is locked on .backup: increase .timeout, or perform FS snapshot; as a last resort, brief stop.
	•	RocksDB corruption after abrupt power loss: prefer snapshot-based restore; allow DB to auto-repair only with guidance.
	•	Slow restores: parallelize transfer (zstd -T0), warm target disk cache, and consider incremental restore when supported.

⸻

10) Appendices

A) restic repository init (one-time)

export RESTIC_PASSWORD_COMMAND="pass animica/restic"
restic -r s3:s3.amazonaws.com/animica-backups/node init
restic -r s3:s3.amazonaws.com/animica-backups/da init

B) Example sops config for keys

.sops.yaml

creation_rules:
  - path_regex: secrets/.*\\.enc\\.yaml$
    kms: ["arn:aws:kms:us-east-1:123456789012:key/abcd-..."]
    age: ["age1q....recipientkey"]

C) Minimal restore validation script

#!/usr/bin/env bash
set -euo pipefail
sqlite3 "$1" "PRAGMA integrity_check;" | grep -q '^ok$' || { echo "Integrity failed"; exit 1; }
echo "OK: $1"


⸻

Owners: @oncall-ops, @oncall-core
Last updated: 2025-10-10
Related: OBSERVABILITY.md, RUNBOOKS.md
