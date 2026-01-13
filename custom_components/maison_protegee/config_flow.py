from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import MaisonProtegeeAPI
from .const import CONF_ENABLE_EVENTS, CONF_ENABLE_TEMPERATURES, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_ENABLE_TEMPERATURES, default=True): bool,
        vol.Optional(CONF_ENABLE_EVENTS, default=True): bool,
    }
)


class MaisonProtegeeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            session = aiohttp.ClientSession()
            try:
                api = MaisonProtegeeAPI(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    session,
                )

                auth_result = await api.async_authenticate()
                if auth_result:
                    await session.close()
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME],
                        data=user_input,
                    )
                errors["base"] = "invalid_auth"
            except aiohttp.ClientError as err:
                _LOGGER.exception("Connection error during authentication: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected exception during authentication: %s", err)
                errors["base"] = "unknown"
            finally:
                await session.close()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

