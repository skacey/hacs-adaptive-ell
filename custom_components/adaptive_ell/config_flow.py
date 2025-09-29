"""Config flow for Adaptive ELL integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import area_registry, entity_registry, device_registry, selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _get_area_options(hass: HomeAssistant) -> Dict[str, str]:
    """Get area options for selection."""
    area_reg = area_registry.async_get(hass)
    
    areas = {}
    for area_id, area in area_reg.areas.items():
        areas[area_id] = area.name
    
    # Sort by name
    sorted_areas = dict(sorted(areas.items(), key=lambda x: x[1]))
    return sorted_areas


async def _get_lux_sensor_options(hass: HomeAssistant) -> Dict[str, str]:
    """Get illuminance sensor options with better detection."""
    ent_reg = entity_registry.async_get(hass)
    sensors = {}
    
    for entity in ent_reg.entities.values():
        if not entity.entity_id.startswith("sensor."):
            continue
            
        if entity.disabled:
            continue
            
        # Skip our own domain entities
        if entity.entity_id.startswith(f"sensor.{DOMAIN}"):
            continue
            
        # Get entity state to check device class and attributes
        state = hass.states.get(entity.entity_id)
        if not state:
            continue
            
        # Multiple ways to detect lux sensors - be very inclusive
        device_class = state.attributes.get("device_class", "").lower()
        unit = state.attributes.get("unit_of_measurement", "").lower()
        entity_name = state.attributes.get("friendly_name", entity.entity_id).lower()
        entity_id_lower = entity.entity_id.lower()
        
        # Check various indicators for lux sensors
        is_lux_sensor = (
            device_class == "illuminance" or
            "lux" in unit or
            "illuminance" in entity_name or
            "illuminance" in entity_id_lower or
            "light" in entity_name or
            "light" in entity_id_lower or
            "roomsense" in entity_name or
            "roomsense" in entity_id_lower
        )
        
        if is_lux_sensor:
            # Get friendly name and add current value if available
            friendly_name = state.attributes.get("friendly_name", entity.entity_id)
            try:
                current_value = float(state.state)
                if current_value >= 0:  # Valid lux reading
                    friendly_name += f" (Current: {current_value:.0f} {unit})"
            except (ValueError, TypeError):
                pass
            
            sensors[entity.entity_id] = friendly_name
            _LOGGER.debug("Found lux sensor: %s, unit: %s, device_class: %s", 
                         entity.entity_id, unit, device_class)
    
    _LOGGER.info("Found %d potential lux sensors: %s", len(sensors), list(sensors.keys()))
    return dict(sorted(sensors.items(), key=lambda x: x[1]))


async def _count_lights_in_areas(hass: HomeAssistant, area_ids: List[str]) -> int:
    """Count lights in specified areas."""
    ent_reg = entity_registry.async_get(hass)
    dev_reg = device_registry.async_get(hass)
    light_count = 0
    
    for entity in ent_reg.entities.values():
        if not entity.entity_id.startswith("light."):
            continue
            
        if entity.disabled:
            continue
            
        # Get device area
        entity_area_id = None
        if entity.device_id:
            device = dev_reg.devices.get(entity.device_id)
            if device:
                entity_area_id = device.area_id
        
        if entity_area_id in area_ids:
            # Verify entity exists
            if hass.states.get(entity.entity_id):
                light_count += 1
    
    return light_count


def _check_existing_helper(hass: HomeAssistant, area_id: str) -> bool:
    """Check if helper already exists for this area."""
    area_reg = area_registry.async_get(hass)
    area = area_reg.areas.get(area_id)
    if not area:
        return False
    
    # Generate expected helper entity ID
    area_slug = area.normalized_name or area.name.lower().replace(" ", "_")
    helper_entity_id = f"sensor.adaptive_ell_{area_slug}"
    
    # Check if entity exists
    return hass.states.get(helper_entity_id) is not None


class AdaptiveELLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Adaptive ELL."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._area_id: str | None = None
        self._sensor_entity: str | None = None
        self._selected_areas: List[str] = []

    async def async_step_user(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # Check for existing entries first
        existing_entries = self._async_current_entries()
        
        if existing_entries and user_input is None:
            return await self.async_step_cleanup()
            
        return await self.async_step_area()

    async def async_step_cleanup(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle cleanup of existing entries."""
        existing_entries = self._async_current_entries()
        
        if user_input is not None:
            action = user_input.get("action")
            
            if action == "remove_all":
                # Force remove all existing entries
                try:
                    for entry in existing_entries:
                        await self.hass.config_entries.async_remove(entry.entry_id)
                    return await self.async_step_area()
                except Exception as err:
                    return self.async_show_form(
                        step_id="cleanup",
                        data_schema=vol.Schema({
                            vol.Required("action"): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=[
                                        {"value": "remove_all", "label": "🗑️ Remove all existing entries"},
                                        {"value": "continue", "label": "➕ Add new entry anyway"},
                                        {"value": "abort", "label": "❌ Cancel setup"}
                                    ]
                                )
                            )
                        }),
                        errors={"base": f"Failed to remove entries: {err}"},
                        description_placeholders={
                            "existing_count": str(len(existing_entries)),
                            "existing_names": ", ".join([entry.title for entry in existing_entries])
                        }
                    )
            elif action == "continue":
                return await self.async_step_area()
            else:  # abort
                return self.async_abort(reason="user_cancelled")
        
        # Show cleanup options
        entry_details = []
        for entry in existing_entries:
            area_name = "Unknown"
            if entry.data.get("test_area"):
                area_reg = area_registry.async_get(self.hass)
                area = area_reg.areas.get(entry.data["test_area"])
                if area:
                    area_name = area.name
            entry_details.append(f"'{entry.title}' (Area: {area_name})")
        
        return self.async_show_form(
            step_id="cleanup",
            data_schema=vol.Schema({
                vol.Required("action"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "remove_all", "label": "🗑️ Remove all existing entries"},
                            {"value": "continue", "label": "➕ Add new entry anyway"},
                            {"value": "abort", "label": "❌ Cancel setup"}
                        ]
                    )
                )
            }),
            description_placeholders={
                "existing_count": str(len(existing_entries)),
                "existing_names": "\n".join(entry_details),
                "instruction": "You have existing Adaptive ELL entries. Choose how to proceed:"
            }
        )

    async def async_step_area(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle target area selection step."""
        errors = {}
        
        if user_input is not None:
            area_id = user_input["area"]
            
            # Check if this area already has lights
            light_count = await _count_lights_in_areas(self.hass, [area_id])
            if light_count == 0:
                errors["area"] = "no_lights"
            else:
                self._area_id = area_id
                return await self.async_step_sensor()
        
        # Get area options
        area_options = await _get_area_options(self.hass)
        
        if not area_options:
            return self.async_abort(reason="no_areas")
        
        # Check for existing helpers and add warnings
        area_choices = []
        for area_id, area_name in area_options.items():
            light_count = await _count_lights_in_areas(self.hass, [area_id])
            has_existing = _check_existing_helper(self.hass, area_id)
            
            if light_count > 0:
                label = f"{area_name} ({light_count} lights)"
                if has_existing:
                    label += " - ⚠️ Will recalibrate"
                area_choices.append({"value": area_id, "label": label})
        
        if not area_choices:
            return self.async_abort(reason="no_areas_with_lights")
        
        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema({
                vol.Required("area"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=area_choices,
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }),
            errors=errors,
            description_placeholders={
                "step": "Step 1: Select Target Room",
                "instruction": "Choose the room you want to calibrate. You will physically move your lux sensor to this room for testing."
            }
        )

    async def async_step_sensor(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle sensor selection step."""
        errors = {}
        
        if user_input is not None:
            sensor_entity = user_input["sensor"]
            
            # Validate sensor exists and is available
            sensor_state = self.hass.states.get(sensor_entity)
            if not sensor_state:
                errors["sensor"] = "sensor_not_found"
            elif sensor_state.state in ["unavailable", "unknown"]:
                errors["sensor"] = "sensor_unavailable"
            else:
                self._sensor_entity = sensor_entity
                return await self.async_step_areas()
        
        # Get sensor options
        sensor_options = await _get_lux_sensor_options(self.hass)
        
        if not sensor_options:
            return self.async_abort(reason="no_sensors")
        
        sensor_choices = [
            {"value": entity_id, "label": name}
            for entity_id, name in sensor_options.items()
        ]
        
        return self.async_show_form(
            step_id="sensor",
            data_schema=vol.Schema({
                vol.Required("sensor"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sensor_choices,
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }),
            errors=errors,
            description_placeholders={
                "step": "Step 2: Select Your Best Lux Sensor",
                "instruction": "Choose your most accurate lux sensor. You will physically move this sensor to the target room during calibration. Current readings are shown to help you pick one that's working."
            }
        )

    async def async_step_areas(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle area selection with dynamic time estimate."""
        if user_input is not None:
            selected_areas = user_input.get("areas", [])
            self._selected_areas = selected_areas
            return await self.async_step_confirm()
        
        # Get all area options (excluding target area)
        area_options = await _get_area_options(self.hass)
        
        area_choices = []
        total_lights_available = 0
        
        for area_id, area_name in area_options.items():
            if area_id == self._area_id:
                continue  # Skip target area - it's automatically included
                
            light_count = await _count_lights_in_areas(self.hass, [area_id])
            if light_count > 0:
                area_choices.append({
                    "value": area_id,
                    "label": f"{area_name} ({light_count} lights)"
                })
                total_lights_available += light_count
        
        # Calculate target area lights
        target_lights = await _count_lights_in_areas(self.hass, [self._area_id])
        target_area_name = area_options.get(self._area_id, "Unknown")
        
        # Default to all areas selected for brute force approach
        default_selected = [choice["value"] for choice in area_choices]
        
        # Calculate estimates
        min_time = max(3, round((2 + (target_lights * 0.5)) * 1.2))  # Target only
        max_time = max(3, round((2 + ((target_lights + total_lights_available) * 0.5)) * 1.2))  # All areas
        
        return self.async_show_form(
            step_id="areas",
            data_schema=vol.Schema({
                vol.Optional("areas", default=default_selected): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=area_choices,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=True
                    )
                )
            }),
            description_placeholders={
                "step": "Step 3: Choose Additional Areas to Test",
                "target_area": target_area_name,
                "target_lights": str(target_lights),
                "min_time": str(min_time),
                "max_time": str(max_time),
                "instruction": f"{target_area_name} will always be tested ({target_lights} lights). Select additional areas to test for lights that might affect {target_area_name}. All areas selected by default - remove areas to reduce calibration time.",
                "recommendation": f"Time estimate: {min_time} minutes (target only) to {max_time} minutes (all areas). Remove distant areas to speed up calibration."
            }
        )

    async def async_step_confirm(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle final confirmation."""
        if user_input is not None:
            # Check for existing entries for this area and remove them
            area_name = self._get_area_name()
            existing_entries = []
            
            for entry in self._async_current_entries():
                if entry.data.get("test_area") == self._area_id:
                    existing_entries.append(entry)
            
            # Remove existing entries
            for entry in existing_entries:
                await self.hass.config_entries.async_remove(entry.entry_id)
                _LOGGER.info("Removed duplicate entry for area: %s", area_name)
            
            return self.async_create_entry(
                title=f"Adaptive ELL - {area_name}",
                data={
                    "test_area": self._area_id,
                    "sensor_entity": self._sensor_entity,
                    "calibration_scope": "selected",  # Always selected now
                    "selected_areas": self._selected_areas,
                }
            )
        
        # Calculate final summary
        area_name = self._get_area_name()
        test_areas = [self._area_id] + self._selected_areas
        total_lights = await _count_lights_in_areas(self.hass, test_areas)
        area_names = [self._get_area_name(area_id) for area_id in test_areas]
        
        estimated_time = max(3, round((2 + (total_lights * 0.5)) * 1.2))
        
        # Check for recalibration warning
        has_existing = _check_existing_helper(self.hass, self._area_id)
        recalibration_warning = "⚠️ This will overwrite existing calibration data for this area." if has_existing else ""
        
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "area_name": area_name,
                "sensor_name": self._get_sensor_name(),
                "scope_description": f"{total_lights} lights in {len(test_areas)} areas: {', '.join(area_names)}",
                "estimated_time": str(estimated_time),
                "recalibration_warning": recalibration_warning,
                "next_steps": f"After setup: 1) Physically move your lux sensor to {area_name}, 2) Go to Settings > Devices & Services > Adaptive ELL - {area_name} > Configure, 3) Enable 'Start Calibration' to begin the {estimated_time}-minute process."
            }
        )

    def _get_area_name(self, area_id: str = None) -> str:
        """Get area name from area ID."""
        area_reg = area_registry.async_get(self.hass)
        area = area_reg.areas.get(area_id or self._area_id)
        return area.name if area else "Unknown"

    def _get_sensor_name(self) -> str:
        """Get sensor friendly name."""
        if not self._sensor_entity:
            return "Unknown"
        
        state = self.hass.states.get(self._sensor_entity)
        if state:
            return state.attributes.get("friendly_name", self._sensor_entity)
        return self._sensor_entity

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return AdaptiveELLOptionsFlow(config_entry)


class AdaptiveELLOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Adaptive ELL."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            if user_input.get("start_calibration", False):
                try:
                    # Get the area for this config entry
                    area_id = self.config_entry.data.get("test_area")
                    area_reg = area_registry.async_get(self.hass)
                    area = area_reg.areas.get(area_id)
                    
                    if area:
                        # Call the service with area parameter to identify the coordinator
                        await self.hass.services.async_call(
                            DOMAIN, 
                            "start_calibration",
                            {"area": area.name}
                        )
                        return self.async_create_entry(
                            title="", 
                            data={}, 
                            description="Calibration started! Check the calibration sensor for progress."
                        )
                    else:
                        raise Exception("Area not found")
                        
                except Exception as err:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=vol.Schema({
                            vol.Optional("start_calibration", default=False): bool,
                        }),
                        errors={"base": f"Failed to start calibration: {err}"},
                        description_placeholders={
                            "area_name": self._get_area_name(),
                            "instruction": f"Make sure your lux sensor is physically located in {self._get_area_name()}, then check 'Start Calibration' and click Submit.",
                            "warning": "Calibration will automatically turn lights on and off for testing. This process takes 10-15 minutes."
                        }
                    )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("start_calibration", default=False): bool,
            }),
            description_placeholders={
                "area_name": self._get_area_name(),
                "instruction": f"Make sure your lux sensor is physically located in {self._get_area_name()}, then check 'Start Calibration' and click Submit to begin the calibration process.",
                "warning": "Calibration will automatically turn lights on and off for testing. This process takes 10-15 minutes."
            }
        )

    def _get_area_name(self) -> str:
        """Get area name from config entry."""
        current_data = self.config_entry.data
        area_reg = area_registry.async_get(self.hass)
        area = area_reg.areas.get(current_data.get("test_area"))
        return area.name if area else "Unknown"