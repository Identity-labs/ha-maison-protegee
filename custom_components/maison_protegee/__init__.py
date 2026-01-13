from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import MaisonProtegeeAPI
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    
    api = MaisonProtegeeAPI(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        hass.helpers.aiohttp_client.async_get_clientsession(),
    )
    
    await api.async_authenticate()
    
    hass.data[DOMAIN][entry.entry_id] = api
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

