"""The Adaptive ELL integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import AdaptiveELLCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Adaptive ELL from a config entry."""
    
    # Create coordinator
    coordinator = AdaptiveELLCoordinator(hass)
    
    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()
    
    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN] = coordinator
    
    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register services
    await _async_register_services(hass, coordinator)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN] = None
    
    return unload_ok


async def _async_register_services(hass: HomeAssistant, coordinator: AdaptiveELLCoordinator) -> None:
    """Register integration services."""
    
    async def start_calibration(call):
        """Start calibration service."""
        try:
            await coordinator.start_calibration()
        except Exception as err:
            _LOGGER.error("Failed to start calibration: %s", err)
    
    async def stop_calibration(call):
        """Stop calibration service."""
        try:
            await coordinator.stop_calibration()
        except Exception as err:
            _LOGGER.error("Failed to stop calibration: %s", err)
    
    # Register services
    hass.services.async_register(DOMAIN, "start_calibration", start_calibration)
    hass.services.async_register(DOMAIN, "stop_calibration", stop_calibration)
    
    _LOGGER.info("Adaptive ELL services registered")