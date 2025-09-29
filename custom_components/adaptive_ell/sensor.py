"""Adaptive ELL sensor platform."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AdaptiveELLCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Adaptive ELL sensor platform."""
    # Get the coordinator for this specific config entry
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    sensors = [
        AdaptiveELLSensor(coordinator),
        AdaptiveELLCalibrationSensor(coordinator),
    ]
    
    async_add_entities(sensors, True)


class AdaptiveELLSensor(CoordinatorEntity, SensorEntity):
    """Estimated Light Level sensor."""

    def __init__(self, coordinator: AdaptiveELLCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        
        # Safe naming with None handling
        room_name = coordinator.room_name or "Unconfigured"
        area_slug = room_name.lower().replace(" ", "_")
        
        self._attr_name = f"Adaptive ELL {room_name.title()}"
        self._attr_unique_id = f"adaptive_ell_{area_slug}"
        self._attr_device_class = SensorDeviceClass.ILLUMINANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = LIGHT_LUX
        self._attr_icon = "mdi:lightbulb-on"
        
        _LOGGER.info("Created ELL sensor: %s (ID: %s)", self._attr_name, self._attr_unique_id)

    @property
    def native_value(self) -> float | None:
        """Return the estimated light level."""
        if not self.coordinator.data:
            _LOGGER.debug("No coordinator data for %s", self._attr_name)
            return None
            
        estimated = self.coordinator.data.get("estimated_lux")
        _LOGGER.debug("ELL sensor %s returning: %s", self._attr_name, estimated)
        return estimated

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}
        
        data = self.coordinator.data
        
        # Check if configured
        is_configured = bool(self.coordinator.sensor_entity)
        
        attributes = {
            "room_name": self.coordinator.room_name or "Not configured",
            "sensor_entity": self.coordinator.sensor_entity or "Not configured",
            "is_configured": is_configured,
            "current_sensor_lux": data.get("current_lux", 0),
            "min_lux": data.get("min_lux", 0),
            "max_lux": data.get("max_lux", 0),
            "lights_configured": len(self.coordinator.lights),
            "calibration_status": "completed" if self.coordinator.light_contributions else "not_calibrated",
        }
        
        # Add light contributions if calibrated
        if self.coordinator.light_contributions:
            for light_entity, contrib in self.coordinator.light_contributions.items():
                light_name = light_entity.split(".")[-1]
                attributes[f"{light_name}_contribution"] = contrib.get("max_contribution", 0)
                attributes[f"{light_name}_linear"] = contrib.get("linear_validated", False)
        
        return attributes


class AdaptiveELLCalibrationSensor(CoordinatorEntity, SensorEntity):
    """Calibration status sensor with progress indication."""

    def __init__(self, coordinator: AdaptiveELLCoordinator) -> None:
        """Initialize the calibration sensor."""
        super().__init__(coordinator)
        
        # Safe naming with None handling  
        room_name = coordinator.room_name or "Unconfigured"
        self._attr_name = f"Adaptive ELL {room_name.title()} Calibration"
        self._attr_unique_id = f"adaptive_ell_{room_name.lower().replace(' ', '_')}_calibration"
        self._attr_icon = "mdi:tune"

    @property
    def native_value(self) -> str:
        """Return the calibration status with progress indication."""
        if not self.coordinator.data:
            return "Unknown"
        
        # Check if integration is configured
        if not self.coordinator.sensor_entity:
            return "⚙️ Not Configured - Use Integration Configure"
        
        if self.coordinator.data.get("calibrating"):
            step = self.coordinator.data.get("calibration_step", "calibrating")
            # Make step names user-friendly with progress indication
            step_names = {
                "validation": "🔍 Validating Setup...",
                "validating_sensor": "📊 Checking Sensor...",
                "validating_lights": "💡 Checking Lights...", 
                "calibrating_timing": "⏱️ Testing Timing...",
                "testing_min_max": "📈 Testing Min/Max Values...",
                "testing_contributions": "🧪 Testing Light Contributions...",
                "validating_pairs": "✅ Validating Results...",
                "saving_data": "💾 Saving Calibration Data...",
                "completed": "✅ Calibration Complete!",
                "stopped": "ℹ️ Calibration Stopped"
            }
            friendly_name = step_names.get(step, f"🔄 {step}...")
            
            # Add failure indication
            if step.startswith("failed:"):
                return f"❌ Failed: {step[7:]}"
            
            return friendly_name
        
        if self.coordinator.light_contributions:
            contrib_count = len(self.coordinator.light_contributions)
            return f"✅ Calibrated ({contrib_count} lights)"
        
        return "❓ Ready for Calibration"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return calibration details with progress information."""
        if not self.coordinator.data:
            return {}
        
        data = self.coordinator.data
        
        # Check if configured
        is_configured = bool(self.coordinator.sensor_entity)
        
        attributes = {
            "is_calibrating": data.get("calibrating", False),
            "calibration_step": data.get("calibration_step", "idle"),
            "is_configured": is_configured,
            "settle_time_seconds": self.coordinator.settle_time_seconds,
            "timing_buffer": self.coordinator.timing_buffer,
            "lights_to_test": self.coordinator.lights,
            "configuration_source": "config_flow" if is_configured else "none"
        }
        
        # Add progress percentage if calibrating
        if data.get("calibrating"):
            step = data.get("calibration_step", "idle")
            step_progress = {
                "validation": 10,
                "validating_sensor": 15,
                "validating_lights": 20,
                "calibrating_timing": 30,
                "testing_min_max": 40,
                "testing_contributions": 80,
                "validating_pairs": 90,
                "saving_data": 95,
                "completed": 100,
            }
            attributes["progress_percent"] = step_progress.get(step, 0)
        
        # Add validation results if available
        if hasattr(self.coordinator, 'validation_results') and self.coordinator.validation_results:
            validation = self.coordinator.validation_results
            attributes.update({
                "pair_tested": validation.get("pair_tested"),
                "pair_validation_passed": validation.get("passed"),
                "pair_validation_error": f"{validation.get('error_percentage', 0):.1%}",
            })
        
        # Add calibration results summary
        if self.coordinator.light_contributions:
            attributes["contributing_lights"] = len(self.coordinator.light_contributions)
            attributes["total_light_contribution"] = sum(
                contrib.get("max_contribution", 0) 
                for contrib in self.coordinator.light_contributions.values()
            )
        
        # Add configuration instructions if not configured
        if not is_configured:
            attributes["configuration_instructions"] = "Go to Settings > Devices & Services > Adaptive ELL > Configure"
        
        return attributes