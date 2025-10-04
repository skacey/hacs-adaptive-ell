"""
QUALITY LEVEL: Alpha
STATUS: FUNCTIONAL (needs validation)

KNOWN ISSUES:
- Only tests first 3 lights (arbitrary limit)
- 30% error tolerance is very high (may accept poor calibrations)
- No testing of non-linear light interactions
- No validation that lights actually turned on during pair test
- Does not test all possible combinations (only sequential pairs)
- Results not used to improve calibration accuracy

Validate that light contributions are approximately additive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, Callable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Error tolerance for pair validation
PAIR_VALIDATION_ERROR_TOLERANCE_PERCENT = 30


async def validate_light_pair_additivity(
    hass: HomeAssistant,
    light_contributions: Dict[str, Dict[str, Any]],
    settle_time_seconds: int,
    set_lights_func: Callable,
    set_light_func: Callable,
    read_sensor_func: Callable
) -> Dict[str, Dict[str, Any]]:
    """
    Validate that pairs of lights have approximately additive contributions.
    
    Tests pairs of contributing lights to ensure their combined contribution
    is close to the sum of their individual contributions. This validates
    the linear assumption used in light level calculations.
    
    Args:
        hass: Home Assistant instance
        light_contributions: Dictionary of light contributions from test_individual_lights
        settle_time_seconds: Seconds to wait for lights to stabilize
        set_lights_func: Async function to set all lights (state: bool) -> None
        set_light_func: Async function to set one light (entity_id: str, brightness: int) -> None
        read_sensor_func: Async function to read sensor value () -> float
        
    Returns:
        Dictionary mapping light pair names to validation results:
        {
            "expected": float,        # Expected combined lux
            "actual": float,          # Measured combined lux
            "error_percent": float,   # Percentage error
            "valid": bool            # True if within tolerance
        }
    """
    _LOGGER.info("Validating light pair additivity")
    
    validation_results = {}
    
    # Test first 3 contributing lights (if available)
    contributing_lights = list(light_contributions.keys())
    lights_to_test = contributing_lights[:3]
    
    if len(lights_to_test) < 2:
        _LOGGER.info("Not enough contributing lights for pair validation, skipping")
        return validation_results
    
    # Test sequential pairs
    for i in range(len(lights_to_test) - 1):
        light1 = lights_to_test[i]
        light2 = lights_to_test[i + 1]
        
        # Get individual contributions
        contrib1 = light_contributions[light1]["max_contribution"]
        contrib2 = light_contributions[light2]["max_contribution"]
        expected_total = contrib1 + contrib2
        
        _LOGGER.debug("Testing pair: %s (%.1f lux) + %s (%.1f lux) = %.1f lux expected",
                     light1, contrib1, light2, contrib2, expected_total)
        
        try:
            # Turn off all lights
            await set_lights_func(False)
            await asyncio.sleep(settle_time_seconds)
            base_lux = await read_sensor_func()
            
            # Turn on both test lights
            await set_light_func(light1, 255)
            await set_light_func(light2, 255)
            await asyncio.sleep(settle_time_seconds)
            both_lights_lux = await read_sensor_func()
            
            actual_total = both_lights_lux - base_lux
            
            # Calculate error percentage
            error_pct = abs(actual_total - expected_total) / expected_total * 100 if expected_total > 0 else 0
            
            is_valid = error_pct <= PAIR_VALIDATION_ERROR_TOLERANCE_PERCENT
            
            _LOGGER.info("Pair %s + %s: expected=%.1f, actual=%.1f, error=%.1f%% (%s)",
                        light1.split('.')[-1], light2.split('.')[-1],
                        expected_total, actual_total, error_pct,
                        "PASS" if is_valid else "WARN")
            
            # Store validation results
            pair_name = f"{light1}+{light2}"
            validation_results[pair_name] = {
                "expected": expected_total,
                "actual": actual_total,
                "error_percent": error_pct,
                "valid": is_valid
            }
            
        except Exception as err:
            _LOGGER.error("Failed to validate pair %s + %s: %s", light1, light2, err)
    
    # Summary
    total_pairs = len(validation_results)
    valid_pairs = sum(1 for result in validation_results.values() if result["valid"])
    
    _LOGGER.info("Pair validation complete: %d/%d pairs within tolerance", valid_pairs, total_pairs)
    
    return validation_results
