from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import MaisonProtegeeAPI
from .const import CONF_ENABLE_EVENTS, CONF_ENABLE_TEMPERATURES, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: MaisonProtegeeAPI = entry_data["api"]
    enable_temperatures = entry.data.get(CONF_ENABLE_TEMPERATURES, True)
    enable_events = entry.data.get(CONF_ENABLE_EVENTS, True)

    coordinator = MaisonProtegeeCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    sensors_data = coordinator.data.get("sensors", {})
    _LOGGER.debug("Sensor setup: sensors data = %s", sensors_data)
    
    sensors = []
    for sensor_id, sensor_info in sensors_data.items():
        sensors.append(
            MaisonProtegeeSensor(
                coordinator,
                entry.entry_id,
                sensor_id,
                sensor_info.get("name", sensor_id),
                sensor_info.get("unit", ""),
            )
        )

    if enable_temperatures:
        temp_coordinator = MaisonProtegeeTemperatureCoordinator(hass, api)
        await temp_coordinator.async_config_entry_first_refresh()
        
        temp_data = temp_coordinator.data
        _LOGGER.debug("Temperature setup: temperature data = %s", temp_data)
        
        for sensor_id, sensor_info in temp_data.items():
            sensors.append(
                MaisonProtegeeSensor(
                    temp_coordinator,
                    entry.entry_id,
                    sensor_id,
                    sensor_info.get("name", sensor_id),
                    sensor_info.get("unit", "°C"),
                )
            )

    if enable_events:
        events_coordinator = MaisonProtegeeEventsCoordinator(hass, api)
        await events_coordinator.async_config_entry_first_refresh()
        
        events_data = events_coordinator.data
        _LOGGER.debug("Events setup: events data = %s", events_data)
        
        if events_data:
            latest_event = events_data[0] if isinstance(events_data, list) and len(events_data) > 0 else None
            if latest_event:
                sensors.append(
                    MaisonProtegeeEventSensor(
                        events_coordinator,
                        entry.entry_id,
                        latest_event,
                    )
                )

    _LOGGER.info("Setting up %d sensor entities: %s", len(sensors), [s._attr_name for s in sensors])
    async_add_entities(sensors)


class MaisonProtegeeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: MaisonProtegeeAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.api = api
        self._last_successful_update_time: datetime | None = None

    def get_last_successful_update_time(self) -> datetime | None:
        """Get the timestamp of the last successful update."""
        return self._last_successful_update_time

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Updating sensor coordinator data")
        try:
            status = await self.api.async_get_status()
            if status is None:
                _LOGGER.warning("Failed to get status, returning empty sensors")
                return {"sensors": {}}
            _LOGGER.debug("Status retrieved: %s", status)
            self._last_successful_update_time = datetime.now()
            return status
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Timeout while getting status: %s", err)
            return {"sensors": {}}
        except Exception as err:
            _LOGGER.error("Unexpected error updating sensor coordinator: %s", err, exc_info=True)
            return {"sensors": {}}


class MaisonProtegeeTemperatureCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: MaisonProtegeeAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_temperatures",
            update_interval=timedelta(seconds=600),
        )
        self.api = api
        self._last_successful_update_time: datetime | None = None

    def get_last_successful_update_time(self) -> datetime | None:
        """Get the timestamp of the last successful update."""
        return self._last_successful_update_time

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Updating temperature coordinator data")
        try:
            temperatures = await self.api.async_get_temperatures()
            if temperatures is None:
                _LOGGER.warning("Failed to get temperatures, returning empty dict")
                return {}
            _LOGGER.debug("Temperatures retrieved: %s", temperatures)
            self._last_successful_update_time = datetime.now()
            return temperatures
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Timeout while getting temperatures: %s", err)
            return {}
        except Exception as err:
            _LOGGER.error("Unexpected error updating temperature coordinator: %s", err, exc_info=True)
            return {}


class MaisonProtegeeEventsCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: MaisonProtegeeAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_events",
            update_interval=timedelta(seconds=60),
        )
        self.api = api
        self._last_processed_event_date: str | None = None
        self._last_successful_update_time: datetime | None = None

    def get_last_successful_update_time(self) -> datetime | None:
        """Get the timestamp of the last successful update."""
        return self._last_successful_update_time

    async def _async_update_data(self) -> list[dict[str, Any]]:
        _LOGGER.debug("Updating events coordinator data")
        try:
            events = await self.api.async_get_events()
            if events is None:
                _LOGGER.warning("Failed to get events, returning empty list")
                return []
            
            _LOGGER.debug("Events retrieved: %d events", len(events))
            
            if not events:
                return []
            
            if self._last_processed_event_date is None:
                _LOGGER.debug("First fetch, processing all events")
                if events:
                    self._last_processed_event_date = events[0].get("date")
                    self._fire_new_events(events)
                self._last_successful_update_time = datetime.now()
                return events
            
            new_events = []
            for event in events:
                event_date = event.get("date")
                if event_date and event_date > self._last_processed_event_date:
                    new_events.append(event)
                else:
                    break
            
            if new_events:
                _LOGGER.info("Found %d new events", len(new_events))
                self._last_processed_event_date = new_events[0].get("date")
                self._fire_new_events(new_events)
            
            self._last_successful_update_time = datetime.now()
            return events
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Timeout while getting events: %s", err)
            return []
        except Exception as err:
            _LOGGER.error("Unexpected error updating events coordinator: %s", err, exc_info=True)
            return []

    def _fire_new_events(self, new_events: list[dict[str, Any]]) -> None:
        """Fire Home Assistant events for new alarm events."""
        for event in reversed(new_events):
            event_type = event.get("type", "unknown")
            self.hass.bus.async_fire(
                f"{DOMAIN}_event",
                {
                    "event_type": event_type,
                    "date": event.get("date"),
                    "date_text": event.get("date_text"),
                    "message": event.get("message"),
                },
            )
            _LOGGER.debug("Fired event: %s", event.get("message"))


class MaisonProtegeeSensor(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
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
        if isinstance(self.coordinator.data, dict) and "sensors" in self.coordinator.data:
            return (
                self.coordinator.data.get("sensors", {})
                .get(self._sensor_id, {})
                .get("value")
            )
        else:
            return (
                self.coordinator.data.get(self._sensor_id, {})
                .get("value")
            )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        
        if hasattr(self.coordinator, "get_last_successful_update_time"):
            last_update = self.coordinator.get_last_successful_update_time()
            if last_update:
                attrs["last_successful_update"] = last_update.isoformat()
        
        if hasattr(self.coordinator, "api") and hasattr(self.coordinator.api, "get_last_successful_auth_time"):
            last_auth = self.coordinator.api.get_last_successful_auth_time()
            if last_auth:
                attrs["last_successful_auth"] = last_auth.isoformat()
        
        return attrs


class MaisonProtegeeEventSensor(CoordinatorEntity, SensorEntity):
    """Sensor for the latest alarm event."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        config_entry_id: str,
        latest_event: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = "Latest Alarm Event"
        self._attr_unique_id = f"{config_entry_id}_latest_event"
        self._attr_native_unit_of_measurement = None
        self._attr_state_class = None

    @property
    def native_value(self) -> str | None:
        """Return the latest event message."""
        if isinstance(self.coordinator.data, list) and len(self.coordinator.data) > 0:
            latest = self.coordinator.data[0]
            return latest.get("message", "Unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        
        if isinstance(self.coordinator.data, list) and len(self.coordinator.data) > 0:
            latest = self.coordinator.data[0]
            attrs.update({
                "event_type": latest.get("type", "unknown"),
                "date": latest.get("date"),
                "date_text": latest.get("date_text"),
            })
        
        if hasattr(self.coordinator, "get_last_successful_update_time"):
            last_update = self.coordinator.get_last_successful_update_time()
            if last_update:
                attrs["last_successful_update"] = last_update.isoformat()
        
        if hasattr(self.coordinator, "api") and hasattr(self.coordinator.api, "get_last_successful_auth_time"):
            last_auth = self.coordinator.api.get_last_successful_auth_time()
            if last_auth:
                attrs["last_successful_auth"] = last_auth.isoformat()
        
        return attrs

