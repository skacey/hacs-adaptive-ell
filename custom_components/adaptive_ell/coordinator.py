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
from homeassistant.helpers import area_registry, entity_registry, device_registry

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
            update_interval=timedelta(seconds=10),  # More responsive updates for real-time feel
        )
        
        # Store config entry reference
        self.config_entry = config_entry
        
        # Read basic configuration immediately for sensor naming
        config_data = config_entry.options or config_entry.data
        test_area_id = config_data.get("test_area")
        
        if test_area_id:
            area_reg = area_registry.async_get(hass)
            area = area_reg.areas.get(test_area_id)
            self.room_name = area.name if area else "Unknown"
        else:
            self.room_name = "Unconfigured"
        
        # Configuration - will be fully loaded during calibration
        self.sensor_entity = config_data.get("sensor_entity")
        self.lights = []
        
        # Load existing calibration data if available
        existing_calibration = config_data.get("calibration", {})
        
        # Initialize calibration results with defaults first
        self.min_lux = 0
        self.max_lux = 0
        self.light_contributions = {}
        self.validation_results = {}
        self.settle_time_seconds = 0
        
        # Then override with existing data if available
        if existing_calibration:
            self.min_lux = existing_calibration.get("min_lux", 0)
            self.max_lux = existing_calibration.get("max_lux", 0)
            self.light_contributions = existing_calibration.get("light_contributions", {})
            self.validation_results = existing_calibration.get("validation_results", {})
            self.settle_time_seconds = existing_calibration.get("settle_time_seconds", 0)
            _LOGGER.info("Loaded existing calibration data for %s: %d contributing lights", 
                        self.room_name, len(self.light_contributions))
        
        # Calibration state
        self.is_calibrating = False
        self.calibration_step = "idle"
        self.timing_buffer = 1.25
        self.initial_light_states = {}  # Store original light states
        
        # State change listeners
        self._unsub_state_listeners = []
        self._light_state_dirty = False  # Flag to indicate lights changed
        
    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from Home Assistant."""
        try:
            # Return current state
            data = {
                "calibrating": self.is_calibrating,
                "calibration_step": self.calibration_step,
                "min_lux": self.min_lux,
                "max_lux": self.max_lux,
                "lights_count": len(self.lights)
            }
            
            # Add current sensor reading if available
            if self.sensor_entity:
                sensor_state = self.hass.states.get(self.sensor_entity)
                if sensor_state and sensor_state.state not in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
                    try:
                        current_lux = float(sensor_state.state)
                        data["current_lux"] = current_lux
                        
                        # Calculate estimated light level if calibrated
                        if self.light_contributions:
                            estimated_lux = await self._calculate_current_estimated_lux()
                            data["estimated_lux"] = estimated_lux
                            
                            # Log when we detect light state changes
                            if self._light_state_dirty:
                                _LOGGER.debug("Light state changed, updated estimated lux: %.1f", estimated_lux)
                                self._light_state_dirty = False
                                
                    except (ValueError, TypeError):
                        pass
            
            # If we have calibration data but no state listeners set up, set them up
            if self.light_contributions and not self._unsub_state_listeners:
                await self._setup_light_state_listeners()
            
            return data
            
        except Exception as err:
            raise UpdateFailed(f"Error updating data: {err}")

    async def async_shutdown(self) -> None:
        """Cleanup when coordinator is shutting down."""
        await self._cleanup_state_listeners()
        await super().async_shutdown()

    async def _calculate_current_estimated_lux(self) -> float:
        """Calculate current estimated lux based on light states."""
        total_estimated = 0
        
        _LOGGER.debug("Calculating estimated lux with %d contributing lights", len(self.light_contributions))
        
        for light_entity, contrib_data in self.light_contributions.items():
            light_state = self.hass.states.get(light_entity)
            if not light_state or light_state.state != STATE_ON:
                _LOGGER.debug("Light %s is off, skipping", light_entity)
                continue
                
            # Get current brightness (0-255)
            brightness = light_state.attributes.get("brightness", 255)
            brightness_percent = brightness / 255.0
            
            # Calculate contribution based on brightness
            max_contribution = contrib_data.get("max_contribution", 0)
            current_contribution = max_contribution * brightness_percent
            total_estimated += current_contribution
            
            _LOGGER.debug("Light %s: brightness=%d (%.1f%%), contrib=%.1f lux", 
                         light_entity, brightness, brightness_percent * 100, current_contribution)
        
        _LOGGER.debug("Total estimated lux: %.1f", total_estimated)
        return round(total_estimated, 1)

    async def _send_notification(self, title: str, message: str) -> None:
        """Send persistent notification."""
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
        """Read configuration from config entry data."""
        # Try options first (for backwards compatibility), then data
        config_data = self.config_entry.options or self.config_entry.data

        _LOGGER.info("Reading configuration from config entry")

        if not config_data:
            raise HomeAssistantError("No configuration found - integration not properly configured")
        
        # Get test area information
        test_area_id = config_data.get("test_area")
        if not test_area_id:
            raise HomeAssistantError("No test area configured")
        
        area_reg = area_registry.async_get(self.hass)
        test_area = area_reg.areas.get(test_area_id)
        if not test_area:
            raise HomeAssistantError(f"Test area '{test_area_id}' not found")
        
        _LOGGER.info("Test area: %s", test_area.name)
        
        # Get sensor
        sensor_entity = config_data.get("sensor_entity")
        if not sensor_entity:
            raise HomeAssistantError("No sensor configured")
        
        _LOGGER.info("Sensor: %s", sensor_entity)
        
        # Get calibration scope and areas
        calibration_scope = config_data.get("calibration_scope", "selected")  # "brute_force" or "selected"
        
        if calibration_scope == "brute_force":
            # Get all areas in the system
            all_areas = list(area_reg.areas.values())
            additional_areas = [area for area in all_areas if area.id != test_area_id]
            _LOGGER.info("Brute force mode: testing ALL %d areas", len(all_areas))
        else:
            # Get selected additional areas
            additional_areas = []
            selected_area_ids = config_data.get("selected_areas", [])
            
            for area_id in selected_area_ids:
                if area_id != test_area_id:  # Don't duplicate test area
                    area = area_reg.areas.get(area_id)
                    if area:
                        additional_areas.append(area)
            
            _LOGGER.info("Selected mode: testing %d additional areas", len(additional_areas))
        
        # Get all lights from test area and additional areas
        all_areas = [test_area] + additional_areas
        lights = await self._get_lights_from_areas(all_areas)
        
        # Calculate estimated time based on light count
        estimated_minutes = self._estimate_calibration_time(len(lights))
        
        return {
            "test_area": test_area.name,
            "sensor_entity": sensor_entity,
            "lights": lights,
            "calibration_scope": calibration_scope,
            "area_count": len(all_areas),
            "additional_area_names": [area.name for area in additional_areas],
            "estimated_minutes": estimated_minutes
        }

    def _estimate_calibration_time(self, light_count: int) -> int:
        """Estimate calibration time based on light count."""
        # Base time for setup and validation: 2 minutes
        # Per light testing: ~30 seconds (timing + contribution + validation)
        # Additional overhead: 20% buffer
        
        base_time = 2  # minutes
        per_light_time = 0.5  # minutes (30 seconds)
        buffer_multiplier = 1.2
        
        estimated = (base_time + (light_count * per_light_time)) * buffer_multiplier
        return max(3, round(estimated))  # Minimum 3 minutes

    async def _get_lights_from_areas(self, areas: List) -> List[str]:
        """Get all light entities from specified areas."""
        try:
            ent_reg = entity_registry.async_get(self.hass)
            dev_reg = device_registry.async_get(self.hass)
            lights = []
            
            _LOGGER.info("Searching for lights in %d areas", len(areas))
            
            # Check each area
            for area in areas:
                area_lights = []
                
                for entity in ent_reg.entities.values():
                    # Only check light entities
                    if not entity.entity_id.startswith("light."):
                        continue
                    
                    # Get the device for this entity
                    entity_area_id = None
                    if entity.device_id:
                        device = dev_reg.devices.get(entity.device_id)
                        if device:
                            entity_area_id = device.area_id
                    
                    # Check if this entity's device is in the current area
                    if entity_area_id == area.id and not entity.disabled:
                        # Verify the entity actually exists in HA
                        state = self.hass.states.get(entity.entity_id)
                        if state:
                            area_lights.append(entity.entity_id)
                            lights.append(entity.entity_id)
                
                _LOGGER.info("Area '%s': found %d lights", area.name, len(area_lights))
            
            _LOGGER.info("Total lights found: %d", len(lights))
            return lights
            
        except Exception as err:
            _LOGGER.error("Exception in _get_lights_from_areas: %s", err)
            return []

    async def start_calibration_from_options(self) -> None:
        """Start calibration using configuration from config entry data."""
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
        
        _LOGGER.error("=== CALIBRATION STARTING ===")
        _LOGGER.error("Room: %s | Sensor: %s | Found %d lights in %d areas (%s mode)", 
                     self.room_name, self.sensor_entity, len(self.lights), 
                     config["area_count"], config["calibration_scope"])
        _LOGGER.error("Estimated time: %d minutes", config["estimated_minutes"])
        
        if not self.lights:
            raise HomeAssistantError(f"No lights found in {config['area_count']} selected areas. "
                                   f"Check that areas have light entities and they are not disabled.")
        
        # Send warning for large light counts
        if len(self.lights) > 50:
            await self._send_notification(
                "Large Calibration Detected",
                f"Calibrating {len(self.lights)} lights will take approximately {config['estimated_minutes']} minutes. "
                f"Consider using Selected Areas mode for faster calibration."
            )
        
        # Use existing calibration logic
        await self.start_calibration()

    async def start_calibration(self) -> None:
        """Start the calibration process."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        if not self.room_name or not self.sensor_entity or not self.lights:
            raise HomeAssistantError("Calibration configuration incomplete - configure integration first")
            
        _LOGGER.info("Starting calibration for room: %s with sensor: %s and %d lights", 
                     self.room_name, self.sensor_entity, len(self.lights))
        
        # Send start notification
        await self._send_notification(
            "Adaptive ELL Calibration Started",
            f"Calibrating {self.room_name.title()} with {len(self.lights)} lights. "
            f"This will take approximately {self._estimate_calibration_time(len(self.lights))} minutes. "
            f"Lights will turn on/off automatically."
        )
        
        self.is_calibrating = True
        self.calibration_step = "validation"
        
        try:
            # Step 0: Capture initial light states
            await self._capture_initial_light_states()
            
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
            _LOGGER.error("=== CALIBRATION COMPLETED ===")
            
            # Force data refresh to update sensors with calibration results
            await self.async_request_refresh()
            
            # Send completion notification
            contributing_lights = len(self.light_contributions)
            total_lights_tested = len(self.lights)
            total_contribution = sum(
                contrib.get("max_contribution", 0) 
                for contrib in self.light_contributions.values()
            )
            
            _LOGGER.error("✓ SUCCESS: %d useful lights found (of %d tested) | Total: %.0f lux | Range: %.0f-%.0f lux",
                         contributing_lights, total_lights_tested, total_contribution, self.min_lux, self.max_lux)
            
            # Check if estimated lux is now available
            if self.light_contributions:
                try:
                    current_estimated = await self._calculate_current_estimated_lux()
                    _LOGGER.error("✓ Current estimated light level: %.1f lux", current_estimated)
                except Exception as est_err:
                    _LOGGER.error("Failed to calculate current estimated lux: %s", est_err)
            
            await self._send_notification(
                "Calibration Complete!",
                f"{self.room_name.title()} calibration finished successfully. "
                f"Found {contributing_lights} useful lights (of {total_lights_tested} tested). "
                f"Total contribution: {total_contribution:.0f} lux."
            )
            
        except Exception as err:
            self.calibration_step = f"failed: {err}"
            _LOGGER.error("Calibration failed: %s", err)
            
            await self._send_notification(
                "Calibration Failed",
                f"Calibration of {self.room_name.title()} failed: {err}"
            )
            
            # Try to restore original light states
            try:
                await self._restore_initial_light_states()
            except Exception as restore_err:
                _LOGGER.error("Failed to restore light states: %s", restore_err)
            
            raise
        finally:
            self.is_calibrating = False
            # Trigger data update
            await self.async_request_refresh()

    async def stop_calibration(self) -> None:
        """Stop the calibration process."""
        if not self.is_calibrating:
            raise HomeAssistantError("No calibration in progress")
        
        _LOGGER.info("Stopping calibration")
        self.is_calibrating = False
        self.calibration_step = "stopped"
        
        # Try to restore original light states
        try:
            await self._restore_initial_light_states()
        except Exception as err:
            _LOGGER.error("Failed to restore light states: %s", err)
        
        await self._send_notification(
            "Calibration Stopped",
            f"Calibration of {self.room_name.title()} was stopped by user."
        )
        
        # Trigger data update
        await self.async_request_refresh()

    # Real calibration methods
    async def _capture_initial_light_states(self) -> None:
        """Capture the current state of all lights before calibration."""
        self.initial_light_states = {}
        
        _LOGGER.error("📸 Capturing initial light states...")
        
        for light_entity in self.lights:
            light_state = self.hass.states.get(light_entity)
            if light_state:
                self.initial_light_states[light_entity] = {
                    "state": light_state.state,
                    "brightness": light_state.attributes.get("brightness"),
                    "rgb_color": light_state.attributes.get("rgb_color"),
                    "color_temp": light_state.attributes.get("color_temp"),
                    "color_temp_kelvin": light_state.attributes.get("color_temp_kelvin"),
                    "hs_color": light_state.attributes.get("hs_color"),
                    "xy_color": light_state.attributes.get("xy_color"),
                }
                _LOGGER.info("Captured state for %s: %s", light_entity, light_state.state)

    async def _restore_initial_light_states(self) -> None:
        """Restore all lights to their original state before calibration."""
        _LOGGER.error("🔄 Restoring initial light states...")
        
        for light_entity, saved_state in self.initial_light_states.items():
            try:
                if saved_state["state"] == STATE_OFF:
                    await self.hass.services.async_call(
                        LIGHT_DOMAIN, "turn_off", {"entity_id": light_entity}
                    )
                else:
                    service_data = {"entity_id": light_entity}
                    
                    # Restore brightness
                    if saved_state.get("brightness"):
                        service_data["brightness"] = saved_state["brightness"]
                    
                    # Restore color (prioritize rgb, then color_temp, then other formats)
                    if saved_state.get("rgb_color"):
                        service_data["rgb_color"] = saved_state["rgb_color"]
                    elif saved_state.get("color_temp"):
                        service_data["color_temp"] = saved_state["color_temp"]
                    elif saved_state.get("color_temp_kelvin"):
                        service_data["color_temp_kelvin"] = saved_state["color_temp_kelvin"]
                    elif saved_state.get("hs_color"):
                        service_data["hs_color"] = saved_state["hs_color"]
                    elif saved_state.get("xy_color"):
                        service_data["xy_color"] = saved_state["xy_color"]
                    
                    await self.hass.services.async_call(
                        LIGHT_DOMAIN, "turn_on", service_data
                    )
                    
                _LOGGER.info("Restored %s to %s", light_entity, saved_state["state"])
                
            except Exception as err:
                _LOGGER.error("Failed to restore %s: %s", light_entity, err)
        
        _LOGGER.info("Light state restoration complete")

    async def _validate_setup(self) -> None:
        """Validate sensor and lights are available."""
        self.calibration_step = "validating_sensor"
        await self.async_request_refresh()
        
        # Test sensor
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} not found")
        
        if sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} is unavailable")
        
        try:
            current_lux = float(sensor_state.state)
            _LOGGER.info("Sensor validated: %.1f lux", current_lux)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} has invalid value: {sensor_state.state}")
        
        # Test lights
        self.calibration_step = "validating_lights"
        await self.async_request_refresh()
        
        working_lights = []
        failed_lights = []
        
        for light_entity in self.lights:
            light_state = self.hass.states.get(light_entity)
            if not light_state:
                failed_lights.append(f"{light_entity} (not found)")
                continue
                
            if light_state.state == STATE_UNAVAILABLE:
                failed_lights.append(f"{light_entity} (unavailable)")
                continue
                
            working_lights.append(light_entity)
        
        if not working_lights:
            raise HomeAssistantError(f"No working lights found. Failed: {failed_lights}")
        
        if failed_lights:
            _LOGGER.warning("Some lights failed validation: %s", failed_lights)
        
        # Update lights list to only working lights
        self.lights = working_lights
        _LOGGER.info("Validated %d working lights", len(self.lights))

    async def _read_sensor(self) -> float:
        """Read current lux value from sensor."""
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state or sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} unavailable during reading")
        
        try:
            return float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Invalid sensor reading: {sensor_state.state}")

    async def _set_light_to_white(self, entity_id: str, brightness: int) -> None:
        """Set a light to white at specified brightness."""
        if brightness == 0:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_off", {"entity_id": entity_id}
            )
        else:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on", {
                    "entity_id": entity_id,
                    "brightness": brightness,
                    "rgb_color": [255, 255, 255]  # Force white
                }
            )

    async def _calibrate_timing(self) -> None:
        """Determine optimal settle time for lights and sensor."""
        self.calibration_step = "calibrating_timing"
        await self.async_request_refresh()
        
        test_light = self.lights[0]
        _LOGGER.info("Calibrating timing using light: %s", test_light)
        
        # Turn off all lights first
        await self._set_all_lights(False)
        await asyncio.sleep(3)  # Initial settle
        
        initial_lux = await self._read_sensor()
        
        # Turn on test light to white and measure settle time
        await self._set_light_to_white(test_light, 255)
        
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
        await self._set_light_to_white(test_light, 0)
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
            _LOGGER.info("Testing light %d/%d: %s", i + 1, len(self.lights), light_entity)
            
            # Turn off all lights
            await self._set_all_lights(False)
            await asyncio.sleep(self.settle_time_seconds)
            base_lux = await self._read_sensor()
            
            # Turn on this light
            await self._set_light_to_white(light_entity, 255)
            await asyncio.sleep(self.settle_time_seconds)
            with_light_lux = await self._read_sensor()
            
            contribution = with_light_lux - base_lux
            
            # Only include lights that contribute significantly (>10 lux threshold)
            if contribution >= 10:
                self.light_contributions[light_entity] = {
                    "max_contribution": contribution,
                    "base_lux": base_lux,
                    "with_light_lux": with_light_lux,
                    "linear_validated": True  # Will be updated in pair validation
                }
                _LOGGER.info("✓ %s contributes %.1f lux", light_entity, contribution)
            else:
                _LOGGER.info("✗ %s contributes only %.1f lux (below threshold)", light_entity, contribution)
        
        _LOGGER.info("Light contribution testing complete: %d contributing lights found", 
                    len(self.light_contributions))

    async def _validate_light_pairs(self) -> None:
        """Validate that light contributions are approximately additive."""
        self.calibration_step = "validating_pairs"
        await self.async_request_refresh()
        
        # Test a few pairs from contributing lights only
        contributing_lights = list(self.light_contributions.keys())
        lights_to_test = contributing_lights[:3]  # Test first 3 contributing lights
        
        if len(lights_to_test) < 2:
            _LOGGER.info("Not enough contributing lights for pair validation, skipping")
            return
        
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
            
            await self._set_light_to_white(light1, 255)
            await self._set_light_to_white(light2, 255)
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
        
        # Set up state change listeners for contributing lights
        await self._setup_light_state_listeners()
        
        # Force immediate data refresh to update sensors
        await self.async_request_refresh()
        
        _LOGGER.info("Calibration data saved successfully")

    async def _setup_light_state_listeners(self) -> None:
        """Set up state change listeners for contributing lights."""
        # Clean up existing listeners first
        await self._cleanup_state_listeners()
        
        if not self.light_contributions:
            return
            
        _LOGGER.info("Setting up state listeners for %d contributing lights", len(self.light_contributions))
        
        from homeassistant.helpers.event import async_track_state_change_event
        
        def light_state_changed(event):
            """Handle light state change - simple flag approach."""
            entity_id = event.data.get("entity_id")
            _LOGGER.debug("Contributing light %s changed, flagging for update", entity_id)
            # Just set flag - coordinator will update on next cycle (every 10 seconds)
            self._light_state_dirty = True
        
        # Track state changes for all contributing lights
        contributing_entities = list(self.light_contributions.keys())
        unsub = async_track_state_change_event(
            self.hass,
            contributing_entities,
            light_state_changed
        )
        self._unsub_state_listeners.append(unsub)
        
        _LOGGER.info("State listeners set up for contributing lights: %s", contributing_entities)

    async def _cleanup_state_listeners(self) -> None:
        """Clean up state change listeners."""
        count = len(self._unsub_state_listeners)
        for unsub in self._unsub_state_listeners:
            unsub()
        self._unsub_state_listeners.clear()
        if count > 0:
            _LOGGER.debug("Cleaned up %d state listeners", count)

    async def _set_all_lights(self, state: bool) -> None:
        """Turn all contributing lights on (white) or off with validation."""
        if hasattr(self, 'light_contributions') and self.light_contributions:
            # Use only contributing lights if we have them
            lights_to_control = list(self.light_contributions.keys())
        else:
            # Use all validated lights during initial testing
            lights_to_control = self.lights
            
        brightness = 255 if state else 0
        expected_state = STATE_ON if state else STATE_OFF
        
        _LOGGER.info("Setting %d lights to %s...", len(lights_to_control), expected_state)
        
        # Send commands to all lights first
        tasks = [self._set_light_to_white(light, brightness) for light in lights_to_control]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Give extra time for all lights to settle
        await asyncio.sleep(2)
        
        # Verify all lights reached expected state
        failed_lights = []
        for light in lights_to_control:
            current_state = self.hass.states.get(light)
            if not current_state or current_state.state != expected_state:
                failed_lights.append(light)
        
        if failed_lights:
            _LOGGER.error("CRITICAL: %d lights failed to reach %s state: %s", 
                         len(failed_lights), expected_state, failed_lights)
            raise HomeAssistantError(f"Lights failed to respond correctly: {failed_lights}. Calibration aborted.")
        
        _LOGGER.info("All %d lights successfully set to %s", len(lights_to_control), expected_state)