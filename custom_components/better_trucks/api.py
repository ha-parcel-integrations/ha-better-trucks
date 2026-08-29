"""Better Trucks public tracking API client.

Better Trucks uses a keyless, code-based model where each tracking number is
registered by the user. The endpoint answers HTTP 200 for all requests, including
unknown numbers — unknown is indicated by status="UNKNOWN" and an empty
tracking_history array, not by HTTP status.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)

# Where users report a broken assumption about this endpoint's contract.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-better-trucks/issues/new"
    "?template=unrecognised_status.yml"
)

# Keys already warned about this session — one-shot, like parcels.py's set.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(message, *args)


class BetterTrucksApiError(Exception):
    """Raised when a Better Trucks API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Better Trucks API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class BetterTrucksApiClient:
    """Client for the public Better Trucks tracking endpoint.

    No authentication: the endpoint is keyed on the tracking code alone.
    Returns the full response envelope directly (no wrapping status/error field).
    Unknown tracking codes return HTTP 200 with status="UNKNOWN" and
    tracking_history=[] — this is a normal pending state, not an error.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the full response dict for a known parcel, or ``None`` when the
        endpoint reports the code as unknown (status="UNKNOWN" or empty
        tracking_history). Any other failure raises :class:`BetterTrucksApiError`;
        network errors propagate as ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise BetterTrucksApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                # The not-found contract (see module docstring) was
                # confirmed as always-200, even for a bogus number. A
                # non-200 for a well-formed tracking request means that
                # assumption broke — worth a one-shot WARNING distinct from
                # the per-poll error the coordinator already logs.
                _warn_once(
                    "not-found-contract-status",
                    "Better Trucks returned HTTP %s for a tracking request "
                    "— this endpoint was assumed to always answer 200, even "
                    "for an unknown number. Open an issue: %s",
                    response.status,
                    NEW_ISSUE_URL,
                )
                raise BetterTrucksApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise BetterTrucksApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise BetterTrucksApiError("unexpected body (not a JSON object)")

        if "tracking_status" not in payload:
            # Every capture so far — resolved or the UNKNOWN sentinel —
            # carried a tracking_status key, present but null at worst. A
            # response missing the key entirely is a different shape change
            # than "present but null", so it gets its own warning.
            _warn_once(
                "not-found-contract-missing-key",
                "A Better Trucks response had no tracking_status key at "
                "all (rather than a null one) — the not-found contract may "
                "have changed. Open an issue: %s",
                NEW_ISSUE_URL,
            )

        # Better Trucks returns HTTP 200 even for unknown numbers.
        # Unknown is indicated by status="UNKNOWN" or empty tracking_history.
        status = (payload.get("tracking_status") or {}).get("status")
        if status == "UNKNOWN" or not payload.get("tracking_history"):
            return None

        return payload
