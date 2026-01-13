from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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

    sensors = []
    status_data = coordinator.data.get("sensors", {})
    
    for sensor_id, sensor_info in status_data.items():
        sensors.append(
            MaisonProtegeeSensor(
                coordinator,
                entry.entry_id,
                sensor_id,
                sensor_info.get("name", sensor_id),
                sensor_info.get("unit", ""),
            )
        )

    async_add_entities(sensors)


class MaisonProtegeeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: MaisonProtegeeAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=30,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        status = await self.api.async_get_status()
        if status is None:
            return {"sensors": {}}
        return status


class MaisonProtegeeSensor(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        coordinator: MaisonProtegeeCoordinator,
        config_entry_id: str,
        sensor_id: str,
        name: str,
        unit: str,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        self._attr_name = name
        self._attr_unique_id = f"{config_entry_id}_{sensor_id}"
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int | float | None:
        return (
            self.coordinator.data.get("sensors", {})
            .get(self._sensor_id, {})
            .get("value")
        )

