# Contributing to Adaptive ELL Integration

Thank you for your interest in contributing! This project uses a modular architecture where individual components can be developed and tested independently.

## Current Development Phase

We are in **active alpha development** with a **modular refactoring phase**. Multiple contributors can work on different modules simultaneously.

**Priority Modules:**
1. `restore_state.py` - BROKEN, needs immediate attention
2. `test_individual_lights.py` - 63% failure rate, needs debugging

## Getting Started

### Prerequisites

- Python 3.11+
- Home Assistant development environment
- Physical smart lights and lux sensor for testing
- Git

### Development Setup

1. **Fork and Clone**
   ```bash
   git clone https://github.com/YOUR_USERNAME/hacs-adaptive-ell.git
   cd hacs-adaptive-ell
   ```

2. **Install in Home Assistant**
   ```bash
   # Copy to your HA custom_components directory
   cp -r custom_components/adaptive_ell /path/to/homeassistant/custom_components/
   ```

3. **Enable Debug Logging**
   Add to `configuration.yaml`:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.adaptive_ell: debug
   ```

4. **Create Test Environment**
   - Set up a test room with multiple lights
   - Have a movable lux sensor
   - Document your hardware setup

## Development Workflow

### Working on a Module

1. **Choose a Module**
   - Check module header for QUALITY LEVEL and KNOWN ISSUES
   - Priority: BROKEN modules first, then Alpha → Beta → Silver → Gold

2. **Create Feature Branch**
   ```bash
   git checkout -b fix/restore-state-off-issue
   ```

3. **Update Module Header**
   ```python
   """
   QUALITY LEVEL: Alpha
   STATUS: IN PROGRESS - fixing OFF state restoration
   
   KNOWN ISSUES:
   - [WORKING ON] Does not restore lights to OFF state
   - Unknown if brightness restoration works correctly
   ...
   """
   ```

4. **Develop and Test**
   - Make changes to the module
   - Test with real hardware
   - Document test results

5. **Update Documentation**
   - Update KNOWN ISSUES in module header
   - Add test results to PR description
   - Update quality level if UAT passed

6. **Submit Pull Request**
   - Clear title: "Fix restore_state OFF state handling"
   - Describe what changed and test results
   - Link to any relevant issues

### Quality Level Promotion

See [docs/QUALITY_LEVELS.md](docs/QUALITY_LEVELS.md) for detailed UAT requirements.

**To Promote a Module:**

1. Pass all UAT tests for current level
2. Document test results
3. Update module header with new quality level
4. Submit PR with test evidence

**Example PR Title:** "Promote restore_state.py to Beta (UAT passed)"

## Code Standards

### Python Style

- Follow PEP 8
- Use type hints for function signatures
- Docstrings for all public functions
- Clear variable names (full words, not abbreviations)

### Module Structure

```python
"""
QUALITY LEVEL: [Alpha/Beta/Silver/Gold]
STATUS: [Brief status]

KNOWN ISSUES:
- Issue 1 description
- Issue 2 description

Module description
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def main_function(
    hass: HomeAssistant,
    param1: str,
    param2: int
) -> Dict[str, Any]:
    """
    Function description.
    
    Args:
        hass: Home Assistant instance
        param1: Description
        param2: Description
        
    Returns:
        Description of return value
        
    Raises:
        HomeAssistantError: Description of when this is raised
    """
    _LOGGER.info("Starting operation")
    
    try:
        # Implementation
        result = {}
        return result
    except Exception as err:
        _LOGGER.error("Operation failed: %s", err)
        raise
```

### Logging Standards

**Use appropriate log levels:**

```python
_LOGGER.debug("Light %s state: %s", entity_id, state)  # Verbose details
_LOGGER.info("Calibration starting with %d lights", count)  # Important events
_LOGGER.warning("Light %s failed to respond", entity_id)  # Recoverable issues
_LOGGER.error("Calibration failed: %s", error)  # Unrecoverable failures
```

**For calibration phases, use ERROR level for user-visible milestones:**

```python
_LOGGER.error("=== CALIBRATION STARTING ===")  # User wants to see this
_LOGGER.error("✓ SUCCESS: Found %d contributing lights", count)
_LOGGER.error("⚠️ WARNING: Excluded non-responsive lights")
```

### Error Handling

**Always handle errors gracefully:**

```python
async def process_light(entity_id: str) -> bool:
    """Process a light, return True if successful."""
    try:
        # Attempt operation
        await hass.services.async_call(...)
        return True
    except Exception as err:
        _LOGGER.error("Failed to process %s: %s", entity_id, err)
        return False  # Don't crash, return failure status
```

## Testing Guidelines

### Manual Testing Requirements

**For each module:**

1. **Happy Path:** Test with optimal conditions (all devices working)
2. **Device Failures:** Test with unavailable devices
3. **Partial Failures:** Test with some devices working, some failing
4. **Edge Cases:** Test with minimum devices (1-2 lights)
5. **Scale:** Test with many devices (15+ lights)

**Document test results:**

```markdown
## Test Results for restore_state.py

### Test 1: Happy Path
- Setup: 5 lights, all initially OFF
- Result: ✓ All lights restored to OFF
- Logs: No errors

### Test 2: Some Lights Unavailable
- Setup: 5 lights, 2 marked unavailable
- Result: ✓ Available lights restored, unavailable lights logged
- Logs: WARNING for unavailable lights

### Test 3: Restoration Fails
- Setup: Manually disabled light during restore
- Result: ✗ ERROR logged but process continued
- Logs: "Failed to restore light.test: Device unavailable"
```

### User Acceptance Testing (UAT)

Each quality level has specific UAT requirements. See [docs/QUALITY_LEVELS.md](docs/QUALITY_LEVELS.md).

**To pass UAT:**

1. Run all required tests for target level
2. Document every test with setup, result, and logs
3. All tests must pass consecutively (no cherry-picking)
4. Include photos/videos if applicable

## Pull Request Process

### PR Checklist

- [ ] Module header updated with quality level and status
- [ ] KNOWN ISSUES list updated (remove fixed, add new)
- [ ] All affected documentation updated
- [ ] Test results documented in PR description
- [ ] Logs from test runs attached
- [ ] No merge conflicts

### PR Template

```markdown
## Description
Brief description of changes

## Module
- Module: `calibration_phases/restore_state.py`
- Previous Quality Level: Alpha (BROKEN)
- New Quality Level: Alpha (FUNCTIONAL)

## Changes
- Fixed OFF state restoration by [specific change]
- Added retry logic for failed restorations
- Improved error logging

## Test Results
### Test 1: Restore OFF States
- Setup: 3 lights initially OFF
- Result: ✓ PASS - All lights restored to OFF
- Log: [paste relevant log]

### Test 2: Restore ON States with Brightness
- Setup: 2 lights ON at 50% brightness
- Result: ✓ PASS - Brightness within 5% of original
- Log: [paste relevant log]

[Continue for all UAT tests]

## Known Issues Remaining
- Retry logic not yet implemented for Silver level
- No user notification on partial failures

## Checklist
- [x] Tested with real hardware
- [x] Updated module header
- [x] Documented test results
- [x] Updated ARCHITECTURE.md if needed
```

## Module-Specific Guidelines

### restore_state.py

**Priority:** CRITICAL - This breaks every calibration run

**Focus Areas:**
- OFF state restoration (primary bug)
- Brightness accuracy
- Color restoration (all formats)
- Error handling for unavailable lights

**Test With:**
- Lights starting OFF
- Lights starting ON at various brightness levels
- RGB, color temp, and white-only lights
- Some unavailable lights

### test_individual_lights.py

**Priority:** HIGH - 63% failure rate

**Focus Areas:**
- Why lights fail validation
- State verification before reading
- Progressive timeout adjustment
- Better error messages

**Test With:**
- Various light response times
- Lights that fail to turn on
- Very dim lights (near threshold)
- Many lights (15+)

## Communication

### Channels

- **GitHub Issues:** Bug reports and feature requests
- **Pull Requests:** Code contributions and review
- **Discussions:** Architecture questions and design

### Response Times

This is a volunteer project. Expect:
- PR review: 3-7 days
- Issue response: 1-3 days
- UAT validation: 1-2 weeks

## Code of Conduct

### Be Respectful

- Assume good intent
- Focus on the code, not the person
- Accept constructive criticism gracefully
- Help newcomers

### Be Honest

- Report bugs accurately (don't minimize or exaggerate)
- Document test failures honestly (no cherry-picking results)
- Admit when you don't know something
- Question designs that seem problematic

### Be Collaborative

- Share your reasoning
- Ask questions when unclear
- Help review others' PRs
- Document your work well

## Questions?

- Check [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Check [docs/QUALITY_LEVELS.md](docs/QUALITY_LEVELS.md) for UAT requirements
- Open a GitHub Discussion for questions
- Tag @skacey in urgent issues

## Thank You!

Every contribution helps improve this integration for the entire Home Assistant community. Whether you're fixing a critical bug or improving documentation, your work matters.
