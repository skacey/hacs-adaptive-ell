"""Adaptive ELL Coordinator for calibration and light management."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_ON, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Hardcoded test configuration for Office
OFFICE_TEST_CONFIG = {
    "room_name": "office",
    "sensor_entity": "sensor.third_reality_multi_function_night_light_illuminance",
    "lights": [
        "light.office_main_lights",
        "light.andon_office_light", 
        "light.front_hall_main_lights",
        "light.bar_main_lights",
        "light.kitchen_main_lights",
        "light.dining_room_table_lights"
    ]
}

class AdaptiveELLCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Adaptive ELL calibration and data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        
        # Calibration state
        self.is_calibrating = False
        self.calibration_step = "idle"
        self.settle_time_seconds = 0
        self.timing_buffer = 1.25
        
        # Room data
        self.room_name = OFFICE_TEST_CONFIG["room_name"]
        self.sensor_entity = OFFICE_TEST_CONFIG["sensor_entity"]
        self.lights = OFFICE_TEST_CONFIG["lights"]
        
        # Calibration results
        self.min_lux = 0
        self.max_lux = 0
        self.light_contributions = {}
        self.validation_results = {}
        
    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from Home Assistant."""
        try:
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
                total_contribution += max_contribution * brightness_pct
        
        # Add minimum ambient light
        return self.min_lux + total_contribution

    async def start_calibration(self) -> None:
        """Start the calibration process."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
            
        _LOGGER.info("Starting calibration for room: %s", self.room_name)
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
            
            # Step 5: Validate light pairs
            await self._validate_light_pairs()
            
            # Step 6: Save calibration data
            await self._save_calibration_data()
            
            self.calibration_step = "completed"
            _LOGGER.info("Calibration completed successfully")
            
        except Exception as err:
            _LOGGER.error("Calibration failed: %s", err)
            self.calibration_step = f"failed: {err}"
        finally:
            self.is_calibrating = False
            await self.async_request_refresh()

    async def _validate_setup(self) -> None:
        """Validate sensor and lights are available."""
        self.calibration_step = "validating_sensor"
        
        # Check sensor
        sensor_state = self.hass.states.get(self.sensor_entity)
        if not sensor_state:
            raise HomeAssistantError(f"Sensor {self.sensor_entity} not found")
        
        try:
            float(sensor_state.state)
        except (ValueError, TypeError):
            raise HomeAssistantError(f"Sensor {self.sensor_entity} does not report illuminance")
        
        self.calibration_step = "validating_lights"
        
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
        _LOGGER.info("Validated %d lights for testing", len(self.lights))

    async def _calibrate_timing(self) -> None:
        """Determine optimal timing by testing first light."""
        self.calibration_step = "calibrating_timing"
        
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
            expected_50_contribution = max_contribution * 0.5 + self.min_lux
            linearity_error = abs(light_50_reading - expected_50_contribution) / max_contribution
            
            self.light_contributions[light_entity] = {
                "max_contribution": max_contribution,
                "linear_validated": linearity_error < 0.05,  # Within 5%
                "linearity_error": linearity_error
            }
            
            _LOGGER.info(
                "Light %s: %.1f lux contribution, %.1f%% linearity error",
                light_entity, max_contribution, linearity_error * 100
            )

    async def _validate_light_pairs(self) -> None:
        """Validate that light contributions are additive."""
        self.calibration_step = "validating_pairs"
        
        if len(self.lights) < 2:
            _LOGGER.warning("Not enough lights for pair validation")
            return
        
        # Sort lights by contribution (highest and lowest)
        sorted_lights = sorted(
            self.lights,
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
            "passed": error_percentage < 0.05  # Within 5%
        }
        
        _LOGGER.info(
            "Pair validation: Expected %.1f, Actual %.1f, Error %.1f%%",
            expected_combined, combined_reading, error_percentage * 100
        )
        
        if not self.validation_results["passed"]:
            raise HomeAssistantError(f"Pair validation failed: {error_percentage:.1%} error")

    async def _save_calibration_data(self) -> None:
        """Save calibration data to text helper."""
        self.calibration_step = "saving_data"
        
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
        
        # For now, just log the data (helper creation will be in V0.2)
        _LOGGER.info("Calibration data for %s: %s", helper_entity, json.dumps(calibration_data, indent=2))

    async def _turn_off_all_lights(self) -> None:
        """Turn off all test lights."""
        await self.hass.services.async_call(
            LIGHT_DOMAIN, "turn_off", {"entity_id": self.lights}
        )

    async def _turn_on_all_lights(self) -> None:
        """Turn on all test lights at 100%."""
        await self.hass.services.async_call(
            LIGHT_DOMAIN, "turn_on", 
            {"entity_id": self.lights, "brightness_pct": 100}
        )

    async def _get_sensor_reading(self) -> float:
        """Get current sensor reading."""
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
        await self.async_request_refresh()