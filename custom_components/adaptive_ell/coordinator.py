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
        self.initial_light_states = {}  # Store original light states
        
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
        
        # Restore lights to original state before stopping
        await self._restore_initial_light_states()
        
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

        _LOGGER.info("Reading configuration from options")

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
        
        _LOGGER.info("Test area: %s", test_area.name)
        
        # Get sensor
        sensor_entity = options.get("sensor_entity")
        if not sensor_entity:
            raise HomeAssistantError("No sensor configured")
        
        _LOGGER.info("Sensor: %s", sensor_entity)
        
        # Get adjacent areas
        adjacent_areas = []
        adjacent_names = []
        for direction in ["north_area", "south_area", "east_area", "west_area"]:
            area_id = options.get(direction)
            if area_id and area_id != "none":
                area = area_reg.areas.get(area_id)
                if area:
                    adjacent_areas.append(area)
                    adjacent_names.append(area.name)
        
        # Get all lights from test area and adjacent areas
        all_areas = [test_area] + adjacent_areas
        _LOGGER.info("Searching %d areas: %s", len(all_areas), [a.name for a in all_areas])
        
        lights = await self._get_lights_from_areas(all_areas)
        
        return {
            "test_area": test_area.name,
            "sensor_entity": sensor_entity,
            "lights": lights,
            "adjacent_areas": adjacent_names,
            "area_count": len(all_areas)
        }

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
        
        _LOGGER.error("=== CALIBRATION STARTING ===")
        _LOGGER.error("Room: %s | Sensor: %s | Found %d lights in %d areas", 
                     self.room_name, self.sensor_entity, len(self.lights), config["area_count"])
        
        if not self.lights:
            raise HomeAssistantError(f"No lights found in {config['area_count']} selected areas. Check that areas have light entities and they are not disabled.")
        
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
            
            # Send completion notification
            contributing_lights = len(self.light_contributions)
            total_lights_tested = len(self.lights)
            total_contribution = sum(
                contrib.get("max_contribution", 0) 
                for contrib in self.light_contributions.values()
            )
            
            _LOGGER.error("✓ SUCCESS: %d useful lights found (of %d tested) | Total: %.0f lux | Range: %.0f-%.0f lux",
                         contributing_lights, total_lights_tested, total_contribution, self.min_lux, self.max_lux)
            
            await self._send_notification(
                "Calibration Complete!",
                f"{self.room_name.title()} calibration finished successfully. "
                f"Found {contributing_lights} useful lights out of {total_lights_tested} tested "
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
            # Always restore lights to original state
            await self._restore_initial_light_states()
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
            _LOGGER.error("✓ Sensor validation passed: %s reading %.1f lux", self.sensor_entity, current_lux)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} does not report numeric illuminance value")
        
        self.calibration_step = "validating_lights"
        await self.async_request_refresh()
        
        # Test which lights are actually controllable
        valid_lights = []
        excluded_lights = []
        
        _LOGGER.error("Testing controllability of %d lights...", len(self.lights))
        
        for light_entity in self.lights:
            light_state = self.hass.states.get(light_entity)
            if not light_state:
                excluded_lights.append(f"{light_entity} (not found)")
                continue
            if light_state.domain != "light":
                excluded_lights.append(f"{light_entity} (not a light)")
                continue
                
            # Test if light is controllable
            if await self._is_light_controllable(light_entity):
                valid_lights.append(light_entity)
            else:
                excluded_lights.append(f"{light_entity} (not controllable)")
        
        if excluded_lights:
            _LOGGER.error("✗ Excluded %d lights: %s", len(excluded_lights), excluded_lights)
        
        if not valid_lights:
            raise HomeAssistantError("No controllable lights found for testing")
        
        self.lights = valid_lights
        _LOGGER.error("✓ Found %d controllable lights for calibration", len(self.lights))

    async def _is_light_controllable(self, entity_id: str) -> bool:
        """Test if a light can be controlled (turned on/off)."""
        try:
            # Get initial state
            initial_state = self.hass.states.get(entity_id)
            if not initial_state:
                return False
            
            was_on = initial_state.state == STATE_ON
            
            # Try to turn it off if it's on, or on if it's off
            if was_on:
                await self.hass.services.async_call(
                    "light", "turn_off",
                    {"entity_id": entity_id},
                    blocking=True
                )
                await asyncio.sleep(1)
                
                # Check if it actually turned off
                new_state = self.hass.states.get(entity_id)
                turned_off = new_state and new_state.state == STATE_OFF
                
                # Restore original state
                if turned_off:
                    await self.hass.services.async_call(
                        "light", "turn_on",
                        {"entity_id": entity_id},
                        blocking=True
                    )
                
                return turned_off
            else:
                await self.hass.services.async_call(
                    "light", "turn_on",
                    {"entity_id": entity_id, "brightness": 128},
                    blocking=True
                )
                await asyncio.sleep(1)
                
                # Check if it actually turned on
                new_state = self.hass.states.get(entity_id)
                turned_on = new_state and new_state.state == STATE_ON
                
                # Turn it back off
                if turned_on:
                    await self.hass.services.async_call(
                        "light", "turn_off",
                        {"entity_id": entity_id},
                        blocking=True
                    )
                
                return turned_on
                
        except Exception as err:
            _LOGGER.warning("Error testing light %s controllability: %s", entity_id, err)
            return False

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
        MIN_CONTRIBUTION_LUX = 10  # Minimum lux contribution to be considered useful
        
        _LOGGER.error("=== TESTING INDIVIDUAL LIGHTS ===")
        _LOGGER.error("Testing %d lights for minimum %d lux contribution...", len(self.lights), MIN_CONTRIBUTION_LUX)
        
        for i, light_entity in enumerate(self.lights):
            _LOGGER.info("Testing light %d/%d: %s", i+1, len(self.lights), light_entity)
            
            # Turn off all lights
            await self._set_all_lights(False)
            await asyncio.sleep(self.settle_time_seconds)
            base_lux = await self._read_sensor()
            
            # Turn on this light with white color and full brightness
            await self._set_light_to_white(light_entity, 255)
            await asyncio.sleep(self.settle_time_seconds)
            with_light_lux = await self._read_sensor()
            
            contribution = with_light_lux - base_lux
            
            # Only keep lights with meaningful contribution
            if contribution >= MIN_CONTRIBUTION_LUX:
                self.light_contributions[light_entity] = {
                    "max_contribution": max(0, contribution),
                    "base_reading": base_lux,
                    "with_light_reading": with_light_lux,
                    "linear_validated": True  # Will validate in next step
                }
                _LOGGER.info("✓ %s: %.1f lux (INCLUDED)", light_entity, contribution)
            else:
                _LOGGER.info("✗ %s: %.1f lux (EXCLUDED - below threshold)", light_entity, contribution)
        
        if not self.light_contributions:
            raise HomeAssistantError("No lights found with sufficient contribution (>10 lux)")
        
        _LOGGER.error("✓ Found %d contributing lights out of %d tested", 
                     len(self.light_contributions), len(self.lights))

    async def _set_light_to_white(self, entity_id: str, brightness: int) -> None:
        """Set light to white color at specified brightness with validation."""
        try:
            if brightness > 0:
                # Get light state to check supported features
                light_state = self.hass.states.get(entity_id)
                if not light_state:
                    _LOGGER.warning("Light %s not found when trying to set to white", entity_id)
                    return
                
                supported_color_modes = light_state.attributes.get("supported_color_modes", [])
                
                # Build service call parameters - start with brightness only
                service_data = {
                    "entity_id": entity_id,
                    "brightness": brightness
                }
                
                # Add ONE color parameter based on priority: color_temp > rgb > hs
                if "color_temp" in supported_color_modes:
                    # Use new kelvin format instead of deprecated mireds
                    service_data["color_temp_kelvin"] = 4000  # Neutral white
                elif "rgb" in supported_color_modes:
                    service_data["rgb_color"] = [255, 255, 255]
                elif "hs" in supported_color_modes:
                    service_data["hs_color"] = [0, 0]  # Hue=0, Saturation=0 = white
                
                await self.hass.services.async_call(
                    "light", "turn_on",
                    service_data,
                    blocking=True
                )
                
                # Validate the command worked
                await self._validate_light_state_change(entity_id, expected_state=STATE_ON, retry_count=2)
                
            else:
                await self.hass.services.async_call(
                    "light", "turn_off",
                    {"entity_id": entity_id},
                    blocking=True
                )
                
                # Validate the light actually turned off
                await self._validate_light_state_change(entity_id, expected_state=STATE_OFF, retry_count=2)
                
        except Exception as err:
            _LOGGER.warning("Failed to set light %s to white: %s, trying basic control", entity_id, err)
            
            # Fallback to basic brightness control
            try:
                if brightness > 0:
                    await self.hass.services.async_call(
                        "light", "turn_on",
                        {"entity_id": entity_id, "brightness": brightness},
                        blocking=True
                    )
                    await self._validate_light_state_change(entity_id, expected_state=STATE_ON, retry_count=2)
                else:
                    await self.hass.services.async_call(
                        "light", "turn_off",
                        {"entity_id": entity_id},
                        blocking=True
                    )
                    await self._validate_light_state_change(entity_id, expected_state=STATE_OFF, retry_count=2)
            except Exception as err2:
                _LOGGER.error("Failed fallback control for light %s: %s", entity_id, err2)

    async def _validate_light_state_change(self, entity_id: str, expected_state: str, retry_count: int = 2) -> bool:
        """Validate that a light actually changed to the expected state."""
        for attempt in range(retry_count + 1):
            # Wait a bit for the change to take effect
            await asyncio.sleep(1.0 + (attempt * 0.5))  # Increasing delay with retries
            
            current_state = self.hass.states.get(entity_id)
            if current_state and current_state.state == expected_state:
                if attempt > 0:
                    _LOGGER.info("Light %s reached %s state after %d retries", entity_id, expected_state, attempt)
                return True
            
            if attempt < retry_count:
                _LOGGER.warning("Light %s failed to reach %s state (attempt %d/%d), retrying...", 
                               entity_id, expected_state, attempt + 1, retry_count + 1)
                
                # Retry the command
                if expected_state == STATE_ON:
                    await self.hass.services.async_call(
                        "light", "turn_on",
                        {"entity_id": entity_id, "brightness": 255},
                        blocking=True
                    )
                else:
                    await self.hass.services.async_call(
                        "light", "turn_off",
                        {"entity_id": entity_id},
                        blocking=True
                    )
        
        # If we get here, validation failed
        _LOGGER.error("CRITICAL: Light %s failed to reach %s state after %d attempts - this will invalidate calibration!", 
                     entity_id, expected_state, retry_count + 1)
        
        current_state = self.hass.states.get(entity_id)
        actual_state = current_state.state if current_state else "unknown"
        raise HomeAssistantError(f"Light {entity_id} failed to respond - expected {expected_state}, got {actual_state}. Calibration aborted.")

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
        
        _LOGGER.info("Calibration data saved successfully")

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
        if not self.initial_light_states:
            return
            
        _LOGGER.error("🔄 Restoring lights to original states...")
        
        for light_entity, original_state in self.initial_light_states.items():
            try:
                if original_state["state"] == STATE_OFF:
                    # Simply turn off the light
                    await self.hass.services.async_call(
                        "light", "turn_off",
                        {"entity_id": light_entity},
                        blocking=True
                    )
                    _LOGGER.info("Restored %s: OFF", light_entity)
                    
                elif original_state["state"] == STATE_ON:
                    # Build service call to restore original on state
                    service_data = {"entity_id": light_entity}
                    
                    # Add brightness if it was set
                    if original_state["brightness"] is not None:
                        service_data["brightness"] = original_state["brightness"]
                    
                    # Add color information - prioritize the most specific format available
                    if original_state["color_temp_kelvin"] is not None:
                        service_data["color_temp_kelvin"] = original_state["color_temp_kelvin"]
                    elif original_state["color_temp"] is not None:
                        service_data["color_temp_kelvin"] = round(1000000 / original_state["color_temp"])  # Convert mireds to kelvin
                    elif original_state["rgb_color"] is not None:
                        service_data["rgb_color"] = original_state["rgb_color"]
                    elif original_state["hs_color"] is not None:
                        service_data["hs_color"] = original_state["hs_color"]
                    elif original_state["xy_color"] is not None:
                        service_data["xy_color"] = original_state["xy_color"]
                    
                    await self.hass.services.async_call(
                        "light", "turn_on",
                        service_data,
                        blocking=True
                    )
                    _LOGGER.info("Restored %s: ON with original settings", light_entity)
                    
            except Exception as err:
                _LOGGER.warning("Failed to restore %s: %s", light_entity, err)
                
        # Clear the stored states
        self.initial_light_states = {}
        _LOGGER.error("✅ Light state restoration complete")

    async def _read_sensor(self) -> float:
        """Read current sensor value."""
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state or sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} unavailable")
        
        try:
            return float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} returned non-numeric value: {sensor_state.state}")