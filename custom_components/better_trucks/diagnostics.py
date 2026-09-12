"""Diagnostics support for the Better Trucks parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BetterTrucksConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Better Trucks specific redaction list:
# - address_to (city, state, zip) and its latitude/longitude
# - tracking_status.latitude/longitude and the same on any event
# - every location object's city/zip
# - event_details.ConsigneeName
# - the entire images[] array (door photographs)
# - tracking_number/shipment_tracking_number (dereference keys)
# - transaction (internal correlatable id)
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # Better Trucks payload fields
    "tracking_number",
    "shipment_tracking_number",
    "address_to",
    "address_from",
    "city",
    "state",
    "zip",
    "country",
    "latitude",
    "longitude",
    "location",
    "event_details",
    "ConsigneeName",
    "DropoffLocation",
    "FailureReason",
    "images",
    "imageUrl",
    "image_description",
    "safesearch_description",
    "transaction",
    "location_info",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BetterTrucksConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Better Trucks config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
