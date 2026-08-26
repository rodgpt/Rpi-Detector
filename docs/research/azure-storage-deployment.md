# Azure Blob Storage — deployment

Standing up the storage account the v2 fleet writes to (D-016), with the credential model D-017 requires. Stage 3 of the pipeline in [RESEARCH.md](./RESEARCH.md); the analysis stage lives in `../../DECISIONS.md` as **D-017**, which records the mechanism, the rejected alternatives and why.

**Verified against Microsoft's documentation on 2026-08-25.** Azure changes SKUs, defaults and portal wording. Re-check before trusting any specific claim here — the pipeline's own rule.

**Scope.** One resource group, one storage account, one private container. That is the entire Azure surface Phase 1 needs. No compute, no Functions, no IoT Hub. The backend exists (Dashboard D-019) and reads this same container; only the *client's* subscription remains blocked.

---

## What this buys, and what it does not

The device already speaks Azure: `storage.py:35-43` builds a `BlobClient.from_connection_string`, and `azure-storage-blob>=12.19` is in `raspberry-pi/requirements.txt`. Nothing needs writing to make uploads work — only configuring.

What this does **not** yet deliver is the D-017 credential model. Steps 1–7 below leave you with an account key, which is the interim state described in "Credentials" at the end. That is acceptable on a bench unit you can physically reach. It is not acceptable on anything deployed.

---

## Prerequisites

An Azure account. Per D-015, development runs against **our own subscription** — client Azure access is a separate, still-blocked dependency (`docs/TODO.md`, blocking section).

The free account has three layers, routinely conflated:

| Layer | What |
|---|---|
| Credit | USD 200, 30 days, spends on anything |
| 12-month free services | Includes ~5 GB LRS hot block blob, ~20,000 read and ~10,000 write operations per month |
| Always-free services | A separate list with monthly caps |

Microsoft's [free services page](https://azure.microsoft.com/en-us/pricing/free-services) is the authority and is rendered client-side, so it cannot be scraped — read it in a browser. Third-party summaries disagree on whether the blob allowance is 12-month or always-free; assume 12-month.

**Keep the spending limit on.** It disables the subscription rather than billing you. Note the failure mode that creates: an over-quota subscription makes uploads fail, and per the project's own rules that must be loud, not silent. See "Quota exhaustion" under Troubleshooting.

**Watch operations, not gigabytes.** Continuous capture writing one event blob plus one WAV per detection approaches the ~10,000 write ceiling faster than the 5 GB capacity ceiling. The bench soak in `../../raspberry-pi/docs/BENCH.md` generates ~17,000 small JSONs per day by design — run that against `OCEANKIND_OUTPUT_DIR`, not against Azure, or it will exhaust a month of free writes in a day.

---

## 1. CLI

```bash
brew install azure-cli
az login
```

## 2. Resource group and storage account

```bash
az group create -n rg-oceankind-dev -l chilecentral

az storage account create \
  -n stoceankinddev01 -g rg-oceankind-dev -l chilecentral \
  --sku Standard_LRS --kind StorageV2 --access-tier Hot \
  --https-only true --min-tls-version TLS1_2 \
  --allow-blob-public-access false
```

Flag by flag:

| Flag | Why |
|---|---|
| `Standard_LRS` | The redundancy the free allowance covers. ZRS/GRS cost more and buy nothing on a dev account |
| `StorageV2` | General-purpose v2. Required for blob index tags, which D-004 depends on |
| `Hot` | Matches the free allowance and the access pattern — the dashboard reads recent events constantly |
| `--allow-blob-public-access false` | Account-level kill switch. Even a misconfigured container cannot go public. Closes the read half of F-07 by construction |
| `--min-tls-version TLS1_2` | Default on new accounts, set explicitly so it survives a portal edit |

**Region.** [Chile Central went GA in 2025](https://www.datacenterdynamics.com/en/news/microsoft-launches-chile-cloud-region/) — three availability zones, in-country residency, closest to both production sites. If a trial subscription refuses it (new regions are sometimes gated for trials), fall back to `brazilsouth`.

**Account name.** Globally unique, 3–24 characters, lowercase alphanumeric only. No hyphens.

## 3. The container

```bash
KEY=$(az storage account keys list -g rg-oceankind-dev -n stoceankinddev01 --query "[0].value" -o tsv)

az storage container create -n alerts \
  --account-name stoceankinddev01 --account-key "$KEY" --public-access off
```

`alerts` matches the default in `config.py:72`. D-016 requires a fresh account and container for v2; a brand-new account satisfies that by definition.

**The gotcha.** `--auth-mode login` fails here even when you are subscription Owner. Owner is a *control-plane* role; blobs are *data-plane*. Data access needs `Storage Blob Data Contributor` assigned separately. Using the account key sidesteps it for bootstrap.

## 4. Versioning and soft delete

```bash
az storage account blob-service-properties update \
  -g rg-oceankind-dev --account-name stoceankinddev01 \
  --enable-versioning true \
  --enable-delete-retention true --delete-retention-days 7
```

D-017 requires this independent of the credential model. A write-capable credential can still *overwrite* its own site's data even with delete denied; versioning makes that recoverable and the scientific record append-only in practice.

Soft delete costs capacity: deleted blobs still count against the 5 GB free allowance for the retention window.

## 5. Point a bench unit at it

Interim, account-key based. See "Credentials" below for why this is bench-only.

```bash
az storage account show-connection-string \
  -g rg-oceankind-dev -n stoceankinddev01 --query connectionString -o tsv
```

Into `/etc/oceankind.env` on the bench Pi — never a literal default, never committed:

```
OCEANKIND_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
OCEANKIND_STORAGE_CONTAINER=alerts
OCEANKIND_SITE=banco
OCEANKIND_SENSOR_LAT=-33.0
OCEANKIND_SENSOR_LON=-71.0
```

`SITE`, `SENSOR_LAT` and `SENSOR_LON` become mandatory the moment storage is enabled — `config.py:385-393` refuses to start without them, by design (R-8.1).

This is option (b) in `../../raspberry-pi/docs/BENCH.md` §4. Run it as a **short** second soak after the local-output run, not as the 24-hour one.

## 6. Verify the round trip

`tools/validate_contract.py` takes a directory, so pull the container down and validate what is actually stored — a stronger check than validating `OUTPUT_DIR` locally, because it exercises real serialisation, real paths and real overwrite semantics.

```bash
rm -rf ./out && mkdir -p ./out
az storage blob download-batch -s alerts -d ./out \
  --account-name stoceankinddev01 --account-key "$KEY"
python3 tools/validate_contract.py ./out
```

Exit 0 means the device produces blobs the dashboard can consume (R-5.6).

---

## Credentials

**Target state is D-017**, not what steps 1–6 leave you with. Read that decision before provisioning anything that leaves the bench.

Summary: one Entra service principal per device, RBAC scoped to the container, narrowed by an ABAC path condition on `sites/{site}/*`. Never delete, never list, never read another site. Blob path conditions [do not require hierarchical namespace](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-auth-abac-examples) and work on a plain StorageV2 account, so the v2 tree survives unchanged. No built-in role is write-only — `Storage Blob Data Contributor` includes read and delete — so this needs a custom role with only `blobs/write` and `blobs/add/action`.

"Write-only" is not literal. The device reads three things, and denying them all would kill remote tuning silently:

| Blob | Read by | Disposition |
|---|---|---|
| `sites/{site}/remote_config.json` | `main.py:204` | Read required — D-015 makes remote tuning non-negotiable |
| `_sites.json` | `storage.py:225`, read-modify-write | Read + write at container root, until the backend owns the registry |
| `sites/{site}/acoustic_indicators.json`, `ocean_conditions.json` | `storage.py:255`, existence check | Droppable; `ensure_aux_blobs` must degrade, not fail |

**Interim rule until D-017 is implemented:** the account key lives on developer machines and on bench units you can physically reach. It never goes onto anything deployed. Implementation is tracked in `raspberry-pi/docs/PROGRESS.md` Phase 5.

**Dashboard read path is settled on the other side.** `Dashboard-Detector` D-019 (2026-08-21) builds a FastAPI backend, and its `docs/SERVER-INFRASTRUCTURE.md` states the blob credential is held there *"and nowhere else"*: the browser never reaches storage, and clips are proxied through `/api/sites/{site}/clips/...`. So `--allow-blob-public-access false` costs the dashboard nothing, and **no CORS rule is needed** — a browser-held SAS would have needed both. Still to confirm with that side: that the backend's credential is scoped (read across sites, plus write to `sites/{id}/remote_config.json` only, per D-020) rather than the account key, and that it targets this new account. Tracked in `docs/TODO.md`.

---

## Troubleshooting

**`AuthorizationPermissionMismatch` on a container or blob command.** Data-plane operation with a control-plane role. Use `--account-key`, or assign `Storage Blob Data Contributor` to yourself.

**Storage account name rejected.** Globally unique across all of Azure, 3–24 lowercase alphanumerics, no hyphens.

**Region unavailable.** New regions are sometimes gated on trial subscriptions. Fall back to `brazilsouth`.

**Quota exhaustion.** With the spending limit on, exceeding the free allowance disables the subscription and uploads start failing. Today that path is a `log.warning` in `storage.py:70-72` plus the event spool. Confirm it escalates to `health.degraded_reason` and `status.json` after repeated failures, or a billing event becomes a silent-deaf outage.

**`Infinity` in JSON.** Not an Azure problem, but it is the scar this contract carries — `sanitize_for_json` in `storage.py:46-55` handles it. Do not bypass it.

---

## Not done here

- D-017 implementation — per-device service principals, custom role, ABAC conditions
- Dashboard read credential and CORS
- Client's own Azure subscription, still blocked (ours suffices for everything above)
- Client subscription access, separate from ours (D-015)

---

**Last verified:** 2026-08-25 against Microsoft documentation.
