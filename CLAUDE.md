# Working in this repository

Home Assistant custom integration for **Better Trucks** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. See ha-dpd's or ha-gls's `const.py` for a live example |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

- **No `awaiting_pickup` sensor, deliberately.** Better Trucks has no pickup
  point concept — `pickup`/`pickup_point` are hardcoded `False`/`None` in
  `parcels.py`, not derived from a status that could ever fire. Structural,
  not a gap — see `.github/CONVENTIONS.md`'s pickup-point convention.
- **Keyless, code-based tracking:** Tracking endpoint `https://tracking.bettertrucks.com/api/tracking/{tracking_code}?isFromExternalTracking=true` needs no credentials or cookies.
- **Not-found is HTTP 200 (UNKNOWN sentinel):** Unknown tracking codes return `200` with `status="UNKNOWN"` and `tracking_history=[]`. This is a pending placeholder state, not an error. Never branch on HTTP status or raise `UpdateFailed` for unrecognised codes.
- **Status vocabulary & prefix rule:**
  - `SHIPMENT_CREATED` / `TRANSACTION_CREATED` → `registered`
  - `INJECTED` → `in_transit`
  - `SCANNED_AT_HUB_*` (prefix match) → `in_transit` (no unmapped status warning, as suffixes are facility codes)
  - `OUT_FOR_DELIVERY` → `out_for_delivery`
  - `DELIVERED` → `delivered`
- **ETA & delivery time:** `eta` is a point estimate (parsed as UTC). On delivered parcels, `eta` equals the actual delivery timestamp, so `planned_from` is explicitly set to `None` on delivered parcels to avoid redundant/conflicting time reporting.
- **Fields omitted by carrier:** No `weight`, `dimensions`, or `pickup_point` exist in the payload (all `None`).
- **Privacy & Diagnostics:** `address_to`, coordinates, and the `images[]` block (door photos) are strictly redacted in `TO_REDACT` and never exposed as HA entities.
- **Pre-1.0 WARNING obligations** (`parcels.py`'s `_warn_once`/`_warned`, mirroring `_warn_unmapped_status`): unmapped status, a 429 (first ever — the endpoint was believed unthrottled), the not-found contract breaking (`api.py`: a non-200 for a well-formed number, or a `200` missing the `tracking_status` key entirely — distinct from key-present-but-null), a non-null `event_details.FailureReason` (value withheld, may be free text), an `event_details.DropoffLocation` other than `"Front Door"` (value logged, it's enum-like), the first timestamp parsed (naive values are assumed UTC — logged next to UTC-now), `eta` diverging from `original_eta` pre-delivery or from the delivery timestamp post-delivery, an unexpected top-level payload key, a resolved tracking number not matching the one-sample `BTS_` + 11-char shape, and `shipment_tracking_number` diverging from `tracking_number` (values withheld). All fire once per HA session and only on a real API response — never on the coordinator's own not-yet-fetched placeholder.
- **API mechanics:** Full documentation lives in `carrier-research/better-trucks/api/`.

## Options and reloads

The options flow starts with exactly `Parcels` and `Settings`. `Parcels` is one
editable multi-code list; `Settings` is a flat form. Changes apply without a
restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  calls `async_request_refresh()`, so added/removed parcel sensors appear
  immediately (this is also the resume path after polling has fully
  suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py` recomputes `update_interval` at the end of
every refresh:

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot.
- **Full stop:** `update_interval = None` when nothing is tracked or every
  tracked parcel is delivered. Resumes the moment a parcel is added back, via
  the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — Better Trucks's own `Retry-After` header if present,
  otherwise an exponential backoff tracked per-coordinator (`api.py`'s
  `BetterTrucksApiError.status_code` / `.retry_after` carry this from the
  HTTP layer). No 429 has actually been observed against this endpoint yet
  (see *Carrier-specific notes*), but the backoff machinery is unconditional
  across the suite.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.better_trucks
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in this carrier's own directory in the private
`carrier-research/<slug>/api/`, never in this repo.
