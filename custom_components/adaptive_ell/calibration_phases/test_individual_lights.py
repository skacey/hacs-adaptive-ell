"""
QUALITY LEVEL: Alpha
STATUS: BROKEN (63% failure rate in production)

KNOWN ISSUES:
- 63% of lights fail testing without clear error messages
- No validation that individual light actually turns on before reading
- Assumes all lights respond within settle_time
- 10 lux threshold may be inappropriate for some light types
- No retry logic for lights that fail to respond
- No progressive timeout adjustment for slow-responding lights
- Fails silently when light doesn't reach expected state

Test individual light contributions to room illumination.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, Callable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Contribution threshold - lights below this are ignored
CONTRIBUTION_THRESHOLD_LUX = 10


async def test_individual_light_contributions(
    hass: HomeAssistant,
    light_entities: list[str],
    settle_time_seconds: int,
    set_lights_func: Callable,
    set_light_func: Callable,
    read_sensor_func: Callable
) -> Dict[str, Dict[str, Any]]:
    """
    Test each light individually to measure its contribution to room illumination.
    
    Args:
        hass: Home Assistant instance
        light_entities: List of light entity IDs to test
        settle_time_seconds: Seconds to wait for lights to stabilize
        set_lights_func: Async function to set all lights (state: bool) -> None
        set_light_func: Async function to set one light (entity_id: str, brightness: int) -> None
        read_sensor_func: Async function to read sensor value () -> float
        
    Returns:
        Dictionary mapping entity_id to contribution data:
        {
            "max_contribution": float,  # Lux contributed at full brightness
            "base_lux": float,          # Room lux with this light OFF
            "with_light_lux": float,    # Room lux with this light ON
            "linear_validated": bool    # Will be updated in pair validation
        }
    """
    _LOGGER.info("Testing individual contributions for %d lights", len(light_entities))
    
    light_contributions = {}
    
    for i, light_entity in enumerate(light_entities):
        _LOGGER.info("Testing light %d/%d: %s", i + 1, len(light_entities), light_entity)
        
        try:
            # Turn off all lights
            await set_lights_func(False)
            await asyncio.sleep(settle_time_seconds)
            base_lux = await read_sensor_func()
            _LOGGER.debug("%s: Base lux (all OFF) = %.1f", light_entity, base_lux)
            
            # Turn on this specific light
            await set_light_func(light_entity, 255)
            await asyncio.sleep(settle_time_seconds)
            with_light_lux = await read_sensor_func()
            _LOGGER.debug("%s: With light ON = %.1f", light_entity, with_light_lux)
            
            # Calculate contribution
            contribution = with_light_lux - base_lux
            
            # Only include lights that contribute significantly
            if contribution >= CONTRIBUTION_THRESHOLD_LUX:
                light_contributions[light_entity] = {
                    "max_contribution": contribution,
                    "base_lux": base_lux,
                    "with_light_lux": with_light_lux,
                    "linear_validated": True  # Will be updated in pair validation
                }
                _LOGGER.info("✓ %s contributes %.1f lux (PASSED)", light_entity, contribution)
            else:
                _LOGGER.info("✗ %s contributes only %.1f lux - below threshold (IGNORED)", 
                           light_entity, contribution)
                
        except Exception as err:
            _LOGGER.error("Failed to test %s: %s", light_entity, err)
            # Continue with next light
    
    contributing_count = len(light_contributions)
    ignored_count = len(light_entities) - contributing_count
    
    _LOGGER.info("Light contribution testing complete: %d contributing, %d below threshold", 
                contributing_count, ignored_count)
    
    return light_contributions
