"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping apart from the coordinator, and it makes the mapping
trivially unit-testable without spinning up HA.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-better-trucks/issues/new"
    "?template=unrecognised_status.yml"
)

# Better Trucks status vocabulary mapping.
# The HUB_PREFIX handles SCANNED_AT_HUB_* codes (the suffix is a facility code,
# not a state). All such codes map to in_transit and emit no warning.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "SHIPMENT_CREATED": ParcelStatus.REGISTERED,
    "TRANSACTION_CREATED": ParcelStatus.REGISTERED,
    "INJECTED": ParcelStatus.IN_TRANSIT,
    "SCANNED_AT_HUB_CHI": ParcelStatus.IN_TRANSIT,
    "SCANNED_AT_HUB_DET": ParcelStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
}
_HUB_PREFIX = "SCANNED_AT_HUB"

# Keys already warned about, so each unconfirmed shape is logged only once
# per HA session instead of on every poll. One shared set: the warnings below
# are rare and distinct enough that a single namespace-by-prefix set is
# simpler than one set per warning kind.
_warned: set[str] = set()

# The seven documented top-level keys (the "Success body" table —
# eta/original_eta are one row but two keys). A response carrying anything
# else means the payload grew a field the one confirmed capture never showed.
_EXPECTED_TOP_LEVEL_KEYS = frozenset(
    {
        "tracking_number",
        "shipment_tracking_number",
        "created_date",
        "address_from",
        "address_to",
        "transaction",
        "eta",
        "original_eta",
        "tracking_status",
        "tracking_history",
    }
)

# The one confirmed sample is BTS_ + 11 alphanumeric characters —
# a one-sample inference, distinct from the looser config-flow usability
# guard in config_flow.py (BTS_ + 8-20 characters), which exists only to
# catch obvious typos and does not claim to know the real shape.
_OBSERVED_NUMBER_RE = re.compile(r"^BTS_[A-Z0-9]{11}$")


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    _warn_once(
        f"status:{code}",
        "Unrecognised Better Trucks status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _warn_failure_reason() -> None:
    """Warn once that event_details.FailureReason was populated.

    Per the build plan this is "the single most valuable unknown in the
    whole doc" — it is what will finally tell us whether a failed delivery
    surfaces as its own ``status`` or only through this field. The value
    itself is withheld: it may be free text describing the failure.
    """
    _warn_once(
        "failure-reason",
        "A Better Trucks event carried a non-null FailureReason — this is "
        "the key to mapping failed/returning deliveries. Open an issue and "
        "describe what your Better Trucks tracking page showed at that "
        "moment (the value itself is withheld here, it may be free text): %s",
        NEW_ISSUE_URL,
    )


def _warn_dropoff_location(value: str) -> None:
    """Warn once for a DropoffLocation other than the only value seen so far."""
    _warn_once(
        f"dropoff:{value}",
        "Better Trucks reported a DropoffLocation other than 'Front Door' — "
        "it may be where at_pickup_point lives. Open an issue and paste "
        "this line: %s\n  DropoffLocation=%s",
        NEW_ISSUE_URL,
        value,
    )


def _warn_timezone_assumption(raw_value: str, parsed_as_utc: datetime) -> None:
    """Warn once, on the first timestamp parsed, so a human can sanity-check UTC."""
    _warn_once(
        "timezone-assumption",
        "Better Trucks timestamps carry no offset; this integration assumes "
        "UTC. First parsed value: %r → %s (UTC now: %s) — if that looks off "
        "by several hours, open an issue: %s",
        raw_value,
        parsed_as_utc.isoformat(),
        datetime.now(timezone.utc).isoformat(),
        NEW_ISSUE_URL,
    )


def _warn_eta_diverges_in_transit() -> None:
    """Warn once for a non-delivered parcel whose eta != original_eta."""
    _warn_once(
        "eta-diverges-in-transit",
        "A non-delivered Better Trucks parcel has eta != original_eta — "
        "this settles whether eta is a live forecast or a fixed estimate. "
        "Open an issue: %s",
        NEW_ISSUE_URL,
    )


def _warn_eta_diverges_on_delivery() -> None:
    """Warn once for a delivered parcel whose eta != the actual delivery time."""
    _warn_once(
        "eta-diverges-on-delivery",
        "A delivered Better Trucks parcel has eta != its delivery timestamp "
        "— the assumption that eta is rewritten to match on delivery may be "
        "wrong. Open an issue: %s",
        NEW_ISSUE_URL,
    )


def _warn_payload_shape(raw: dict) -> None:
    """Warn once when a real response carries top-level keys we do not expect."""
    unexpected = sorted(set(raw) - _EXPECTED_TOP_LEVEL_KEYS)
    if not unexpected:
        return
    _warn_once(
        f"payload-shape:{','.join(unexpected)}",
        "A Better Trucks response has unexpected top-level keys — open an "
        "issue and paste this line: %s\n  unexpected keys=%s",
        NEW_ISSUE_URL,
        unexpected,
    )


def _warn_divergent_tracking_number() -> None:
    """Warn once when shipment_tracking_number differs from tracking_number.

    Values are withheld — both are dereference keys.
    """
    _warn_once(
        "divergent-tracking-number",
        "A Better Trucks response's shipment_tracking_number differs from "
        "its tracking_number — open an issue (values withheld, they are "
        "tracking numbers): %s",
        NEW_ISSUE_URL,
    )


def _warn_number_format(tracking_code: str) -> None:
    """Warn once for a resolved parcel whose number doesn't match the one-sample shape."""
    _warn_once(
        "number-format",
        "A Better Trucks tracking number resolved with an unexpected shape "
        "(assumed BTS_ + 11 alphanumeric characters, inferred from a single "
        "sample) — open an issue and mention the number's length: %s\n"
        "  length=%d",
        NEW_ISSUE_URL,
        len(tracking_code),
    )


def _check_event_details(event_details: dict | None) -> None:
    """Run the one-shot checks for event_details.FailureReason / DropoffLocation."""
    if not isinstance(event_details, dict):
        return
    if event_details.get("FailureReason"):
        _warn_failure_reason()
    dropoff = event_details.get("DropoffLocation")
    if dropoff and dropoff != "Front Door":
        _warn_dropoff_location(dropoff)


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.

    Better Trucks: SCANNED_AT_HUB_* codes (prefix match) map to in_transit
    without warning — the suffix is a facility code, not a distinct state.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    # Handle SCANNED_AT_HUB_* prefix — new facilities are expected
    if code.startswith(_HUB_PREFIX):
        return ParcelStatus.IN_TRANSIT
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    # Handle SCANNED_AT_HUB_* prefix — new facilities are expected
    if code.startswith(_HUB_PREFIX):
        return ParcelStatus.IN_TRANSIT
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
        _warn_timezone_assumption(str(value), parsed)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    Better Trucks: tracking_history is newest-first, so we reverse it.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("object_created"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event.get("status")),
            "raw_status": event.get("status"),  # Keep hub suffix intact
        }
        _check_event_details(event.get("event_details"))
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    Better Trucks specific field mapping according to the canonical contract.
    All nested reads are guarded — Better Trucks returns null for many fields.
    """
    tracking_code = raw.get("tracking_number")
    tracking_status = raw.get("tracking_status") or {}
    status_code = tracking_status.get("status")
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED

    # Pre-1.0 shape checks — only meaningful on a real API response. The
    # coordinator's own not-yet-fetched/not-found placeholder is a bare
    # {"tracking_number": code} dict with no tracking_status key at all, so
    # gating on that key's presence skips it cleanly.
    if "tracking_status" in raw:
        _warn_payload_shape(raw)

        shipment_tracking_number = raw.get("shipment_tracking_number")
        if (
            shipment_tracking_number
            and tracking_code
            and shipment_tracking_number != tracking_code
        ):
            _warn_divergent_tracking_number()

        _check_event_details(tracking_status.get("event_details"))

        # "Accepted" means the carrier actually resolved the number, not the
        # UNKNOWN not-found sentinel — that one is an echo of whatever the
        # user typed, not evidence of the real format.
        if tracking_code and status_code and status_code != "UNKNOWN":
            if not _OBSERVED_NUMBER_RE.match(tracking_code):
                _warn_number_format(tracking_code)

    # Better Trucks returns eta as a point in time, not a window
    # On delivered parcels, eta equals delivery time (per trap 3), so clear it
    eta_raw = raw.get("eta")
    eta = None
    if eta_raw and not delivered:
        # Parse and format to ensure timezone-aware output
        eta_parsed = parse_iso(eta_raw)
        eta = eta_parsed.isoformat() if eta_parsed else None

    # Address fields: only city/state available, no name or street
    address_from = raw.get("address_from") or {}
    address_to = raw.get("address_to") or {}
    sender = None
    if address_from.get("city") and address_from.get("state"):
        sender = f"{address_from['city']}, {address_from['state']}"
    receiver = None
    if address_to.get("city") and address_to.get("state"):
        receiver = f"{address_to['city']}, {address_to['state']}"

    # Better Trucks has no pickup point concept
    pickup = False
    pickup_point = None

    # Better Trucks provides no weight or dimensions
    weight = None
    dimensions = None

    # Parse delivered_at for consistent timezone handling
    delivered_at_raw = tracking_status.get("object_created")
    delivered_at = None
    if delivered and delivered_at_raw:
        delivered_parsed = parse_iso(delivered_at_raw)
        delivered_at = delivered_parsed.isoformat() if delivered_parsed else None

    # ETA behaviour (trap 3, unresolved until Gate B): compare parsed values,
    # never the raw strings — a Z-vs-+00:00 formatting difference alone must
    # not look like a divergence.
    eta_parsed_for_compare = parse_iso(eta_raw)
    if delivered:
        delivered_at_parsed_for_compare = (
            parse_iso(delivered_at_raw) if delivered_at_raw else None
        )
        if (
            eta_parsed_for_compare
            and delivered_at_parsed_for_compare
            and eta_parsed_for_compare != delivered_at_parsed_for_compare
        ):
            _warn_eta_diverges_on_delivery()
    else:
        original_eta_parsed_for_compare = parse_iso(raw.get("original_eta"))
        if (
            eta_parsed_for_compare
            and original_eta_parsed_for_compare
            and eta_parsed_for_compare != original_eta_parsed_for_compare
        ):
            _warn_eta_diverges_in_transit()

    return {
        "carrier": "Better Trucks",
        "barcode": tracking_code,
        "sender": sender,
        "receiver": receiver,
        "status": status,
        "raw_status": status_code,  # Keep hub suffix intact
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": eta,  # Point estimate, not a window
        "planned_to": None,  # No second bound exists
        "pickup": pickup,
        "pickup_point": pickup_point,
        "url": tracking_url(tracking_code),
        "weight": weight,
        "dimensions": dimensions,
        "history": build_history(raw.get("tracking_history")) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
