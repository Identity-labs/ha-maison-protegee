from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import MaisonProtegeeAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: MaisonProtegeeAPI = entry_data["api"]

    coordinator = MaisonProtegeeCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    entities_data = coordinator.data.get("entities", {})
    _LOGGER.debug("Switch setup: entities data = %s", entities_data)
    
    if not entities_data:
        _LOGGER.warning("No entities found in coordinator data, creating default alarm switch")
        entities_data = {"alarm": {"name": "Alarme", "state": False, "status_text": "Unknown"}}

    entities = [
        MaisonProtegeeSwitch(coordinator, entry.entry_id, entity_id, entity_data.get("name", entity_id))
        for entity_id, entity_data in entities_data.items()
    ]
    
    _LOGGER.info("Setting up %d switch entities: %s", len(entities), [e._attr_name for e in entities])
    async_add_entities(entities)


class MaisonProtegeeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: MaisonProtegeeAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Updating switch coordinator data")
        try:
            status = await self.api.async_get_status()
            if status is None:
                _LOGGER.warning("Failed to get status, returning empty entities")
                return {"entities": {}}
            _LOGGER.debug("Status retrieved: %s", status)
            return status
        except (asyncio.TimeoutError, aiohttp.ClientTimeout) as err:
            _LOGGER.warning("Timeout while getting status: %s", err)
            return {"entities": {}}
        except Exception as err:
            _LOGGER.error("Unexpected error updating switch coordinator: %s", err, exc_info=True)
            return {"entities": {}}


class MaisonProtegeeSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: MaisonProtegeeCoordinator,
        config_entry_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = f"{config_entry_id}_{entity_id}"

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.data.get("entities", {})
            .get(self._entity_id, {})
            .get("state", False)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        api: MaisonProtegeeAPI = self.coordinator.api
        if await api.async_set_status("arm"):
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        api: MaisonProtegeeAPI = self.coordinator.api
        if await api.async_set_status("disarm"):
            await self.coordinator.async_request_refresh()

