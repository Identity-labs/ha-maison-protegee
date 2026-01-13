from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import Event, HomeAssistant, callback

from .api import MaisonProtegeeAPI
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    
    if f"{DOMAIN}_shutdown_listener" not in hass.data:
        @callback
        async def async_shutdown_listener(_event: Event) -> None:
            """Logout all sessions on shutdown."""
            for entry_id, entry_data in list(hass.data.get(DOMAIN, {}).items()):
                if isinstance(entry_data, dict) and "api" in entry_data:
                    await entry_data["api"].async_logout()
        
        hass.bus.async_listen_once("homeassistant_stop", async_shutdown_listener)
        hass.data[f"{DOMAIN}_shutdown_listener"] = True
    
    session = aiohttp.ClientSession()
    
    api = MaisonProtegeeAPI(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session,
    )
    
    await api.async_authenticate()
    
    hass.data[DOMAIN][entry.entry_id] = {"api": api, "session": session}
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        if "api" in entry_data:
            await entry_data["api"].async_logout()
        if "session" in entry_data:
            await entry_data["session"].close()
    
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

