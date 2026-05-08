# JSON Config Engine

## Overview

Snapshot-based UI configuration management system with three levels: **OOB (Out-of-Box)**, **Tenant**, and **User**. No delta/merge logic — every override is a full JSON copy with lineage tracking.

---

## Core Concepts

| Concept | Description |
|---|---|
| **Snapshot model** | Full JSON copies per scope, no partial updates or diffs |
| **Resolution order** | User → Tenant → OOB (first active match wins) |
| **Lineage tracking** | Every override requires `base_config_id`, `base_release_version`; `base_config_hash` auto-populated if missing |
| **Immutable OOB** | OOB configs are write-once; set via management command only, never via API; `is_active` locked in Admin |
| **Drift detection** | SHA-256 hash comparison between override's `base_config_hash` and current active OOB payload |
| **Upgrade detection** | `base_config_id` comparison against the current active OOB id |
| **Cache layer** | Two-key pointer scheme (`config:ptr:…` → `config:…:release`), 300s TTL, auto-invalidated on writes |

---

## Data Model

Single table: **`config_instances`**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `config_key` | VARCHAR(255) | Logical config identifier e.g. `invoice.form` |
| `scope_type` | VARCHAR(10) | `oob` / `tenant` / `user` |
| `scope_id` | VARCHAR(255) | `NULL` for OOB; tenant or user ID otherwise |
| `release_version` | VARCHAR(50) | Semver string e.g. `v2.0.0` |
| `config_json` | JSONB | Full config payload (objects only, never arrays) |
| `is_active` | BOOLEAN | Only one active config per `(config_key, scope_type, scope_id)` |
| `base_config_id` | UUID | OOB instance this override was derived from |
| `base_release_version` | VARCHAR(50) | OOB release version at time of override creation |
| `base_config_hash` | VARCHAR(64) | SHA-256 of OOB `config_json` at derivation time |
| `parent_config_instance_id` | UUID | Optional pointer to a parent override |
| `created_at` | TIMESTAMPTZ | Auto-set on insert |
| `updated_at` | TIMESTAMPTZ | Auto-updated on save |

**Indexes:** `idx_config_lookup` `(config_key, scope_type, scope_id, is_active)`, `idx_release`, `idx_base_config`

---

## Setup Instructions

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd json_config_engine

# 2. Create and activate virtual environment
python -m venv venv
# Windows
.\venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env   # edit as needed
# Required keys: SECRET_KEY, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT

# 5. Apply migrations
python manage.py migrate

# 6. Create admin superuser
python manage.py createsuperuser

# 7. Start the development server
python manage.py runserver
```

---

## Management Commands

### `load_oob_config`

Inserts a new immutable OOB config for a given release. Use this in your release pipeline.
Any previously active OOB config for the same `config_key` is automatically deactivated, and the cache is invalidated.

```bash
python manage.py load_oob_config \
  --config-key invoice.form \
  --release-version v2.0.0 \
  --config-file ./configs/invoice_form_v2.json
```

| Argument | Required | Description |
|---|---|---|
| `--config-key` | ✅ | Logical config key e.g. `invoice.form` |
| `--release-version` | ✅ | Semver string e.g. `v2.0.0` |
| `--config-file` | ✅ | Path to a JSON file containing the full config payload |

> **Note:** If an OOB config for this `(config_key, release_version)` pair already exists the command prints a warning and exits without making changes. OOB configs are immutable.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/config/` | Get effective config (User → Tenant → OOB resolution) |
| `POST` | `/api/v1/config/override/` | Create or replace a tenant/user override |
| `POST` | `/api/v1/config/reset/` | Reset override back to OOB |
| `GET` | `/api/v1/config/lineage/` | Full history for a config key (all scopes, all states) |
| `GET` | `/api/v1/config/diff/` | Diff a config against current active OOB |
| `GET` | `/api/v1/config/outdated/` | List tenant configs based on a superseded OOB release |

Interactive API docs available at:

| Interface | URL |
|---|---|
| **Swagger UI** | http://127.0.0.1:8000/api/docs/ |
| **ReDoc** | http://127.0.0.1:8000/api/redoc/ |
| **Raw OpenAPI schema** | http://127.0.0.1:8000/api/schema/ |

### Example: Get effective config

```http
GET /api/v1/config/?key=invoice.form&tenant_id=tenant_123
```

```json
{
  "config": { "fields": { "name": { "visible": false }, "email": { "visible": true } } },
  "source": "tenant",
  "release": "v1.0.0"
}
```

### Example: Create tenant override

```http
POST /api/v1/config/override/
Content-Type: application/json

{
  "config_key": "invoice.form",
  "scope_type": "tenant",
  "scope_id": "tenant_123",
  "config_json": { "fields": { "name": { "visible": false }, "email": { "visible": true } } },
  "release_version": "v1.0.0",
  "base_config_id": "4f6cd786-5eb1-458d-8979-f48a0a6de5bd",
  "base_release_version": "v1.0.0"
}
```

> **Tip:** You do not need to provide `base_config_hash`; the engine will automatically fetch the OOB record and compute the hash before saving.

---

## Admin Panel

Access at **http://127.0.0.1:8000/admin/**

| Feature | Description |
|---|---|
| **Config Explorer** | Filter by `config_key`, `scope_type`, `release_version`, `is_active`, `base_config_id` |
| **JSON Editor** | Monospace textarea with client-side `JSON.parse()` validation on submit |
| **Diff Viewer** | Per-record side-by-side view vs. current active OOB; status banner (🔴 drifted / 🟡 outdated / 🟢 in sync) |
| **Upgrade Alerts** | Banner on changelist showing count of outdated tenant configs |
| **Bulk Actions** | Mark selected as inactive (skips OOB records) · Reset selected to OOB |
| **Lineage Links** | Clickable column to filter by `base_config_id` |
| **OOB Protection** | Delete button hidden and `is_active` field locked for all `scope_type='oob'` records |

---

## Running Tests

```bash
# Run all tests with verbose output
.\venv\Scripts\python.exe -m pytest tests/test_config_engine.py -v --no-cov

# Run with coverage report
.\venv\Scripts\python.exe -m pytest tests/test_config_engine.py --cov=apps --cov-report=term-missing
```

The test suite (**58 tests**) covers:

- **ConfigHasher** — determinism, key-order independence, SHA-256 hex output
- **ConfigResolutionService** — full resolution hierarchy, lineage storage, deactivation, drift & outdated detection
- **Cache behaviour** — pointer key creation, invalidation on override/reset/OOB load
- **API views** — all 6 endpoints, including 4xx error paths
- **Constraints** — OOB immutability, single-active-per-scope, method-not-allowed
- **Swagger / OpenAPI** — schema generation, Swagger UI, ReDoc, all endpoints present in schema

---

## JSON Design Guidelines

- Use **objects, not arrays**, for all config values — keys must remain stable across releases
- Only **values** evolve between releases, never the key structure
- Keep the config payload **flat and namespaced** within each release (e.g. `{ "fields": { "name": { ... } } }`)
- Never store computed or runtime values in `config_json`; it is a pure configuration snapshot
