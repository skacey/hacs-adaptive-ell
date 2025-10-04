"""
QUALITY LEVEL: Alpha
STATUS: BROKEN

KNOWN ISSUES:
- Does not restore lights to OFF state (leaves lights ON after calibration)
- Unknown if brightness restoration works correctly
- Unknown if color restoration works for all color modes
- No error handling for unavailable lights during restore
- No retry logic for failed restoration attempts
- No user notification of restoration failures

Restore light states after calibration.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def capture_initial_states(
    hass: HomeAssistant,
    light_entities: list[str]
) -> Dict[str, Dict[str, Any]]:
    """
    Capture current state of all lights before calibration.
    
    Args:
        hass: Home Assistant instance
        light_entities: List of light entity IDs to capture
        
    Returns:
        Dictionary mapping entity_id to state information
    """
    initial_states = {}
    
    _LOGGER.info("📸 Capturing initial light states for %d lights...", len(light_entities))
    
    for light_entity in light_entities:
        light_state = hass.states.get(light_entity)
        if light_state:
            initial_states[light_entity] = {
                "state": light_state.state,
                "brightness": light_state.attributes.get("brightness"),
                "rgb_color": light_state.attributes.get("rgb_color"),
                "color_temp": light_state.attributes.get("color_temp"),
                "color_temp_kelvin": light_state.attributes.get("color_temp_kelvin"),
                "hs_color": light_state.attributes.get("hs_color"),
                "xy_color": light_state.attributes.get("xy_color"),
            }
            _LOGGER.debug("Captured state for %s: %s", light_entity, light_state.state)
        else:
            _LOGGER.warning("Could not capture state for %s - entity not found", light_entity)
    
    return initial_states


async def restore_initial_states(
    hass: HomeAssistant,
    initial_states: Dict[str, Dict[str, Any]]
) -> Dict[str, str]:
    """
    Restore lights to their captured initial state.
    
    Args:
        hass: Home Assistant instance
        initial_states: Dictionary of captured states from capture_initial_states()
        
    Returns:
        Dictionary mapping entity_id to restoration status ("success", "failed", "skipped")
    """
    _LOGGER.info("🔄 Restoring initial light states for %d lights...", len(initial_states))
    
    restoration_results = {}
    
    for light_entity, saved_state in initial_states.items():
        try:
            if saved_state["state"] == STATE_OFF:
                await hass.services.async_call(
                    LIGHT_DOMAIN, "turn_off", {"entity_id": light_entity}
                )
                _LOGGER.debug("Restored %s to OFF", light_entity)
            else:
                service_data = {"entity_id": light_entity}
                
                # Restore brightness
                if saved_state.get("brightness"):
                    service_data["brightness"] = saved_state["brightness"]
                
                # Restore color (prioritize rgb, then color_temp, then other formats)
                if saved_state.get("rgb_color"):
                    service_data["rgb_color"] = saved_state["rgb_color"]
                elif saved_state.get("color_temp"):
                    service_data["color_temp"] = saved_state["color_temp"]
                elif saved_state.get("color_temp_kelvin"):
                    service_data["color_temp_kelvin"] = saved_state["color_temp_kelvin"]
                elif saved_state.get("hs_color"):
                    service_data["hs_color"] = saved_state["hs_color"]
                elif saved_state.get("xy_color"):
                    service_data["xy_color"] = saved_state["xy_color"]
                
                await hass.services.async_call(
                    LIGHT_DOMAIN, "turn_on", service_data
                )
                _LOGGER.debug("Restored %s to ON with attributes", light_entity)
            
            restoration_results[light_entity] = "success"
                
        except Exception as err:
            _LOGGER.error("Failed to restore %s: %s", light_entity, err)
            restoration_results[light_entity] = "failed"
    
    # Summary logging
    success_count = sum(1 for status in restoration_results.values() if status == "success")
    failed_count = sum(1 for status in restoration_results.values() if status == "failed")
    
    _LOGGER.info("Light state restoration complete: %d succeeded, %d failed", 
                 success_count, failed_count)
    
    if failed_count > 0:
        failed_entities = [entity for entity, status in restoration_results.items() 
                          if status == "failed"]
        _LOGGER.warning("Failed to restore: %s", failed_entities)
    
    return restoration_results
