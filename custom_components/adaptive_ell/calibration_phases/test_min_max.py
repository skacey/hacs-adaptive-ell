"""
QUALITY LEVEL: Alpha
STATUS: FUNCTIONAL (needs validation)

KNOWN ISSUES:
- No validation that lights actually reached expected state
- Assumes all lights respond within settle_time
- No handling of lights that fail to turn on/off
- settle_time may be too short for some light types
- No retry logic for failed state changes

Test minimum and maximum light levels.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


async def test_min_max_levels(
    hass: HomeAssistant,
    sensor_entity: str,
    light_entities: list[str],
    settle_time_seconds: int,
    set_lights_func,
    read_sensor_func
) -> Tuple[float, float]:
    """
    Test minimum (all lights off) and maximum (all lights on) lux levels.
    
    Args:
        hass: Home Assistant instance
        sensor_entity: Lux sensor entity ID
        light_entities: List of light entity IDs to test
        settle_time_seconds: Seconds to wait for lights to stabilize
        set_lights_func: Async function to set all lights (state: bool) -> None
        read_sensor_func: Async function to read sensor value () -> float
        
    Returns:
        Tuple of (min_lux, max_lux)
        
    Raises:
        HomeAssistantError: If min >= max or sensor unavailable
    """
    _LOGGER.info("Testing min/max light levels with %d lights", len(light_entities))
    
    # Test minimum (all lights off)
    _LOGGER.debug("Setting all lights OFF for minimum test")
    await set_lights_func(False)
    await asyncio.sleep(settle_time_seconds)
    min_lux = await read_sensor_func()
    _LOGGER.info("Minimum level (all OFF): %.1f lux", min_lux)
    
    # Test maximum (all lights on full)
    _LOGGER.debug("Setting all lights ON for maximum test")
    await set_lights_func(True)
    await asyncio.sleep(settle_time_seconds)
    max_lux = await read_sensor_func()
    _LOGGER.info("Maximum level (all ON): %.1f lux", max_lux)
    
    # Validation
    if max_lux <= min_lux:
        error_msg = f"Invalid min/max values: min={min_lux}, max={max_lux}. Max must be greater than min."
        _LOGGER.error(error_msg)
        raise HomeAssistantError(error_msg)
    
    lux_range = max_lux - min_lux
    _LOGGER.info("Valid min/max range: %.1f lux (%.1f to %.1f)", lux_range, min_lux, max_lux)
    
    return min_lux, max_lux
