"""The Adaptive ELL integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry

from .const import DOMAIN
from .coordinator import AdaptiveELLCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Adaptive ELL from a config entry."""
    
    # Create coordinator
    coordinator = AdaptiveELLCoordinator(hass, entry)
    
    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()
    
    # Store coordinator per entry
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register services only once
    if len(hass.data[DOMAIN]) == 1:  # First entry
        await _async_register_services(hass)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Clean up this coordinator
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            coordinator = hass.data[DOMAIN][entry.entry_id]
            await coordinator.async_shutdown()
            hass.data[DOMAIN].pop(entry.entry_id)
            
        # Remove services if this was the last entry
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "start_calibration")
            hass.services.async_remove(DOMAIN, "stop_calibration")
            hass.data.pop(DOMAIN)
    
    return unload_ok


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    
    async def start_calibration(call: ServiceCall):
        """Start calibration service."""
        try:
            _LOGGER.info("=== SERVICE CALL: start_calibration ===")
            _LOGGER.info("Service call data: %s", call.data)
            
            # Find the coordinator to use based on target area or service context
            coordinator = await _find_target_coordinator(hass, call)
            if coordinator:
                _LOGGER.info("Found coordinator for area: %s", coordinator.room_name)
                _LOGGER.info("Coordinator config: sensor=%s, lights=%d", 
                           coordinator.sensor_entity, len(coordinator.lights))
                await coordinator.start_calibration_from_options()
                _LOGGER.info("Calibration started successfully")
            else:
                _LOGGER.error("No coordinator found for calibration")
        except Exception as err:
            _LOGGER.error("Failed to start calibration: %s", err, exc_info=True)
    
    async def stop_calibration(call: ServiceCall):
        """Stop calibration service."""
        try:
            coordinator = await _find_target_coordinator(hass, call)
            if coordinator:
                await coordinator.stop_calibration()
            else:
                _LOGGER.error("No coordinator found for calibration stop")
        except Exception as err:
            _LOGGER.error("Failed to stop calibration: %s", err)
    
    # Register services
    hass.services.async_register(DOMAIN, "start_calibration", start_calibration)
    hass.services.async_register(DOMAIN, "stop_calibration", stop_calibration)
    
    _LOGGER.info("Adaptive ELL services registered")


async def _find_target_coordinator(hass: HomeAssistant, call: ServiceCall) -> AdaptiveELLCoordinator | None:
    """Find the target coordinator for a service call."""
    coordinators = hass.data.get(DOMAIN, {})
    
    if not coordinators:
        _LOGGER.error("No coordinators found")
        return None
    
    _LOGGER.info("Found %d coordinators", len(coordinators))
    
    # If there's only one coordinator, use it
    if len(coordinators) == 1:
        coordinator = next(iter(coordinators.values()))
        _LOGGER.info("Using single coordinator for area: %s", coordinator.room_name)
        return coordinator
    
    # Try to find coordinator by area if specified in service data
    target_area = call.data.get("area")
    if target_area:
        _LOGGER.info("Looking for coordinator with area: %s", target_area)
        area_reg = area_registry.async_get(hass)
        area = None
        
        # Find area by name or ID
        for area_obj in area_reg.areas.values():
            if area_obj.name == target_area or area_obj.id == target_area:
                area = area_obj
                break
        
        if area:
            # Find coordinator for this area
            for coordinator in coordinators.values():
                config_data = coordinator.config_entry.data
                if config_data.get("test_area") == area.id:
                    _LOGGER.info("Found coordinator for area %s", target_area)
                    return coordinator
            _LOGGER.warning("No coordinator found for area %s", target_area)
        else:
            _LOGGER.warning("Area %s not found", target_area)
    
    # If no specific area, try to find a coordinator that's ready for calibration
    for coordinator in coordinators.values():
        if not coordinator.is_calibrating:
            _LOGGER.info("Using available coordinator for area: %s", coordinator.room_name)
            return coordinator
    
    # Last resort: return the first coordinator
    coordinator = next(iter(coordinators.values()))
    _LOGGER.info("Using first coordinator for area: %s", coordinator.room_name)
    return coordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Adaptive ELL component."""
    return True