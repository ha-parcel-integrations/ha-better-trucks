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

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

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

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Dynamic polling* — **no 429 has ever been observed against this endpoint**
(see *Carrier-specific notes*); the suite's backoff machinery is still wired
up unconditionally.

## Running tests

```
python -m pytest tests/ --cov=custom_components.better_trucks
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in this carrier's own directory in the private
`carrier-research/<slug>/api/`, never in this repo.
