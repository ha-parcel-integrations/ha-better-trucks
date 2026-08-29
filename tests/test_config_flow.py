"""Tests for the Better Trucks config and options flow."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_trucks.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.better_trucks.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("BTS-123 45678") == "BTS_12345678"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_bounds():
    """Better Trucks format: BTS_ prefix + 8-20 alphanumeric characters."""
    assert valid_tracking_code("BTS_12345678")
    assert valid_tracking_code("BTS_ABCDEFGHIJKLMNOPQRST")  # 20 chars after prefix
    assert not valid_tracking_code("BTS_1234567")  # too short (7 chars after prefix)
    assert not valid_tracking_code("BTS_" + "A" * 21)  # too long (21 chars after prefix)
    assert not valid_tracking_code("EXAMPLE123456")  # missing BTS_ prefix
    assert not valid_tracking_code("BTS_123-456")  # dashes not allowed (will be stripped)


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the entry is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Better Trucks"
    assert result["options"][CONF_PARCELS] == []


async def test_second_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    # single_config_entry in the manifest aborts before the flow runs.
    assert result["reason"] == "single_instance_allowed"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _parcel_input(*tracking_codes: str) -> dict:
    """Build the parcel-management form submission."""
    return {"tracking_codes": list(tracking_codes)}


def _settings_input(*, history=False, filter_type="days", amount=7) -> dict:
    """Build the integration-settings form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    """Start an options flow and select one of its menu entries."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS_12345678")
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "BTS_12345678"}
    ]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces/dashes are sanitised like the consumer site."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS-123 45678")
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "BTS_12345678"}
    ]


async def test_options_add_invalid_tracking_code(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS_1234567")  # Too short
    )
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "BTS_111111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS_111111111", "bts_111111111")
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "BTS_111111111"}]


async def test_options_remove_parcel(hass):
    entry = _hub([
        {CONF_TRACKING_CODE: "BTS_111111111"},
        {CONF_TRACKING_CODE: "BTS_222222222"},
    ])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS_222222222")
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"BTS_222222222"}


async def test_options_can_clear_the_tracked_code_list(hass):
    """An explicitly empty code list removes the final tracked parcel."""
    entry = _hub([{CONF_TRACKING_CODE: "BTS_111111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input()
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_replaces_the_tracked_code_list(hass):
    """The list selector applies additions and removals in one submission."""
    entry = _hub([{CONF_TRACKING_CODE: "BTS_111111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _parcel_input("BTS_222222222", "BTS_333333333")
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "BTS_222222222"},
        {CONF_TRACKING_CODE: "BTS_333333333"},
    ]


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True, filter_type="parcels", amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
