"""Adaptive ELL sensor platform."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AdaptiveELLCoordinator


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Adaptive ELL sensor platform."""
    coordinator = hass.data[DOMAIN]
    
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
        self._attr_name = f"Adaptive ELL {coordinator.room_name.title()}"
        self._attr_unique_id = f"adaptive_ell_{coordinator.room_name}"
        self._attr_device_class = SensorDeviceClass.ILLUMINANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = LIGHT_LUX
        self._attr_icon = "mdi:lightbulb-on"

    @property
    def native_value(self) -> float | None:
        """Return the estimated light level."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("estimated_lux")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}
        
        data = self.coordinator.data
        attributes = {
            "room_name": self.coordinator.room_name,
            "sensor_entity": self.coordinator.sensor_entity,
            "current_sensor_lux": data.get("current_lux"),
            "min_lux": data.get("min_lux"),
            "max_lux": data.get("max_lux"),
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
    """Calibration status sensor."""

    def __init__(self, coordinator: AdaptiveELLCoordinator) -> None:
        """Initialize the calibration sensor."""
        super().__init__(coordinator)
        self._attr_name = f"Adaptive ELL {coordinator.room_name.title()} Calibration"
        self._attr_unique_id = f"adaptive_ell_{coordinator.room_name}_calibration"
        self._attr_icon = "mdi:tune"

    @property
    def native_value(self) -> str:
        """Return the calibration status."""
        if not self.coordinator.data:
            return "unknown"
        
        if self.coordinator.data.get("calibrating"):
            return self.coordinator.data.get("calibration_step", "calibrating")
        
        if self.coordinator.light_contributions:
            return "calibrated"
        
        return "not_calibrated"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return calibration details."""
        if not self.coordinator.data:
            return {}
        
        data = self.coordinator.data
        attributes = {
            "is_calibrating": data.get("calibrating", False),
            "calibration_step": data.get("calibration_step", "idle"),
            "settle_time_seconds": self.coordinator.settle_time_seconds,
            "timing_buffer": self.coordinator.timing_buffer,
            "lights_to_test": self.coordinator.lights,
        }
        
        # Add validation results if available
        if hasattr(self.coordinator, 'validation_results') and self.coordinator.validation_results:
            validation = self.coordinator.validation_results
            attributes.update({
                "pair_tested": validation.get("pair_tested"),
                "pair_validation_passed": validation.get("passed"),
                "pair_validation_error": f"{validation.get('error_percentage', 0):.1%}",
            })
        
        return attributes