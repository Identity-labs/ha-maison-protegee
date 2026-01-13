from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

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

    async_add_entities(
        [
            MaisonProtegeeSwitch(coordinator, entry.entry_id, entity_id, entity_data.get("name", entity_id))
            for entity_id, entity_data in coordinator.data.get("entities", {}).items()
        ]
    )


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
        status = await self.api.async_get_status()
        if status is None:
            return {"entities": {}}
        return status


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

