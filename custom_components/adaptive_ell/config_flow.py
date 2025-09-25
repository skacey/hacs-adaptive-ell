"""Config flow for Adaptive ELL integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector, area_registry

from .const import DOMAIN


async def _get_area_options(hass: HomeAssistant) -> dict[str, str]:
    """Get list of available areas."""
    area_reg = area_registry.async_get(hass)
    areas = {"none": "None"}
    
    for area in area_reg.areas.values():
        areas[area.id] = area.name
    
    return areas


async def _get_illuminance_sensor_options(hass: HomeAssistant) -> dict[str, str]:
    """Get available illuminance sensors."""
    sensors = {}
    
    for state in hass.states.async_all():
        entity_id = state.entity_id
        if (entity_id.startswith("sensor.") and 
            (state.attributes.get("unit_of_measurement") in ["lux", "lm"] or
             state.attributes.get("device_class") == "illuminance" or
             "illuminance" in entity_id.lower() or
             "light" in entity_id.lower())):
            
            name = state.attributes.get("friendly_name", entity_id)
            sensors[entity_id] = name
    
    return sensors


class AdaptiveELLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Adaptive ELL."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return AdaptiveELLOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the initial step - check for existing entries first."""
        
        # Check for existing entries and offer removal
        existing_entries = self._async_current_entries()
        if existing_entries:
            return await self.async_step_cleanup()

        # Create entry with minimal data - real config happens in options
        return self.async_create_entry(
            title="Adaptive ELL",
            data={},
        )

    async def async_step_cleanup(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle cleanup of existing entries."""
        if user_input is not None:
            if user_input.get("remove_existing", False):
                # Remove existing entries
                try:
                    for entry in self._async_current_entries():
                        await self.hass.config_entries.async_remove(entry.entry_id)
                    # Continue to create new entry
                    return self.async_create_entry(title="Adaptive ELL", data={})
                except Exception as err:
                    return self.async_abort(reason="removal_failed")
            else:
                return self.async_abort(reason="existing_entry_kept")
        
        # Show cleanup form
        existing_titles = [entry.title for entry in self._async_current_entries()]
        
        return self.async_show_form(
            step_id="cleanup",
            data_schema=vol.Schema({
                vol.Required("remove_existing", default=True): selector.BooleanSelector(),
            }),
            description_placeholders={
                "existing_entries": ", ".join(existing_titles),
                "count": len(existing_titles)
            }
        )


class AdaptiveELLOptionsFlow(config_entries.OptionsFlow):
    """Handle Adaptive ELL options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""

    async def async_step_init(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return await self.async_step_areas(user_input)

    async def async_step_areas(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle area selection step."""
        errors = {}
        
        if user_input is not None:
            # Store area selections and move to sensor selection
            self._areas = user_input
            return await self.async_step_sensor()
        
        # Get current options or defaults
        current_options = self.config_entry.options
        area_options = await _get_area_options(self.hass)
        
        # Get current selections or defaults
        test_area = current_options.get("test_area", list(area_options.keys())[1] if len(area_options) > 1 else "none")
        
        return self.async_show_form(
            step_id="areas",
            data_schema=vol.Schema({
                vol.Required("test_area", default=test_area): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in area_options.items() if k != "none"]
                    )
                ),
                vol.Required("north_area", default=current_options.get("north_area", "none")): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in area_options.items()]
                    )
                ),
                vol.Required("south_area", default=current_options.get("south_area", "none")): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in area_options.items()]
                    )
                ),
                vol.Required("east_area", default=current_options.get("east_area", "none")): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in area_options.items()]
                    )
                ),
                vol.Required("west_area", default=current_options.get("west_area", "none")): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in area_options.items()]
                    )
                ),
            }),
            errors=errors,
            description_placeholders={
                "step": "1 of 2"
            }
        )

    async def async_step_sensor(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle sensor selection step."""
        errors = {}
        
        if user_input is not None:
            # Validate sensor
            sensor_entity = user_input["sensor_entity"]
            sensor_state = self.hass.states.get(sensor_entity)
            
            if not sensor_state:
                errors["sensor_entity"] = "sensor_not_found"
            else:
                try:
                    float(sensor_state.state)
                    # Valid sensor - combine with area data and create options
                    final_options = {**self._areas, **user_input}
                    
                    return self.async_create_entry(
                        title="Configuration Complete",
                        data=final_options
                    )
                except (ValueError, TypeError):
                    errors["sensor_entity"] = "invalid_sensor_reading"
        
        # Get sensor options
        sensor_options = await _get_illuminance_sensor_options(self.hass)
        
        if not sensor_options:
            return self.async_abort(reason="no_sensors_found")
        
        # Get current selection or default
        current_sensor = self.config_entry.options.get("sensor_entity", list(sensor_options.keys())[0])
        
        return self.async_show_form(
            step_id="sensor",
            data_schema=vol.Schema({
                vol.Required("sensor_entity", default=current_sensor): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": k, "label": v} for k, v in sensor_options.items()]
                    )
                ),
            }),
            errors=errors,
            description_placeholders={
                "step": "2 of 2",
                "test_area": self._get_area_name(self._areas["test_area"]),
                "adjacent_count": len([area for area in [
                    self._areas.get("north_area"),
                    self._areas.get("south_area"), 
                    self._areas.get("east_area"),
                    self._areas.get("west_area")
                ] if area and area != "none"])
            }
        )

    def _get_area_name(self, area_id: str) -> str:
        """Get area name from ID."""
        if area_id == "none":
            return "None"
        
        area_reg = area_registry.async_get(self.hass)
        area = area_reg.areas.get(area_id)
        return area.name if area else area_id