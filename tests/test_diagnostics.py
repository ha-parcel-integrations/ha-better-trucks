"""Tests for Better Trucks diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.better_trucks.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "BTS_12345678"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "BTS_12345678",
            "sender": "<ORIGIN CITY>, <ST>",
            "receiver": "<DEST CITY>, <ST>",
            "status": "out_for_delivery",
            "raw": {
                "tracking_number": "BTS_12345678",
                "address_to": {"city": "<DEST CITY>", "state": "<ST>", "zip": "<ZIP>"},
                "tracking_status": {"latitude": "<LAT>", "longitude": "<LON>"},
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["tracking_number"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["address_to"] == "**REDACTED**"
    # tracking_status is redacted at the field level, but dict structure preserved
    assert result["incoming"][0]["raw"]["tracking_status"]["latitude"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["tracking_status"]["longitude"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
