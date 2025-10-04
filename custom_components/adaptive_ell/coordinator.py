"""
QUALITY LEVEL: Alpha
STATUS: FUNCTIONAL (orchestration only - delegates to calibration phase modules)

KNOWN ISSUES:
- Error handling between phases may allow partial calibration data
- No rollback mechanism if calibration fails midway
- Phase failures may leave lights in unexpected states
- No user feedback during calibration progress
- Timing calibration phase not yet extracted to module

Coordinator for Adaptive ELL integration - orchestrates calibration phases.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import STATE_ON, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import area_registry, entity_registry, device_registry

from .const import DOMAIN
from .calibration_phases import restore_state
from .calibration_phases import test_min_max
from .calibration_phases import test_individual_lights
from .calibration_phases import validate_combinations
from .calibration_phases import save_calibration

_LOGGER = logging.getLogger(__name__)


class AdaptiveELLCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Adaptive ELL calibration and data."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=10),
        )
        
        self.config_entry = config_entry
        config_data = config_entry.options or config_entry.data
        test_area_id = config_data.get("test_area")
        
        if test_area_id:
            area_reg = area_registry.async_get(hass)
            area = area_reg.areas.get(test_area_id)
            self.room_name = area.name if area else "Unknown"
        else:
            self.room_name = "Unconfigured"
        
        self.sensor_entity = config_data.get("sensor_entity")
        self.lights = []
        self.excluded_lights = []
        
        # Load existing calibration data if available
        existing_calibration = config_data.get("calibration", {})
        
        self.min_lux = existing_calibration.get("min_lux", 0)
        self.max_lux = existing_calibration.get("max_lux", 0)
        self.light_contributions = existing_calibration.get("light_contributions", {})
        self.validation_results = existing_calibration.get("validation_results", {})
        self.settle_time_seconds = existing_calibration.get("settle_time_seconds", 0)
        
        if self.light_contributions:
            _LOGGER.info("Loaded existing calibration data for %s: %d contributing lights", 
                        self.room_name, len(self.light_contributions))
        
        # Calibration state
        self.is_calibrating = False
        self.calibration_step = "idle"
        self.timing_buffer = 1.25
        self.initial_light_states = {}
        
        # State change listeners
        self._unsub_state_listeners = []
        self._light_state_dirty = False
        
    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from Home Assistant."""
        try:
            data = {
                "calibrating": self.is_calibrating,
                "calibration_step": self.calibration_step,
                "min_lux": self.min_lux,
                "max_lux": self.max_lux,
                "lights_count": len(self.lights)
            }
            
            if self.sensor_entity:
                sensor_state = self.hass.states.get(self.sensor_entity)
                if sensor_state and sensor_state.state not in [STATE_UNAVAILABLE, STATE_UNKNOWN]:
                    try:
                        current_lux = float(sensor_state.state)
                        data["current_lux"] = current_lux
                        
                        if self.light_contributions:
                            estimated_lux = await self._calculate_current_estimated_lux()
                            data["estimated_lux"] = estimated_lux
                            
                            if self._light_state_dirty:
                                _LOGGER.debug("Light state changed, updated estimated lux: %.1f", estimated_lux)
                                self._light_state_dirty = False
                                
                    except (ValueError, TypeError):
                        pass
            
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
        
        for light_entity, contrib_data in self.light_contributions.items():
            light_state = self.hass.states.get(light_entity)
            if not light_state or light_state.state != STATE_ON:
                continue
                
            brightness = light_state.attributes.get("brightness", 255)
            brightness_percent = brightness / 255.0
            
            max_contribution = contrib_data.get("max_contribution", 0)
            current_contribution = max_contribution * brightness_percent
            total_estimated += current_contribution
        
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
        config_data = self.config_entry.options or self.config_entry.data
        
        test_area_id = config_data.get("test_area")
        additional_area_ids = config_data.get("selected_areas", [])
        area_ids = [test_area_id] + additional_area_ids
        
        _LOGGER.info("Loading config: test_area=%s, additional_areas=%s", test_area_id, additional_area_ids)
        
        area_reg = area_registry.async_get(self.hass)
        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)
        
        lights = []
        for entity in ent_reg.entities.values():
            if not entity.entity_id.startswith("light."):
                continue
            if entity.disabled:
                continue
            
            entity_area_id = None
            if entity.device_id:
                device = dev_reg.devices.get(entity.device_id)
                if device:
                    entity_area_id = device.area_id
            
            if entity_area_id in area_ids:
                if self.hass.states.get(entity.entity_id):
                    lights.append(entity.entity_id)
        
        _LOGGER.info("Found %d lights across %d areas", len(lights), len(area_ids))
        
        return {
            "sensor_entity": config_data.get("sensor_entity"),
            "lights": lights,
            "area_ids": area_ids
        }

    async def start_calibration_from_options(self) -> None:
        """Start calibration using configuration from config entry data."""
        if self.is_calibrating:
            raise HomeAssistantError("Calibration already in progress")
        
        config = await self._get_configuration_from_options()
        self.sensor_entity = config["sensor_entity"]
        self.lights = config["lights"]
        self.excluded_lights = []
        
        if not self.sensor_entity or not self.lights:
            raise HomeAssistantError("Configuration incomplete - missing sensor or lights")
        
        await self.start_calibration()

    def _estimate_calibration_time(self, light_count: int) -> int:
        """Estimate calibration time in minutes."""
        time_per_light = 30
        time_overhead = 60
        total_seconds = (light_count * time_per_light) + time_overhead
        return max(1, round(total_seconds / 60))

    async def start_calibration(self) -> None:
        """
        Start the calibration process by orchestrating calibration phases.
        
        Phases:
        1. Capture initial light states
        2. Validate setup (sensor and lights available)
        3. Calibrate timing
        4. Test min/max levels
        5. Test individual light contributions
        6. Validate light pair additivity
        7. Save calibration data
        8. Restore initial light states
        """
        _LOGGER.error("=== CALIBRATION STARTING ===")
        _LOGGER.error("Room: %s | Sensor: %s | Found %d lights in %d areas (selected mode)",
                     self.room_name, self.sensor_entity, len(self.lights),
                     len(self.config_entry.data.get("selected_areas", [])) + 1)
        _LOGGER.error("Estimated time: %d minutes", self._estimate_calibration_time(len(self.lights)))
        
        await self._send_notification(
            "Calibration Starting",
            f"This will take approximately {self._estimate_calibration_time(len(self.lights))} minutes. "
            f"Lights will turn on/off automatically."
        )
        
        self.is_calibrating = True
        self.calibration_step = "validation"
        
        try:
            # PHASE 1: Capture initial states
            self.calibration_step = "capturing_states"
            self.initial_light_states = await restore_state.capture_initial_states(
                self.hass,
                self.lights
            )
            
            # PHASE 2: Validate setup
            await self._validate_setup()
            
            # PHASE 3: Calibrate timing (TODO: Extract to module)
            await self._calibrate_timing()
            
            # PHASE 4: Test min/max levels
            self.calibration_step = "testing_min_max"
            await self.async_request_refresh()
            self.min_lux, self.max_lux = await test_min_max.test_min_max_levels(
                self.hass,
                self.sensor_entity,
                self.lights,
                self.settle_time_seconds,
                self._set_all_lights,
                self._read_sensor
            )
            
            # PHASE 5: Test individual light contributions
            self.calibration_step = "testing_contributions"
            await self.async_request_refresh()
            self.light_contributions = await test_individual_lights.test_individual_light_contributions(
                self.hass,
                self.lights,
                self.settle_time_seconds,
                self._set_all_lights,
                self._set_light_to_white,
                self._read_sensor
            )
            
            # PHASE 6: Validate light pairs
            self.calibration_step = "validating_pairs"
            await self.async_request_refresh()
            self.validation_results = await validate_combinations.validate_light_pair_additivity(
                self.hass,
                self.light_contributions,
                self.settle_time_seconds,
                self._set_all_lights,
                self._set_light_to_white,
                self._read_sensor
            )
            
            # PHASE 7: Save calibration data
            self.calibration_step = "saving_data"
            await self.async_request_refresh()
            save_success = await save_calibration.save_calibration_data(
                self.hass,
                self.config_entry,
                self.room_name,
                self.min_lux,
                self.max_lux,
                self.light_contributions,
                self.validation_results,
                self.settle_time_seconds,
                self.excluded_lights
            )
            
            if not save_success:
                _LOGGER.warning("Calibration data save reported failure, but continuing")
            
            # Setup state listeners
            await self._setup_light_state_listeners()
            
            self.calibration_step = "completed"
            _LOGGER.error("=== CALIBRATION COMPLETED ===")
            
            await self.async_request_refresh()
            
            # Log summary
            contributing_lights = len(self.light_contributions)
            total_lights_tested = len(self.lights) - len(self.excluded_lights)
            total_contribution = sum(
                contrib.get("max_contribution", 0) 
                for contrib in self.light_contributions.values()
            )
            
            _LOGGER.error("✓ SUCCESS: %d useful lights found (of %d tested) | Total: %.0f lux | Range: %.0f-%.0f lux",
                         contributing_lights, total_lights_tested, total_contribution, self.min_lux, self.max_lux)
            
            if self.excluded_lights:
                _LOGGER.error("⚠️ Excluded %d non-responsive lights: %s", 
                             len(self.excluded_lights), self.excluded_lights)
            
            if self.light_contributions:
                try:
                    current_estimated = await self._calculate_current_estimated_lux()
                    _LOGGER.error("✓ Current estimated light level: %.1f lux", current_estimated)
                except Exception as est_err:
                    _LOGGER.error("Failed to calculate current estimated lux: %s", est_err)
            
            notification_msg = (
                f"{self.room_name.title()} calibration finished successfully. "
                f"Found {contributing_lights} useful lights (of {total_lights_tested} tested). "
                f"Total contribution: {total_contribution:.0f} lux."
            )
            
            if self.excluded_lights:
                notification_msg += f"\n\n⚠️ Excluded {len(self.excluded_lights)} non-responsive lights."
            
            await self._send_notification("Calibration Complete!", notification_msg)
            
        except Exception as err:
            self.calibration_step = f"failed: {err}"
            _LOGGER.error("Calibration failed: %s", err)
            
            await self._send_notification(
                "Calibration Failed",
                f"Calibration of {self.room_name.title()} failed: {err}"
            )
            
            raise
            
        finally:
            # PHASE 8: Always attempt to restore initial states
            try:
                restoration_results = await restore_state.restore_initial_states(
                    self.hass,
                    self.initial_light_states
                )
                
                failed_restorations = [entity for entity, status in restoration_results.items() 
                                      if status == "failed"]
                if failed_restorations:
                    _LOGGER.warning("Failed to restore %d lights: %s", 
                                   len(failed_restorations), failed_restorations)
                    
            except Exception as restore_err:
                _LOGGER.error("Failed to restore light states: %s", restore_err)
            
            self.is_calibrating = False
            await self.async_request_refresh()

    async def stop_calibration(self) -> None:
        """Stop the calibration process."""
        if not self.is_calibrating:
            raise HomeAssistantError("No calibration in progress")
        
        _LOGGER.info("Stopping calibration")
        self.is_calibrating = False
        self.calibration_step = "stopped"
        
        try:
            await restore_state.restore_initial_states(self.hass, self.initial_light_states)
        except Exception as err:
            _LOGGER.error("Failed to restore light states: %s", err)
        
        await self._send_notification(
            "Calibration Stopped",
            f"Calibration of {self.room_name.title()} was stopped by user."
        )
        
        await self.async_request_refresh()

    # TODO: Extract these validation/setup methods to modules
    async def _validate_setup(self) -> None:
        """Validate sensor and lights are available."""
        self.calibration_step = "validating_sensor"
        await self.async_request_refresh()
        
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
        service_data = {
            "entity_id": entity_id,
            "brightness": brightness,
        }
        
        if brightness > 0:
            service_data["color_temp_kelvin"] = 4000
            await self.hass.services.async_call(LIGHT_DOMAIN, "turn_on", service_data)
        else:
            await self.hass.services.async_call(LIGHT_DOMAIN, "turn_off", {"entity_id": entity_id})

    # TODO: Extract this to calibration_phases/calibrate_timing.py
    async def _calibrate_timing(self) -> None:
        """Calibrate optimal timing for light state changes."""
        self.calibration_step = "calibrating_timing"
        await self.async_request_refresh()
        
        timings = []
        test_light = self.lights[0]
        
        for _ in range(3):
            start_lux = await self._read_sensor()
            
            await self._set_light_to_white(test_light, 255)
            
            for wait_time in [1, 2, 3, 4, 5]:
                await asyncio.sleep(1)
                current_lux = await self._read_sensor()
                if abs(current_lux - start_lux) > 10:
                    timings.append(wait_time)
                    _LOGGER.info("Light stabilized in %d seconds", wait_time)
                    break
            
            await self._set_light_to_white(test_light, 0)
            await asyncio.sleep(2)
        
        if timings:
            avg_timing = sum(timings) / len(timings)
            self.settle_time_seconds = max(2, int(avg_timing * self.timing_buffer))
        else:
            self.settle_time_seconds = 5
        
        _LOGGER.info("Using settle time: %d seconds", self.settle_time_seconds)

    async def _setup_light_state_listeners(self) -> None:
        """Set up state change listeners for contributing lights."""
        await self._cleanup_state_listeners()
        
        if not self.light_contributions:
            return
            
        _LOGGER.info("Setting up state listeners for %d contributing lights", len(self.light_contributions))
        
        from homeassistant.helpers.event import async_track_state_change_event
        
        def light_state_changed(event):
            """Handle light state change - simple flag approach."""
            entity_id = event.data.get("entity_id")
            _LOGGER.debug("Contributing light %s changed, flagging for update", entity_id)
            self._light_state_dirty = True
        
        contributing_entities = list(self.light_contributions.keys())
        unsub = async_track_state_change_event(
            self.hass,
            contributing_entities,
            light_state_changed
        )
        self._unsub_state_listeners.append(unsub)
        
        _LOGGER.info("State listeners set up for contributing lights")

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
            lights_to_control = list(self.light_contributions.keys())
        else:
            lights_to_control = self.lights
            
        brightness = 255 if state else 0
        expected_state = STATE_ON if state else STATE_OFF
        
        _LOGGER.info("Setting %d lights to %s...", len(lights_to_control), expected_state)
        
        tasks = [self._set_light_to_white(light, brightness) for light in lights_to_control]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        await asyncio.sleep(2)
        
        failed_lights = []
        for light in lights_to_control:
            current_state = self.hass.states.get(light)
            if not current_state or current_state.state != expected_state:
                failed_lights.append(light)
        
        if failed_lights:
            _LOGGER.warning("⚠️ Excluding %d non-responsive lights from calibration: %s", 
                           len(failed_lights), failed_lights)
            
            for light in failed_lights:
                if light not in self.excluded_lights:
                    self.excluded_lights.append(light)
                    
            self.lights = [light for light in self.lights if light not in failed_lights]
            
            if hasattr(self, 'light_contributions') and self.light_contributions:
                for light in failed_lights:
                    self.light_contributions.pop(light, None)
            
            if not self.lights:
                raise HomeAssistantError("All lights failed to respond. Cannot proceed with calibration.")
            
            _LOGGER.error("✓ Continuing calibration with %d working lights", len(self.lights))
        else:
            _LOGGER.info("All %d lights successfully set to %s", len(lights_to_control), expected_state)