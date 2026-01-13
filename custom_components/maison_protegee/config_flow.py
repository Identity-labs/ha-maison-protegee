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

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_ENABLE_TEMPERATURES, default=True): bool,
        vol.Optional(CONF_ENABLE_EVENTS, default=True): bool,
    }
)


class MaisonProtegeeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MaisonProtegeeOptionsFlowHandler:
        """Get the options flow for this handler."""
        return MaisonProtegeeOptionsFlowHandler()

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

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthorization flow."""
        return await self.async_step_user(user_input)


class MaisonProtegeeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Maison Protegee."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        config_entry = self.config_entry

        if user_input is not None:
            new_password = user_input.get(CONF_PASSWORD, "").strip()
            current_password = config_entry.data.get(CONF_PASSWORD)
            current_username = config_entry.data.get(CONF_USERNAME)
            
            password_changed = bool(new_password) and new_password != current_password
            username_changed = user_input[CONF_USERNAME] != current_username
            
            if password_changed or username_changed:
                password_to_use = new_password if password_changed else current_password
                username_to_use = user_input[CONF_USERNAME]
                
                if not password_to_use:
                    errors["base"] = "password_required"
                else:
                    session = aiohttp.ClientSession()
                    try:
                        api = MaisonProtegeeAPI(
                            username_to_use,
                            password_to_use,
                            session,
                        )

                        auth_result = await api.async_authenticate()
                        if auth_result:
                            await session.close()
                            updated_data = dict(config_entry.data)
                            updated_data[CONF_USERNAME] = username_to_use
                            updated_data[CONF_PASSWORD] = password_to_use
                            updated_data[CONF_ENABLE_TEMPERATURES] = user_input.get(CONF_ENABLE_TEMPERATURES, True)
                            updated_data[CONF_ENABLE_EVENTS] = user_input.get(CONF_ENABLE_EVENTS, True)
                            return self.async_create_entry(data=updated_data)
                        errors["base"] = "invalid_auth"
                    except aiohttp.ClientError as err:
                        _LOGGER.exception("Connection error during authentication: %s", err)
                        errors["base"] = "cannot_connect"
                    except Exception as err:
                        _LOGGER.exception("Unexpected exception during authentication: %s", err)
                        errors["base"] = "unknown"
                    finally:
                        await session.close()
            else:
                updated_data = dict(config_entry.data)
                updated_data[CONF_ENABLE_TEMPERATURES] = user_input.get(CONF_ENABLE_TEMPERATURES, True)
                updated_data[CONF_ENABLE_EVENTS] = user_input.get(CONF_ENABLE_EVENTS, True)
                return self.async_create_entry(data=updated_data)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=config_entry.data.get(CONF_USERNAME),
                    ): str,
                    vol.Optional(
                        CONF_PASSWORD,
                        default="",
                        description="Leave empty to keep current password",
                    ): str,
                    vol.Optional(
                        CONF_ENABLE_TEMPERATURES,
                        default=config_entry.data.get(
                            CONF_ENABLE_TEMPERATURES, True
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_EVENTS,
                        default=config_entry.data.get(CONF_ENABLE_EVENTS, True),
                    ): bool,
                }
            ),
            errors=errors,
        )

