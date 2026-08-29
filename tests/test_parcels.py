"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_trucks import parcels as parcels_module
from custom_components.better_trucks.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.better_trucks.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import active_sample, delivered_sample, not_found_sample

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("SHIPMENT_CREATED", ParcelStatus.REGISTERED),
        ("TRANSACTION_CREATED", ParcelStatus.REGISTERED),
        ("INJECTED", ParcelStatus.IN_TRANSIT),
        ("OUT_FOR_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        ("DELIVERED", ParcelStatus.DELIVERED),
        ("SCANNED_AT_HUB_DET", ParcelStatus.IN_TRANSIT),  # Prefix match
        ("SCANNED_AT_HUB_CHI", ParcelStatus.IN_TRANSIT),  # Prefix match
        ("SCANNED_AT_HUB_NEW", ParcelStatus.IN_TRANSIT),  # New hub, no warning
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("TELEPORTED") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("SOMETHING_NEW") is None
    assert map_event_status("DELIVERED") == ParcelStatus.DELIVERED
    assert map_event_status("SCANNED_AT_HUB_CHI") == ParcelStatus.IN_TRANSIT
    assert map_event_status("SCANNED_AT_HUB_DET") == ParcelStatus.IN_TRANSIT
    assert map_event_status("SCANNED_AT_HUB_NEW") == ParcelStatus.IN_TRANSIT


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    """Better Trucks tracking_history is newest-first, so we reverse it."""
    history = build_history(delivered_sample()["tracking_history"])
    assert len(history) == 7
    assert history[0]["raw_status"] == "SHIPMENT_CREATED"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    """Create 25 events to test the 20-event cap."""
    from tests.payloads import tracking_event
    events = [
        tracking_event("IN_TRANSIT", f"2026-04-{day:02d}T10:00:00Z")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"status": "IN_TRANSIT"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    from tests.payloads import tracking_event
    history = build_history(
        [
            tracking_event("SHIPMENT_CREATED", "2026-04-24T10:00:00Z"),
            tracking_event("INJECTED", "not-a-date"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["SHIPMENT_CREATED", "INJECTED"]


def test_build_history_uses_status_as_raw_status():
    """Better Trucks uses status code as raw_status (no separate description field)."""
    from tests.payloads import tracking_event
    history = build_history([tracking_event("INJECTED", "2026-04-24T10:00:00Z")])
    assert history[0]["raw_status"] == "INJECTED"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample.

    Copy this test into a real carrier's own test_parcels.py verbatim — it
    stays correct for whatever subset of CAPABILITIES that carrier declares.
    """
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert delivered["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    """Better Trucks: locality string for sender/receiver, no weight/dimensions."""
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Better Trucks"
    assert parcel["barcode"] == "BTS_YYYYYYYYYYY"
    assert parcel["sender"] == "<ORIGIN CITY>, <ST>"
    assert parcel["receiver"] == "<DEST CITY>, <ST>"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "DELIVERED"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-06-12T18:51:48+00:00"
    # A delivered parcel drops its ETA — per trap 3, eta equals delivery time
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == "https://tracking.bettertrucks.com/track/BTS_YYYYYYYYYYY"
    assert parcel["weight"] is None  # Better Trucks doesn't provide weight
    assert parcel["dimensions"] is None  # Better Trucks doesn't provide dimensions
    assert parcel["pickup"] is False  # Better Trucks has no pickup point concept
    assert parcel["pickup_point"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 7  # Better Trucks sample has 7 events
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED
    assert parcel["history"][0]["raw_status"] == "SHIPMENT_CREATED"


def test_normalize_active_parcel_has_point_estimate():
    """Better Trucks provides a point estimate (eta), not a window."""
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] == "2026-06-12T18:51:48+00:00"  # parse_iso adds UTC timezone
    assert parcel["planned_to"] is None  # No second bound in Better Trucks payload


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"tracking_number": "BTS_0000000000"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_handles_null_location():
    """Better Trucks can have null location fields (trap 4)."""
    raw = active_sample()
    raw["tracking_status"]["location"] = None
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is not None  # Should handle null gracefully


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_not_found_skeleton():
    """Test the not-found contract (trap 2a)."""
    parcel = normalize_parcel(not_found_sample("BTS_0000000000"))
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["barcode"] == "BTS_0000000000"


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


# ---------------------------------------------------------------------------
# Pre-1.0 WARNING obligations (BUILD_PLAN.md §5)
# ---------------------------------------------------------------------------


def test_failure_reason_warns_once(caplog):
    parcels_module._warned.discard("failure-reason")
    raw = delivered_sample()
    raw["tracking_status"]["event_details"]["FailureReason"] = "Recipient refused"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("FailureReason") == 1
    # the actual reason text is never logged — it may be free text
    assert "Recipient refused" not in caplog.text
    assert "issues/new" in caplog.text


def test_no_failure_reason_warning_when_null():
    parcels_module._warned.discard("failure-reason")
    normalize_parcel(delivered_sample())
    assert "failure-reason" not in parcels_module._warned


def test_dropoff_location_warns_once_and_logs_the_value(caplog):
    parcels_module._warned.discard("dropoff:Side Gate")
    raw = delivered_sample()
    raw["tracking_status"]["event_details"]["DropoffLocation"] = "Side Gate"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("Side Gate") == 1
    assert "issues/new" in caplog.text


def test_no_dropoff_warning_for_front_door():
    parcels_module._warned.discard("dropoff:Front Door")
    normalize_parcel(delivered_sample())
    assert "dropoff:Front Door" not in parcels_module._warned


def test_timezone_assumption_warns_once_on_first_naive_timestamp(caplog):
    parcels_module._warned.discard("timezone-assumption")
    assert parse_iso("2026-04-29T13:12:42") is not None
    assert parse_iso("2026-04-30T08:00:00") is not None
    assert caplog.text.count("assumes") == 1
    assert "issues/new" in caplog.text


def test_timezone_assumption_not_warned_for_offset_aware_timestamp():
    parcels_module._warned.discard("timezone-assumption")
    parse_iso("2026-04-29T13:12:42+02:00")
    assert "timezone-assumption" not in parcels_module._warned


def test_eta_diverges_in_transit_warns_once(caplog):
    parcels_module._warned.discard("eta-diverges-in-transit")
    raw = active_sample()
    raw["original_eta"] = "2026-06-12T20:00:00Z"  # differs from eta
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("eta != original_eta") == 1
    assert "issues/new" in caplog.text


def test_eta_matches_original_eta_does_not_warn():
    parcels_module._warned.discard("eta-diverges-in-transit")
    normalize_parcel(active_sample())  # eta == original_eta in the fixture
    assert "eta-diverges-in-transit" not in parcels_module._warned


def test_eta_diverges_on_delivery_warns_once(caplog):
    parcels_module._warned.discard("eta-diverges-on-delivery")
    raw = delivered_sample()
    raw["eta"] = "2026-06-12T12:00:00"  # differs from tracking_status.object_created
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("eta != its delivery timestamp") == 1
    assert "issues/new" in caplog.text


def test_eta_matches_delivery_time_does_not_warn():
    parcels_module._warned.discard("eta-diverges-on-delivery")
    normalize_parcel(delivered_sample())  # eta equals delivery time in the fixture
    assert "eta-diverges-on-delivery" not in parcels_module._warned


def test_payload_shape_warns_once_on_unexpected_top_level_key(caplog):
    parcels_module._warned.discard("payload-shape:carrier_notes")
    raw = delivered_sample()
    raw["carrier_notes"] = "unexpected new field"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("carrier_notes") == 1
    assert "issues/new" in caplog.text


def test_payload_shape_not_warned_for_documented_keys():
    before = set(parcels_module._warned)
    normalize_parcel(delivered_sample())
    added = parcels_module._warned - before
    assert not any(k.startswith("payload-shape:") for k in added)


def test_payload_shape_not_checked_on_local_placeholder():
    """The coordinator's synthesised {"tracking_number": code} has no shape to check."""
    before = set(parcels_module._warned)
    normalize_parcel({"tracking_number": "BTS_0000000000"})
    added = parcels_module._warned - before
    assert not any(k.startswith("payload-shape:") for k in added)


def test_divergent_tracking_number_warns_once(caplog):
    parcels_module._warned.discard("divergent-tracking-number")
    raw = delivered_sample()
    raw["shipment_tracking_number"] = "BTS_DIFFERENTNUM"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("shipment_tracking_number differs") == 1
    # neither number's value is logged
    assert "BTS_DIFFERENTNUM" not in caplog.text
    assert "issues/new" in caplog.text


def test_matching_tracking_numbers_do_not_warn():
    parcels_module._warned.discard("divergent-tracking-number")
    normalize_parcel(delivered_sample())
    assert "divergent-tracking-number" not in parcels_module._warned


def test_number_format_warns_once_for_unexpected_shape(caplog):
    parcels_module._warned.discard("number-format")
    raw = delivered_sample(code="BTS_SHORT")
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("unexpected shape") == 1
    assert "issues/new" in caplog.text


def test_number_format_not_warned_for_11_char_code():
    parcels_module._warned.discard("number-format")
    normalize_parcel(delivered_sample())  # BTS_YYYYYYYYYYY -> 11 chars
    assert "number-format" not in parcels_module._warned


def test_number_format_not_checked_on_unknown_sentinel():
    """UNKNOWN is an echo of user input, not evidence of the real format."""
    parcels_module._warned.discard("number-format")
    normalize_parcel(not_found_sample("BTS_X"))
    assert "number-format" not in parcels_module._warned
