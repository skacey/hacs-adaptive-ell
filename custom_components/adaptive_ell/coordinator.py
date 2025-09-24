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
            # Skip update if no sensor configured yet
            if not self.sensor_entity:
                return {
                    "current_lux": 0,
                    "estimated_lux": 0,
                    "calibrating": self.is_calibrating,
                    "calibration_step": "not_configured",
                    "min_lux": self.min_lux,
                    "max_lux": self.max_lux,
                    "light_contributions": self.light_contributions,
                }
            
            # Get current sensor reading
            sensor_state = self.hass.states.get(self.sensor_entity)
            if not sensor_state or sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
                raise UpdateFailed(f"Sensor {self.sensor_entity} unavailable")
            
            current_lux = float(sensor_state.state)
            
            # Calculate ELL if we have calibration data
            estimated_lux = await self._calculate_ell() if self.light_contributions else current_lux
            
            return {
                "current_lux": current_lux,
                "estimated_lux": estimated_lux,
                "calibrating": self.is_calibrating,
                "calibration_step": self.calibration_step,
                "min_lux": self.min_lux,
                "max_lux": self.max_lux,
                "light_contributions": self.light_contributions,
            }
            
        except ValueError as err:
            raise UpdateFailed(f"Error parsing sensor data: {err}")
        except Exception as err:
            raise UpdateFailed(f"Error communicating with HA: {err}")

    async def _calculate_ell(self) -> float:
        """Calculate estimated light level based on current light states."""
        total_contribution = 0
        
        for light_entity in self.lights:
            if light_entity not in self.light_contributions:
                continue
                
            light_state = self.hass.states.get(light_entity)
            if not light_state or light_state.state == STATE_OFF:
                continue
                
            if light_state.state == STATE_ON:
                brightness = light_state.attributes.get("brightness", 255)
                brightness_pct = brightness / 255.0
                max_contribution = self.light_contributions[light_entity]["max_contribution"]
                
                # Use square root curve for better accuracy
                sqrt_contribution = max_contribution * (brightness_pct ** 0.5)
                total_contribution += sqrt_contribution
        
        # Add minimum ambient light
        return self.min_lux + total_contribution

    async def _send_notification(self, title: str, message: str) -> None:
        """Send persistent notification to user."""
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
        
        if not options:
            raise HomeAssistantError("No configuration found - use integration options to configure")
        
        # Get test area information
        test_area_id = options.get("test_area")
        if not test_area_id:
            raise HomeAssistantError("No test area configured")
        
        area_reg = area_registry.async_get(self.hass)
        test_area = area_reg.areas.get(test_area_id)
        if not test_area:
            raise HomeAssistantError(f"Test area {test_area_id} not found")
        
        # Get sensor
        sensor_entity = options.get("sensor_entity")
        if not sensor_entity:
            raise HomeAssistantError("No sensor configured")
        
        # Get adjacent areas
        adjacent_areas = []
        for direction in ["north_area", "south_area", "east_area", "west_area"]:
            area_id = options.get(direction)
            if area_id and area_id != "none":
                area = area_reg.areas.get(area_id)
                if area:
                    adjacent_areas.append(area)
        
        # Get all lights from test area and adjacent areas
        all_areas = [test_area] + adjacent_areas
        lights = await self._get_lights_from_areas(all_areas)
        
        return {
            "test_area": test_area.name.lower(),
            "sensor_entity": sensor_entity,
            "lights": lights,
            "adjacent_areas": [area.name.lower() for area in adjacent_areas],
            "area_count": len(all_areas)
        }

    async def _get_lights_from_areas(self, areas: List) -> List[str]:
        """Get all light entities from specified areas."""
        ent_reg = entity_registry.async_get(self.hass)
        lights = []
        
        for area in areas:
            # Find lights in this area
            for entity in ent_reg.entities.values():
                if (entity.area_id == area.id and 
                    entity.entity_id.startswith("light.") and
                    not entity.disabled):
                    lights.append(entity.entity_id)
        
        return lights

    async def start_calibration_from_options(self) -> None:
        """Start calibration using configuration from integration options."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        # Read configuration from options
        try:
            config = await self._get_configuration_from_options()
        except Exception as err:
            raise HomeAssistantError(f"Failed to read configuration: {err}")
        
        # Update coordinator with configuration
        self.room_name = config["test_area"]
        self.sensor_entity = config["sensor_entity"] 
        self.lights = config["lights"]
        
        if not self.lights:
            raise HomeAssistantError(f"No lights found in {config['area_count']} selected areas")
        
        _LOGGER.info("Starting calibration from options: area=%s, sensor=%s, lights=%d, adjacent_areas=%s", 
                     self.room_name, self.sensor_entity, len(self.lights), config["adjacent_areas"])
        
        # Use existing calibration logic
        await self.start_calibration()

    async def start_calibration(self) -> None:
        """Start the calibration process."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        if not self.room_name or not self.sensor_entity or not self.lights:
            raise HomeAssistantError("Calibration configuration incomplete - configure integration options first")
            
        _LOGGER.info("Starting calibration for room: %s with sensor: %s", 
                     self.room_name, self.sensor_entity)
        
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
        
        try:
            float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} does not report illuminance")
        
        self.calibration_step = "validating_lights"
        await self.async_request_refresh()
        
        # Check lights
        valid_lights = []
        for light_entity in self.lights:
            light_state = self.hass.states.get(light_entity)
            if not light_state:
                _LOGGER.warning("Light %s not found, skipping", light_entity)
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
        
        if not self.lights:
            raise HomeAssistantError("No lights available for timing calibration")
        
        test_light = self.lights[0]
        _LOGGER.info("Calibrating timing with light: %s", test_light)
        
        # Turn off all lights
        await self._turn_off_all_lights()
        await asyncio.sleep(2)
        
        # Get baseline reading
        baseline = await self._get_sensor_reading()
        
        # Turn on test light
        await self.hass.services.async_call(
            LIGHT_DOMAIN, "turn_on", 
            {"entity_id": test_light, "brightness_pct": 100}
        )
        
        # Monitor until stable
        stable_reading = None
        last_reading = baseline
        stable_count = 0
        max_wait = 30  # Maximum 30 seconds
        
        for i in range(max_wait):
            await asyncio.sleep(1)
            current_reading = await self._get_sensor_reading()
            
            if abs(current_reading - last_reading) < 5:  # Within 5 lux
                stable_count += 1
                if stable_count >= 3:  # Stable for 3 seconds
                    stable_reading = current_reading
                    self.settle_time_seconds = (i + 1) * self.timing_buffer
                    break
            else:
                stable_count = 0
            
            last_reading = current_reading
        
        if stable_reading is None:
            raise HomeAssistantError("Could not determine stable timing")
        
        _LOGGER.info("Timing calibrated: %.1f seconds settle time", self.settle_time_seconds)

    async def _test_min_max_values(self) -> None:
        """Test minimum and maximum light values."""
        self.calibration_step = "testing_min_max"
        await self.async_request_refresh()
        
        # Test minimum (all lights off)
        await self._turn_off_all_lights()
        await asyncio.sleep(self.settle_time_seconds)
        self.min_lux = await self._get_sensor_reading()
        
        # Test maximum (all lights on)
        await self._turn_on_all_lights()
        await asyncio.sleep(self.settle_time_seconds)
        self.max_lux = await self._get_sensor_reading()
        
        if self.max_lux <= self.min_lux:
            raise HomeAssistantError("Max lux not greater than min lux")
        
        _LOGGER.info("Min lux: %.1f, Max lux: %.1f", self.min_lux, self.max_lux)

    async def _test_light_contributions(self) -> None:
        """Test individual light contributions."""
        self.calibration_step = "testing_contributions"
        await self.async_request_refresh()
        
        self.light_contributions = {}
        
        for light_entity in self.lights:
            _LOGGER.info("Testing contribution of: %s", light_entity)
            
            # Turn off all lights
            await self._turn_off_all_lights()
            await asyncio.sleep(self.settle_time_seconds)
            
            # Turn on this light at 100%
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on",
                {"entity_id": light_entity, "brightness_pct": 100}
            )
            await asyncio.sleep(self.settle_time_seconds)
            light_100_reading = await self._get_sensor_reading()
            
            # Test at 50% for linearity
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on",
                {"entity_id": light_entity, "brightness_pct": 50}
            )
            await asyncio.sleep(self.settle_time_seconds)
            light_50_reading = await self._get_sensor_reading()
            
            # Calculate contribution
            max_contribution = light_100_reading - self.min_lux
            
            # Skip lights with no measurable contribution
            if max_contribution <= 0:
                _LOGGER.warning(
                    "Light %s has no measurable contribution (%.1f lux), skipping",
                    light_entity, max_contribution
                )
                continue
            
            # Calculate linearity with better error handling
            expected_50_contribution = max_contribution * 0.5 + self.min_lux
            linearity_error = 0
            linear_validated = False
            
            # Test square root curve fit as well
            sqrt_expected_50 = max_contribution * (0.5 ** 0.5) + self.min_lux
            sqrt_error = 0
            
            # Avoid division by zero and provide detailed diagnostics
            if max_contribution > 1:  # Only test if contribution is significant
                linearity_error = abs(light_50_reading - expected_50_contribution) / max_contribution
                sqrt_error = abs(light_50_reading - sqrt_expected_50) / max_contribution
                linear_validated = linearity_error < 0.05  # Within 5%
                sqrt_validated = sqrt_error < 0.05
                
                _LOGGER.info(
                    "Light %s: Expected linear %.1f, sqrt %.1f, Actual %.1f - Linear error %.1f%%, Sqrt error %.1f%% (%s)",
                    light_entity, expected_50_contribution, sqrt_expected_50, light_50_reading,
                    linearity_error * 100, sqrt_error * 100, 
                    "LINEAR" if linear_validated else "SQRT" if sqrt_validated else "NON-LINEAR"
                )
            else:
                _LOGGER.warning(
                    "Light %s contribution too small for linearity testing (%.1f lux)",
                    light_entity, max_contribution
                )
            
            self.light_contributions[light_entity] = {
                "max_contribution": max_contribution,
                "linear_validated": linear_validated,
                "linearity_error": linearity_error,
                "sqrt_error": sqrt_error,
                "readings": {
                    "min_lux": self.min_lux,
                    "light_100_reading": light_100_reading,
                    "light_50_reading": light_50_reading,
                    "expected_50_linear": expected_50_contribution,
                    "expected_50_sqrt": sqrt_expected_50
                }
            }
            
            _LOGGER.info(
                "Light %s: %.1f lux contribution, %.1f%% linearity error, %.1f%% sqrt error",
                light_entity, max_contribution, linearity_error * 100, sqrt_error * 100
            )

    async def _validate_light_pairs(self) -> None:
        """Validate that light contributions are additive."""
        self.calibration_step = "validating_pairs"
        await self.async_request_refresh()
        
        if len(self.light_contributions) < 2:
            _LOGGER.warning("Not enough contributing lights for pair validation")
            return
        
        # Sort lights by contribution (highest and lowest)
        sorted_lights = sorted(
            self.light_contributions.keys(),
            key=lambda x: self.light_contributions[x]["max_contribution"],
            reverse=True
        )
        
        highest_light = sorted_lights[0]
        lowest_light = sorted_lights[-1]
        
        _LOGGER.info("Testing pair: %s + %s", highest_light, lowest_light)
        
        # Turn off all lights
        await self._turn_off_all_lights()
        await asyncio.sleep(self.settle_time_seconds)
        
        # Turn on both lights
        await self.hass.services.async_call(
            LIGHT_DOMAIN, "turn_on",
            {"entity_id": [highest_light, lowest_light], "brightness_pct": 100}
        )
        await asyncio.sleep(self.settle_time_seconds)
        combined_reading = await self._get_sensor_reading()
        
        # Calculate expected vs actual
        expected_combined = (
            self.min_lux + 
            self.light_contributions[highest_light]["max_contribution"] +
            self.light_contributions[lowest_light]["max_contribution"]
        )
        
        error_percentage = abs(combined_reading - expected_combined) / expected_combined
        
        self.validation_results = {
            "pair_tested": f"{highest_light} + {lowest_light}",
            "expected_lux": expected_combined,
            "actual_lux": combined_reading,
            "error_percentage": error_percentage,
            "passed": error_percentage < 0.10  # Within 10% (relaxed from 5%)
        }
        
        _LOGGER.info(
            "Pair validation: Expected %.1f, Actual %.1f, Error %.1f%% (%s)",
            expected_combined, combined_reading, error_percentage * 100,
            "PASS" if self.validation_results["passed"] else "FAIL"
        )
        
        if not self.validation_results["passed"]:
            _LOGGER.warning(f"Pair validation failed: {error_percentage:.1%} error, but continuing calibration")

    async def _save_calibration_data(self) -> None:
        """Save calibration data to logs and future text helper."""
        self.calibration_step = "saving_data"
        await self.async_request_refresh()
        
        # Create text helper entity name
        helper_entity = f"text.adaptive_ell_calibration_{self.room_name}"
        
        # Prepare calibration data
        calibration_data = {
            "room_name": self.room_name,
            "sensor_entity": self.sensor_entity,
            "calibrated_at": datetime.now().isoformat(),
            "timing_buffer": self.timing_buffer,
            "settle_time_seconds": self.settle_time_seconds,
            "min_lux": self.min_lux,
            "max_lux": self.max_lux,
            "lights": self.light_contributions,
            "pair_validation": self.validation_results
        }
        
        # For now, just log the data (helper creation will be in future version)
        _LOGGER.info("Calibration data for %s: %s", helper_entity, json.dumps(calibration_data, indent=2))

    async def _turn_off_all_lights(self) -> None:
        """Turn off all test lights."""
        if self.lights:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_off", {"entity_id": self.lights}
            )

    async def _turn_on_all_lights(self) -> None:
        """Turn on all test lights at 100%."""
        if self.lights:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on", 
                {"entity_id": self.lights, "brightness_pct": 100}
            )

    async def _get_sensor_reading(self) -> float:
        """Get current sensor reading."""
        if not self.sensor_entity:
            raise HomeAssistantError("No sensor configured")
            
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state or sensor_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} unavailable")
        
        try:
            return float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Invalid sensor reading: {sensor_state.state}")

    async def stop_calibration(self) -> None:
        """Stop calibration process."""
        if not self.is_calibrating:
            return
        
        self.is_calibrating = False
        self.calibration_step = "stopped"
        await self._turn_off_all_lights()
        
        # Send stop notification
        await self._send_notification(
            "Calibration Stopped",
            f"{self.room_name.title() if self.room_name else 'Calibration'} was stopped by user. Lights have been turned off."
        )
        
        await self.async_request_refresh()