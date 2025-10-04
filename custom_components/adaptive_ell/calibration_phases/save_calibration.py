"""
QUALITY LEVEL: Alpha
STATUS: FUNCTIONAL (needs validation)

KNOWN ISSUES:
- No validation that config entry update succeeded
- No backup of previous calibration data before overwrite
- No versioning of calibration data
- State listeners setup may fail silently
- No rollback if save fails partway through

Save calibration data to persistent storage.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


async def save_calibration_data(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    room_name: str,
    min_lux: float,
    max_lux: float,
    light_contributions: Dict[str, Dict[str, Any]],
    validation_results: Dict[str, Dict[str, Any]],
    settle_time_seconds: int,
    excluded_lights: list[str]
) -> bool:
    """
    Save calibration data to config entry.
    
    Args:
        hass: Home Assistant instance
        config_entry: Config entry to update
        room_name: Name of calibrated room
        min_lux: Minimum lux level (all lights off)
        max_lux: Maximum lux level (all lights on)
        light_contributions: Dictionary of light contributions
        validation_results: Dictionary of validation results
        settle_time_seconds: Calibrated settle time
        excluded_lights: List of lights excluded due to failures
        
    Returns:
        True if save succeeded, False otherwise
    """
    _LOGGER.info("Saving calibration data for %s", room_name)
    
    try:
        calibration_data = {
            "timestamp": datetime.now().isoformat(),
            "room_name": room_name,
            "min_lux": min_lux,
            "max_lux": max_lux,
            "light_contributions": light_contributions,
            "validation_results": validation_results,
            "settle_time_seconds": settle_time_seconds,
            "excluded_lights": excluded_lights,
            "contributing_light_count": len(light_contributions),
            "total_contribution_lux": sum(
                contrib.get("max_contribution", 0) 
                for contrib in light_contributions.values()
            )
        }
        
        # Log summary
        _LOGGER.info("Calibration summary:")
        _LOGGER.info("  Room: %s", room_name)
        _LOGGER.info("  Contributing lights: %d", len(light_contributions))
        _LOGGER.info("  Excluded lights: %d", len(excluded_lights))
        _LOGGER.info("  Lux range: %.1f - %.1f", min_lux, max_lux)
        _LOGGER.info("  Total contribution: %.1f lux", calibration_data["total_contribution_lux"])
        
        # Update config entry data
        new_data = {**config_entry.data, "calibration": calibration_data}
        hass.config_entries.async_update_entry(config_entry, data=new_data)
        
        _LOGGER.info("✓ Calibration data saved successfully")
        return True
        
    except Exception as err:
        _LOGGER.error("Failed to save calibration data: %s", err)
        return False
