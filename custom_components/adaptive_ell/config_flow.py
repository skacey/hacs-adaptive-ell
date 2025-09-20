"""Config flow for Adaptive ELL integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class AdaptiveELLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Adaptive ELL."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # For V0.1, we just auto-configure with hardcoded Office setup
        if user_input is not None or self._async_current_entries():
            return self.async_create_entry(
                title="Adaptive ELL - Office Test",
                data={
                    "room_name": "office",
                    "sensor_entity": "sensor.andon_sensor_illuminance",
                }
            )

        # Check if already configured
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "room": "Office",
                "sensor": "sensor.andon_sensor_illuminance",
                "lights": "6 hardcoded test lights"
            }
        )