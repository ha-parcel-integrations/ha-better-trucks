"""Tests for the Better Trucks API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.better_trucks import api as api_module
from custom_components.better_trucks.api import (
    BetterTrucksApiClient,
    BetterTrucksApiError,
)
from tests.payloads import delivered_sample, not_found_sample

CODE = "BTS_XXXXXXXXXXX"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_parcel_on_success():
    """Better Trucks returns the response directly, no envelope."""
    session = _session_returning(200, delivered_sample(CODE))
    client = BetterTrucksApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["tracking_number"] == CODE
    # the tracking code ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_get_parcel_returns_none_on_unknown_status():
    """UNKNOWN status is the not-found sentinel, returns None (not an error)."""
    session = _session_returning(200, not_found_sample("BTS_0000000000"))
    client = BetterTrucksApiClient(session)
    assert await client.async_get_parcel("BTS_0000000000") is None


async def test_get_parcel_returns_none_on_empty_history():
    """Empty tracking_history also indicates unknown/not-yet-scanned."""
    body = delivered_sample(CODE)
    body["tracking_history"] = []
    body["tracking_status"]["status"] = "SOME_STATUS"
    session = _session_returning(200, body)
    client = BetterTrucksApiClient(session)
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_on_error_status():
    client = BetterTrucksApiClient(_session_returning(500, {}))
    with pytest.raises(BetterTrucksApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_with_status_code():
    """A non-2xx, non-429 error carries its status code for callers to inspect."""
    client = BetterTrucksApiClient(_session_returning(503, {}))
    with pytest.raises(BetterTrucksApiError) as excinfo:
        await client.async_get_parcel(CODE)
    assert excinfo.value.status_code == 503
    assert excinfo.value.retry_after is None


async def test_get_parcel_raises_on_429_with_retry_after_header():
    session = _session_returning(429, {})
    session.get.return_value.__aenter__.return_value.headers = {"Retry-After": "30"}
    client = BetterTrucksApiClient(session)
    with pytest.raises(BetterTrucksApiError) as excinfo:
        await client.async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 30


async def test_get_parcel_raises_on_429_with_non_numeric_retry_after():
    """An HTTP-date Retry-After falls back to None; the caller's own backoff takes over."""
    session = _session_returning(429, {})
    session.get.return_value.__aenter__.return_value.headers = {
        "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"
    }
    client = BetterTrucksApiClient(session)
    with pytest.raises(BetterTrucksApiError) as excinfo:
        await client.async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None


async def test_get_parcel_raises_on_429_without_retry_after_header():
    session = _session_returning(429, {})
    session.get.return_value.__aenter__.return_value.headers = {}
    client = BetterTrucksApiClient(session)
    with pytest.raises(BetterTrucksApiError) as excinfo:
        await client.async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None


async def test_get_parcel_raises_on_unparseable_body():
    client = BetterTrucksApiClient(_session_returning(200, "not json"))
    with pytest.raises(BetterTrucksApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = BetterTrucksApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(BetterTrucksApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = BetterTrucksApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)


# ---------------------------------------------------------------------------
# Not-found contract change — pre-1.0 WARNING obligation
# ---------------------------------------------------------------------------


async def test_non_200_for_well_formed_number_warns_once(caplog):
    api_module._warned.discard("not-found-contract-status")
    client = BetterTrucksApiClient(_session_returning(500, {}))
    with pytest.raises(BetterTrucksApiError):
        await client.async_get_parcel(CODE)
    with pytest.raises(BetterTrucksApiError):
        await client.async_get_parcel(CODE)
    assert caplog.text.count("assumed to always answer 200") == 1
    assert "issues/new" in caplog.text


async def test_missing_tracking_status_key_warns_once(caplog):
    """Key entirely absent is a different shape change than key-present-but-null."""
    api_module._warned.discard("not-found-contract-missing-key")
    client = BetterTrucksApiClient(_session_returning(200, {"unexpected": "shape"}))
    assert await client.async_get_parcel(CODE) is None  # no tracking_history either
    assert await client.async_get_parcel(CODE) is None
    assert caplog.text.count("no tracking_status key at all") == 1
    assert "issues/new" in caplog.text


async def test_present_but_null_tracking_status_does_not_warn():
    """The not-found sentinel carries tracking_status; only its absence is news."""
    api_module._warned.discard("not-found-contract-missing-key")
    client = BetterTrucksApiClient(_session_returning(200, not_found_sample(CODE)))
    await client.async_get_parcel(CODE)
    assert "not-found-contract-missing-key" not in api_module._warned


async def test_normal_200_response_does_not_warn_about_the_contract():
    before = set(api_module._warned)
    client = BetterTrucksApiClient(_session_returning(200, delivered_sample(CODE)))
    await client.async_get_parcel(CODE)
    added = api_module._warned - before
    assert "not-found-contract-status" not in added
    assert "not-found-contract-missing-key" not in added
