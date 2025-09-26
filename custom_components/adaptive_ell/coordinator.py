"""Adaptive ELL Coordinator for calibration and light management."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_ON, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import area_registry, entity_registry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AdaptiveELLCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Adaptive ELL calibration and data."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=300),  # 5 minutes
        )
        
        # Store config entry reference
        self.config_entry = config_entry
        
        # Configuration from options - all start empty/None
        self.room_name = None
        self.sensor_entity = None
        self.lights = []
        
        # Calibration state
        self.is_calibrating = False
        self.calibration_step = "idle"
        self.settle_time_seconds = 0
        self.timing_buffer = 1.25
        
        # Calibration results
        self.min_lux = 0
        self.max_lux = 0
        self.light_contributions = {}
        self.validation_results = {}
        
    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from Home Assistant."""
        try:
            data = {}
            
            # Basic state information
            data["calibrating"] = self.is_calibrating
            data["calibration_step"] = self.calibration_step
            
            # Get current sensor reading if configured
            if self.sensor_entity:
                sensor_state = self.hass.states.get(self.sensor_entity)
                if sensor_state and sensor_state.state not in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
                    try:
                        current_lux = float(sensor_state.state)
                        data["current_lux"] = current_lux
                    except (ValueError, TypeError):
                        data["current_lux"] = 0
                else:
                    data["current_lux"] = 0
            else:
                data["current_lux"] = 0
            
            # Add calibration results
            data["min_lux"] = self.min_lux
            data["max_lux"] = self.max_lux
            
            # Calculate estimated lux if we have calibration data
            if self.light_contributions and not self.is_calibrating:
                estimated_lux = await self._calculate_estimated_lux()
                data["estimated_lux"] = estimated_lux
            else:
                data["estimated_lux"] = 0
                
            return data
            
        except Exception as err:
            _LOGGER.error("Error updating data: %s", err)
            raise UpdateFailed(f"Error updating data: {err}")

    async def _calculate_estimated_lux(self) -> float:
        """Calculate estimated lux based on current light states."""
        if not self.light_contributions:
            return 0
        
        total_lux = self.min_lux  # Start with ambient/min level
        
        for light_entity, contribution_data in self.light_contributions.items():
            light_state = self.hass.states.get(light_entity)
            if not light_state or light_state.state != STATE_ON:
                continue
                
            brightness = light_state.attributes.get("brightness", 255)
            brightness_pct = brightness / 255.0
            max_contribution = contribution_data.get("max_contribution", 0)
            
            # Simple linear model for now
            light_contribution = max_contribution * brightness_pct
            total_lux += light_contribution
        
        return round(total_lux, 1)

    async def stop_calibration(self) -> None:
        """Stop any running calibration."""
        if not self.is_calibrating:
            return
            
        _LOGGER.info("Stopping calibration")
        self.is_calibrating = False
        self.calibration_step = "stopped"
        
        # Turn off all lights we were testing
        if self.lights:
            await self._set_all_lights(False)
        
        await self.async_request_refresh()

    async def _send_notification(self, title: str, message: str) -> None:
        """Send a persistent notification."""
        try:
            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": "adaptive_ell_calibration"
                }
            )
        except Exception as err:
            _LOGGER.warning("Failed to send notification: %s", err)

    async def _get_configuration_from_options(self) -> Dict[str, Any]:
        """Read configuration from integration options."""
        options = self.config_entry.options

        _LOGGER.error("=== CONFIGURATION DEBUG START ===")
        _LOGGER.error("Reading configuration from options: %s", options)

        if not options:
            raise HomeAssistantError("No configuration found - use integration options to configure")
        
        # Get test area information
        test_area_id = options.get("test_area")
        if not test_area_id:
            raise HomeAssistantError("No test area configured")
        
        area_reg = area_registry.async_get(self.hass)
        test_area = area_reg.areas.get(test_area_id)
        if not test_area:
            raise HomeAssistantError(f"Test area '{test_area_id}' not found")
        
        _LOGGER.error("Test area found: %s (ID: %s)", test_area.name, test_area.id)
        
        # Get sensor
        sensor_entity = options.get("sensor_entity")
        if not sensor_entity:
            raise HomeAssistantError("No sensor configured")
        
        _LOGGER.error("Sensor configured: %s", sensor_entity)
        
        # Get adjacent areas
        adjacent_areas = []
        adjacent_names = []
        for direction in ["north_area", "south_area", "east_area", "west_area"]:
            area_id = options.get(direction)
            _LOGGER.error("Direction %s: area_id = %s", direction, area_id)
            if area_id and area_id != "none":
                area = area_reg.areas.get(area_id)
                if area:
                    adjacent_areas.append(area)
                    adjacent_names.append(area.name)
                    _LOGGER.error("Adjacent area %s: %s (ID: %s)", direction, area.name, area.id)
                else:
                    _LOGGER.error("Adjacent area %s: ID %s not found in registry", direction, area_id)
        
        # Get all lights from test area and adjacent areas
        all_areas = [test_area] + adjacent_areas
        _LOGGER.error("Total areas to search: %d", len(all_areas))
        _LOGGER.error("Area list: %s", [f"{a.name} (ID: {a.id})" for a in all_areas])
        
        lights = await self._get_lights_from_areas(all_areas)
        
        _LOGGER.error("=== CONFIGURATION DEBUG END ===")
        
        return {
            "test_area": test_area.name,
            "sensor_entity": sensor_entity,
            "lights": lights,
            "adjacent_areas": adjacent_names,
            "area_count": len(all_areas)
        }

    async def _get_lights_from_areas(self, areas: List) -> List[str]:
        """Get all light entities from specified areas with extensive debugging."""
        try:
            ent_reg = entity_registry.async_get(self.hass)
            lights = []
            
            _LOGGER.error("=== LIGHT DISCOVERY DEBUG START ===")
            _LOGGER.error("Entity registry has %d total entities", len(ent_reg.entities))
            _LOGGER.error("Looking for lights in %d areas: %s", len(areas), [area.name for area in areas])
            
            # Debug: Show all light entities in the system
            all_light_entities = [e for e in ent_reg.entities.values() if e.entity_id.startswith("light.")]
            _LOGGER.error("Total light entities in system: %d", len(all_light_entities))
            
            for light in all_light_entities[:5]:  # Show first 5 for debugging
                _LOGGER.error("Sample light: %s, area_id: %s, disabled: %s", 
                            light.entity_id, light.area_id, light.disabled)
            
            # Check each area
            for area in areas:
                _LOGGER.error("--- Checking area: %s (ID: %s) ---", area.name, area.id)
                area_light_count = 0
                
                for entity in ent_reg.entities.values():
                    # Check if this entity belongs to current area and is a light
                    if entity.area_id == area.id and entity.entity_id.startswith("light."):
                        area_light_count += 1
                        _LOGGER.error("Found light in area: %s (disabled: %s)", entity.entity_id, entity.disabled)
                        
                        # Only add if not disabled
                        if not entity.disabled:
                            # Verify the entity actually exists in HA
                            state = self.hass.states.get(entity.entity_id)
                            if state:
                                lights.append(entity.entity_id)
                                _LOGGER.error("Added light to list: %s", entity.entity_id)
                            else:
                                _LOGGER.error("Light entity exists in registry but not in states: %s", entity.entity_id)
                        else:
                            _LOGGER.error("Skipping disabled light: %s", entity.entity_id)
                
                _LOGGER.error("Area %s summary: %d total lights, %d will be tested", area.name, area_light_count, len([l for l in lights if any(e.entity_id == l and e.area_id == area.id for e in ent_reg.entities.values())]))
            
            _LOGGER.error("=== LIGHT DISCOVERY DEBUG END ===")
            _LOGGER.error("Final light list (%d lights): %s", len(lights), lights)
            
            return lights
            
        except Exception as err:
            _LOGGER.error("Exception in _get_lights_from_areas: %s", err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return []

    async def start_calibration_from_options(self) -> None:
        """Start calibration using configuration from integration options."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        # Read configuration from options
        try:
            config = await self._get_configuration_from_options()
        except Exception as err:
            _LOGGER.error("Failed to read configuration: %s", err)
            raise HomeAssistantError(f"Failed to read configuration: {err}")
        
        # Update coordinator with configuration
        self.room_name = config["test_area"]
        self.sensor_entity = config["sensor_entity"] 
        self.lights = config["lights"]
        
        _LOGGER.info("Configuration loaded: area=%s, sensor=%s, lights=%d", 
                     self.room_name, self.sensor_entity, len(self.lights))
        
        if not self.lights:
            raise HomeAssistantError(f"No lights found in {config['area_count']} selected areas. Check that areas have light entities and they are not disabled.")
        
        _LOGGER.info("Starting calibration: area=%s, sensor=%s, lights=%d, adjacent_areas=%s", 
                     self.room_name, self.sensor_entity, len(self.lights), config["adjacent_areas"])
        
        # Use existing calibration logic
        await self.start_calibration()

    async def start_calibration(self) -> None:
        """Start the calibration process."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        if not self.room_name or not self.sensor_entity or not self.lights:
            raise HomeAssistantError("Calibration configuration incomplete - configure integration options first")
            
        _LOGGER.info("Starting calibration for room: %s with sensor: %s and %d lights", 
                     self.room_name, self.sensor_entity, len(self.lights))
        
        # Send start notification
        await self._send_notification(
            "Adaptive ELL Calibration Started",
            f"Calibrating {self.room_name.title()} with {len(self.lights)} lights. "
            f"This will take 10-15 minutes. Lights will turn on/off automatically."
        )
        
        self.is_calibrating = True
        self.calibration_step = "validation"
        
        try:
            # Step 1: Validate setup
            await self._validate_setup()
            
            # Step 2: Determine timing
            await self._calibrate_timing()
            
            # Step 3: Test min/max values
            await self._test_min_max_values()
            
            # Step 4: Test individual light contributions
            await self._test_light_contributions()
            
            # Step 5: Validate light pairs (with relaxed tolerance)
            await self._validate_light_pairs()
            
            # Step 6: Save calibration data
            await self._save_calibration_data()
            
            self.calibration_step = "completed"
            _LOGGER.info("Calibration completed successfully")
            
            # Send completion notification
            contributing_lights = len(self.light_contributions)
            total_contribution = sum(
                contrib.get("max_contribution", 0) 
                for contrib in self.light_contributions.values()
            )
            
            await self._send_notification(
                "Calibration Complete!",
                f"{self.room_name.title()} calibration finished successfully. "
                f"Found {contributing_lights} contributing lights "
                f"with {total_contribution:.0f} lux total contribution. "
                f"Range: {self.min_lux:.0f}-{self.max_lux:.0f} lux."
            )
            
        except Exception as err:
            _LOGGER.error("Calibration failed: %s", err)
            self.calibration_step = f"failed: {err}"
            
            # Send failure notification
            await self._send_notification(
                "Calibration Failed",
                f"{self.room_name.title()} calibration failed at step '{self.calibration_step}'. "
                f"Error: {str(err)[:100]}... Check logs for details."
            )
            
        finally:
            self.is_calibrating = False
            await self.async_request_refresh()

    async def _validate_setup(self) -> None:
        """Validate sensor and lights are available."""
        self.calibration_step = "validating_sensor"
        await self.async_request_refresh()
        
        # Check sensor
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} not found")
        
        if sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} is unavailable")
        
        try:
            current_lux = float(sensor_state.state)
            _LOGGER.info("Sensor validation passed: current reading %.1f lux", current_lux)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} does not report numeric illuminance value")
        
        self.calibration_step = "validating_lights"
        await self.async_request_refresh()
        
        # Check lights
        valid_lights = []
        for light_entity in self.lights:
            light_state = self.hass.states.get(light_entity)
            if not light_state:
                _LOGGER.warning("Light %s not found in states, skipping", light_entity)
                continue
            if light_state.domain != "light":
                _LOGGER.warning("Entity %s is not a light, skipping", light_entity)
                continue
            valid_lights.append(light_entity)
        
        if not valid_lights:
            raise HomeAssistantError("No valid lights found for testing")
        
        self.lights = valid_lights
        _LOGGER.info("Validated %d lights for testing: %s", len(self.lights), self.lights)

    async def _calibrate_timing(self) -> None:
        """Determine optimal timing by testing first light."""
        self.calibration_step = "calibrating_timing"
        await self.async_request_refresh()
        
        test_light = self.lights[0]
        _LOGGER.info("Calibrating timing using light: %s", test_light)
        
        # Turn off all lights first
        await self._set_all_lights(False)
        await asyncio.sleep(3)  # Initial settle
        
        initial_lux = await self._read_sensor()
        
        # Turn on test light and measure settle time
        await self._set_light_brightness(test_light, 255)
        
        readings = []
        for i in range(10):  # Test for 10 seconds
            await asyncio.sleep(1)
            lux = await self._read_sensor()
            readings.append(lux)
            
            # Check if settled (last 3 readings within 2 lux)
            if len(readings) >= 3:
                recent = readings[-3:]
                if max(recent) - min(recent) <= 2:
                    settle_time = i + 1
                    break
        else:
            settle_time = 5  # Default if no clear settle point
        
        # Add buffer
        self.settle_time_seconds = max(3, int(settle_time * self.timing_buffer))
        
        # Clean up
        await self._set_light_brightness(test_light, 0)
        await asyncio.sleep(self.settle_time_seconds)
        
        _LOGGER.info("Timing calibration complete: settle_time=%d seconds", self.settle_time_seconds)

    async def _test_min_max_values(self) -> None:
        """Test minimum and maximum light levels."""
        self.calibration_step = "testing_min_max"
        await self.async_request_refresh()
        
        # Test minimum (all lights off)
        await self._set_all_lights(False)
        await asyncio.sleep(self.settle_time_seconds)
        self.min_lux = await self._read_sensor()
        
        # Test maximum (all lights on full)
        await self._set_all_lights(True)
        await asyncio.sleep(self.settle_time_seconds)
        self.max_lux = await self._read_sensor()
        
        _LOGGER.info("Min/Max levels: min=%.1f lux, max=%.1f lux", self.min_lux, self.max_lux)
        
        if self.max_lux <= self.min_lux:
            raise HomeAssistantError(f"Invalid min/max values: min={self.min_lux}, max={self.max_lux}")

    async def _test_light_contributions(self) -> None:
        """Test individual light contributions."""
        self.calibration_step = "testing_contributions"
        await self.async_request_refresh()
        
        self.light_contributions = {}
        
        for i, light_entity in enumerate(self.lights):
            _LOGGER.info("Testing light %d/%d: %s", i+1, len(self.lights), light_entity)
            
            # Turn off all lights
            await self._set_all_lights(False)
            await asyncio.sleep(self.settle_time_seconds)
            base_lux = await self._read_sensor()
            
            # Turn on this light
            await self._set_light_brightness(light_entity, 255)
            await asyncio.sleep(self.settle_time_seconds)
            with_light_lux = await self._read_sensor()
            
            contribution = with_light_lux - base_lux
            
            self.light_contributions[light_entity] = {
                "max_contribution": max(0, contribution),
                "base_reading": base_lux,
                "with_light_reading": with_light_lux,
                "linear_validated": True  # Will validate in next step
            }
            
            _LOGGER.info("Light %s contribution: %.1f lux", light_entity, contribution)

    async def _validate_light_pairs(self) -> None:
        """Validate that light contributions are approximately additive."""
        self.calibration_step = "validating_pairs"
        await self.async_request_refresh()
        
        # Test a few pairs to validate additivity
        lights_to_test = list(self.light_contributions.keys())[:3]  # Test first 3 lights
        
        for i in range(len(lights_to_test) - 1):
            light1 = lights_to_test[i]
            light2 = lights_to_test[i + 1]
            
            # Get individual contributions
            contrib1 = self.light_contributions[light1]["max_contribution"]
            contrib2 = self.light_contributions[light2]["max_contribution"]
            expected_total = contrib1 + contrib2
            
            # Test both lights together
            await self._set_all_lights(False)
            await asyncio.sleep(self.settle_time_seconds)
            base_lux = await self._read_sensor()
            
            await self._set_light_brightness(light1, 255)
            await self._set_light_brightness(light2, 255)
            await asyncio.sleep(self.settle_time_seconds)
            both_lights_lux = await self._read_sensor()
            
            actual_total = both_lights_lux - base_lux
            
            # Calculate error percentage
            error_pct = abs(actual_total - expected_total) / expected_total * 100 if expected_total > 0 else 0
            
            _LOGGER.info("Pair validation %s + %s: expected=%.1f, actual=%.1f, error=%.1f%%", 
                        light1, light2, expected_total, actual_total, error_pct)
            
            # Store validation results (relaxed tolerance - 30% is acceptable for MVP)
            self.validation_results[f"{light1}+{light2}"] = {
                "expected": expected_total,
                "actual": actual_total,
                "error_percent": error_pct,
                "valid": error_pct <= 30
            }

    async def _save_calibration_data(self) -> None:
        """Save calibration data to config entry."""
        self.calibration_step = "saving_data"
        await self.async_request_refresh()
        
        calibration_data = {
            "timestamp": datetime.now().isoformat(),
            "room_name": self.room_name,
            "min_lux": self.min_lux,
            "max_lux": self.max_lux,
            "light_contributions": self.light_contributions,
            "validation_results": self.validation_results,
            "settle_time_seconds": self.settle_time_seconds
        }
        
        # Update config entry data
        new_data = {**self.config_entry.data, "calibration": calibration_data}
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        
        _LOGGER.info("Calibration data saved successfully")

    async def _set_all_lights(self, state: bool) -> None:
        """Turn all lights on or off."""
        brightness = 255 if state else 0
        tasks = [self._set_light_brightness(light, brightness) for light in self.lights]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _set_light_brightness(self, entity_id: str, brightness: int) -> None:
        """Set light brightness (0 = off, 255 = full brightness)."""
        try:
            if brightness > 0:
                await self.hass.services.async_call(
                    "light", "turn_on",
                    {"entity_id": entity_id, "brightness": brightness},
                    blocking=True
                )
            else:
                await self.hass.services.async_call(
                    "light", "turn_off",
                    {"entity_id": entity_id},
                    blocking=True
                )
        except Exception as err:
            _LOGGER.error("Failed to control light %s: %s", entity_id, err)

    async def _read_sensor(self) -> float:
        """Read current sensor value."""
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state or sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} unavailable")
        
        try:
            return float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} returned non-numeric value: {sensor_state.state}")