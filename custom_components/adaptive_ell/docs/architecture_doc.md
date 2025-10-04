# Architecture Documentation

## System Overview

Adaptive ELL is a Home Assistant custom integration that learns how lights contribute to room illumination through automated calibration, then provides real-time estimated lux levels based on current light states.

## Design Principles

1. **Modular Independence:** Each calibration phase is a separate module that can be tested, debugged, and promoted independently
2. **Quality-Driven Development:** Code progresses through Alpha → Beta → Silver → Gold based on UAT results
3. **Fail Gracefully:** Errors in one module should not crash the entire calibration
4. **User Transparency:** Clear logging and notifications about what's happening and what failed

## Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Home Assistant                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Adaptive ELL Integration                │  │
│  │                                                       │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │         Coordinator (Orchestration)            │  │  │
│  │  │  - Manages calibration flow                    │  │  │
│  │  │  - Delegates to phase modules                  │  │  │
│  │  │  - Handles state updates                       │  │  │
│  │  └─────────────┬──────────────────────────────────┘  │  │
│  │                │                                      │  │
│  │      ┌─────────┴──────────┐                          │  │
│  │      │  Calibration Phases │                          │  │
│  │      ├────────────────────┴─────────────────────┐    │  │
│  │      │ restore_state.py          (Alpha-BROKEN) │    │  │
│  │      │ test_min_max.py           (Alpha)        │    │  │
│  │      │ test_individual_lights.py (Alpha-BROKEN) │    │  │
│  │      │ validate_combinations.py  (Alpha)        │    │  │
│  │      │ save_calibration.py       (Alpha)        │    │  │
│  │      └──────────────────────────────────────────┘    │  │
│  │                                                       │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │              Sensor Entities                   │  │  │
│  │  │  - sensor.adaptive_ell_{room}                 │  │  │
│  │  │  - sensor.adaptive_ell_{room}_calibration     │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  │                                                       │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │            Config Flow (UI)                    │  │  │
│  │  │  - Room selection                              │  │  │
│  │  │  - Sensor selection                            │  │  │
│  │  │  - Area selection                              │  │  │
│  │  │  - Calibration trigger                         │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Calibration Flow

### Phase Sequence

```
1. Capture Initial States
   ↓
2. Validate Setup (sensor + lights)
   ↓
3. Calibrate Timing (determine settle time)
   ↓
4. Test Min/Max Levels (all OFF / all ON)
   ↓
5. Test Individual Light Contributions
   ↓
6. Validate Light Pair Additivity
   ↓
7. Save Calibration Data
   ↓
8. Restore Initial States (always runs)
```

### Error Handling Strategy

**Current State (Alpha):**
- Phases fail independently
- Coordinator catches phase exceptions
- Restoration always attempts to run
- Errors logged but not always reported to user

**Target State (Silver):**
- Each phase returns success/failure with details
- Failed phases trigger specific recovery actions
- User gets detailed error report with solutions
- Partial calibration data can be saved/reused

## Module Interfaces

### Calibration Phase Modules

All calibration phase modules follow this pattern:

```python
"""
QUALITY LEVEL: Alpha/Beta/Silver/Gold
STATUS: Description

KNOWN ISSUES:
- Issue 1
- Issue 2
...

Module description
"""

async def primary_function(
    hass: HomeAssistant,
    # Required parameters
    ...
) -> ReturnType:
    """
    Function description.
    
    Args:
        ...
    
    Returns:
        Description of return value
        
    Raises:
        HomeAssistantError: When unrecoverable failure occurs
    """
```

### restore_state.py

**Purpose:** Capture and restore light states before/after calibration

**Functions:**
- `capture_initial_states(hass, light_entities) -> Dict[str, Dict]`
- `restore_initial_states(hass, initial_states) -> Dict[str, str]`

**Known Issues:**
- Does not restore OFF states correctly
- No retry logic
- No user notification of failures

### test_min_max.py

**Purpose:** Measure room lux with all lights OFF and all lights ON

**Functions:**
- `test_min_max_levels(hass, sensor_entity, light_entities, settle_time, ...) -> Tuple[float, float]`

**Known Issues:**
- No validation that lights actually reached expected state
- Assumes all lights respond within settle_time

### test_individual_lights.py

**Purpose:** Test each light individually to measure its contribution

**Functions:**
- `test_individual_light_contributions(hass, light_entities, settle_time, ...) -> Dict[str, Dict]`

**Known Issues:**
- 63% failure rate in production
- No validation that light turned on before reading
- No progressive timeout adjustment
- Fails silently

### validate_combinations.py

**Purpose:** Validate that light contributions are approximately additive

**Functions:**
- `validate_light_pair_additivity(hass, light_contributions, settle_time, ...) -> Dict[str, Dict]`

**Known Issues:**
- Only tests first 3 lights
- 30% error tolerance very high
- Results not used to improve accuracy

### save_calibration.py

**Purpose:** Persist calibration data to config entry

**Functions:**
- `save_calibration_data(hass, config_entry, room_name, ...) -> bool`

**Known Issues:**
- No backup before overwrite
- No versioning
- No rollback on partial failure

## Data Structures

### Light Contribution Data

```python
{
    "light.entity_id": {
        "max_contribution": float,  # Lux at full brightness
        "base_lux": float,          # Room lux without this light
        "with_light_lux": float,    # Room lux with this light
        "linear_validated": bool    # Pair test result
    }
}
```

### Calibration Data

```python
{
    "timestamp": str,              # ISO format
    "room_name": str,
    "min_lux": float,
    "max_lux": float,
    "light_contributions": dict,   # See above
    "validation_results": dict,
    "settle_time_seconds": int,
    "excluded_lights": list,
    "contributing_light_count": int,
    "total_contribution_lux": float
}
```

## State Management

### Coordinator State

- `is_calibrating`: bool
- `calibration_step`: str (idle, capturing_states, testing_min_max, etc.)
- `lights`: list[str] (validated working lights)
- `excluded_lights`: list[str] (failed during validation)
- `light_contributions`: dict (calibrated data)
- `initial_light_states`: dict (for restoration)

### Sensor Updates

The coordinator updates every 10 seconds:
1. Read current sensor lux
2. Calculate estimated lux based on light states
3. Update sensor entities
4. Check for light state changes

## Quality Level Progression

### Module Promotion Path

```
Alpha (works in basic scenarios)
  ↓ Pass Alpha UAT
Beta (works reliably in optimal conditions)
  ↓ Pass Beta UAT
Silver (handles errors with clear reporting)
  ↓ Pass Silver UAT
Gold (self-heals and offers multiple solutions)
```

See [docs/QUALITY_LEVELS.md](docs/QUALITY_LEVELS.md) for specific UAT requirements per module.

## Future Architecture Goals

### Post-Release Candidate

When all modules reach at least Silver:

1. **Standard Testing:** Replace UAT with automated unit/integration tests
2. **CI/CD Pipeline:** GitHub Actions for test automation
3. **Semantic Versioning:** Replace quality levels with standard versions
4. **Issue Tracking:** GitHub Issues/Projects for bug tracking
5. **Standard Contributions:** PR-based workflow with code review

### Planned Features (Post-Silver)

- **Goal-Seeking Services:** `adaptive_ell.set_target_lux` to reach specific lux levels
- **Recalibration Scheduling:** Automatic periodic recalibration
- **Failure Detection:** Identify dying bulbs or sensor drift
- **Multi-Sensor Support:** Use multiple sensors per room for spatial mapping
- **Scene Integration:** Pre-calculated scenes for common lux targets

## Dependencies

- Home Assistant Core 2024.1.0+
- Python 3.11+
- No external Python packages (uses HA built-ins only)

## Testing Strategy

### Current (Alpha)

- Manual testing with real hardware
- UAT-based promotion criteria
- User feedback drives improvements

### Future (Post-Release)

- Unit tests for each module (pytest)
- Integration tests with HA test framework
- Mock hardware for CI/CD
- Property-based testing for edge cases
