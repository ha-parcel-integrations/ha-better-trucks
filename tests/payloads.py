"""Sample Better Trucks API payloads shared by the test modules.

Based on the real payload structure documented in carrier-research/better-trucks/api/tracking.md
"""
from __future__ import annotations

ACTIVE_CODE = "BTS_XXXXXXXXXXX"
DELIVERED_CODE = "BTS_YYYYYYYYYYY"


def tracking_event(status: str, object_created: str, location: dict | None = None, event_details: dict | None = None) -> dict:
    """One entry of Better Trucks' tracking_history array."""
    event = {
        "object_created": object_created,
        "status": status,
        "status_details": status,  # Simplified for tests
        "location": location or {"city": None, "state": None, "zip": None, "country": None},
    }
    if event_details:
        event["event_details"] = event_details
    return event


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel based on the annotated capture from tracking.md."""
    return {
        "tracking_number": code,
        "shipment_tracking_number": code,
        "created_date": "2026-06-10T22:14:21",
        "address_from": {
            "city": "<ORIGIN CITY>",
            "state": "<ST>",
            "zip": "<ZIP>",
            "country": "US",
            "latitude": None,
            "longitude": None,
        },
        "address_to": {
            "city": "<DEST CITY>",
            "state": "<ST>",
            "zip": "<ZIP>",
            "country": "US",
            "latitude": "<LAT>",
            "longitude": "<LON>",
        },
        "transaction": "<TRANSACTION UUID>",
        "eta": "2026-06-12T18:51:48",
        "original_eta": "2026-06-12T18:51:48",
        "tracking_status": {
            "object_created": "2026-06-12T18:51:48",
            "status": "DELIVERED",
            "status_details": "Delivered",
            "location": {
                "city": "<DEST CITY>",
                "state": "<ST>",
                "zip": "<ZIP>",
                "country": "US",
                "latitude": None,
                "longitude": None,
            },
            "latitude": "<LAT>",
            "longitude": "<LON>",
            "location_info": None,
            "event_details": {
                "ConsigneeName": None,
                "DropoffLocation": "Front Door",
                "FailureReason": None,
            },
            "images": [
                {
                    "imageUrl": "<GCS URL>",
                    "image_description": None,
                    "safesearch_description": None,
                    "is_proof_of_delivery": False,
                    "is_safe_to_display": True,
                    "is_signature": False,
                }
            ],
        },
        "tracking_history": [
            tracking_event("DELIVERED", "2026-06-12T18:51:48", {"city": "<DEST CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}),
            tracking_event("OUT_FOR_DELIVERY", "2026-06-12T14:25:19", {"city": "<HUB CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}),
            tracking_event("SCANNED_AT_HUB_DET", "2026-06-12T10:09:10", {"city": "<HUB CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}),
            tracking_event("SCANNED_AT_HUB_CHI", "2026-06-12T00:51:59", {"city": "<HUB CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}, {"location_info": "facility_id:0"}),
            tracking_event("INJECTED", "2026-06-12T00:50:59", {"city": "<HUB CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}, {"location_info": "facility_id:0"}),
            tracking_event("TRANSACTION_CREATED", "2026-06-10T22:14:22", None),
            tracking_event("SHIPMENT_CREATED", "2026-06-10T22:14:21", {"city": "<ORIGIN CITY>", "state": "<ST>", "zip": "<ZIP>", "country": "US"}),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An in-transit parcel (out for delivery)."""
    sample = delivered_sample(code)
    sample["tracking_status"]["status"] = "OUT_FOR_DELIVERY"
    sample["tracking_status"]["status_details"] = "Out For Delivery"
    sample["tracking_status"]["object_created"] = "2026-06-12T14:25:19"
    sample["eta"] = "2026-06-12T18:51:48Z"  # Add Z to make it timezone-aware
    sample["original_eta"] = "2026-06-12T18:51:48Z"
    # Remove delivered event, keep transit events (remove first event which is DELIVERED)
    sample["tracking_history"] = sample["tracking_history"][1:4]
    return sample


def not_found_sample(code: str = "BTS_0000000000") -> dict:
    """The not-found skeleton (UNKNOWN status) from tracking.md."""
    return {
        "tracking_number": code,
        "shipment_tracking_number": None,
        "created_date": None,
        "address_from": {
            "city": None,
            "state": None,
            "zip": None,
            "country": None,
            "latitude": None,
            "longitude": None,
        },
        "address_to": {
            "city": None,
            "state": None,
            "zip": None,
            "country": None,
            "latitude": None,
            "longitude": None,
        },
        "transaction": "<FRESH UUID>",
        "eta": None,
        "original_eta": None,
        "tracking_status": {
            "object_created": "2026-08-25T00:00:00+00:00",
            "status": "UNKNOWN",
            "status_details": "Invalid tracking no",
            "location": {
                "city": None,
                "state": None,
                "zip": None,
                "country": None,
                "latitude": None,
                "longitude": None,
            },
            "latitude": 0,
            "longitude": 0,
            "location_info": None,
            "event_details": None,
            "images": None,
        },
        "tracking_history": [],
    }
