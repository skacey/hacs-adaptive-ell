# Adaptive ELL Integration

**Version:** 0.1.0-alpha  
**Status:** Active Development - Modular Refactor Phase

Self-learning Home Assistant integration that calculates Estimated Light Level (ELL) by learning how lights contribute to room illumination through automated calibration.

## 🚧 Development Status

This integration is in **active alpha development** using a **hybrid quality management approach**. We are currently refactoring from a monolithic coordinator to modular, independently testable components.

**Current Phase:** Refactoring to modular architecture  
**Goal:** Working release candidate, then transition to standard open source model

### Quality Level System (Current Phase)

We use a four-level quality promotion system for development:

- **Alpha** - Code works in basic scenarios
- **Beta** - Code works reliably in optimal conditions  
- **Silver** - Code handles errors gracefully with clear reporting
- **Gold** - Code self-heals errors and offers multiple solutions

See [docs/QUALITY_LEVELS.md](docs/QUALITY_LEVELS.md) for detailed criteria.

### Future Direction

Once we achieve a working release candidate (all modules at least Silver), we will transition to:
- Standard semantic versioning (0.1.0, 1.0.0, etc.)
- Conventional CI/CD with automated testing
- GitHub Issues/Projects for tracking
- Standard open source contribution model

## ⚠️ Known Issues (As of Current Build)

- **Light Restoration:** Fails to restore lights to OFF state after calibration
- **Individual Light Testing:** ~63% failure rate with insufficient error reporting
- **Multi-room:** Config entry reading has key mismatch issues
- **Error Handling:** Inadequate failure recovery and user feedback

See module headers for specific known issues in each component.

## 📁 Project Structure

```
custom_components/adaptive_ell/
├── coordinator.py              # Orchestration (Alpha)
├── config_flow.py              # UI configuration (Alpha) 
├── sensor.py                   # ELL sensors (Alpha)
├── calibration_phases/         # Modular calibration components
│   ├── __init__.py
│   ├── restore_state.py        # BROKEN - Light state restoration
│   ├── test_min_max.py         # Alpha - Min/max level testing
│   ├── test_individual_lights.py # BROKEN - Individual contributions (63% fail rate)
│   ├── validate_combinations.py # Alpha - Pair additivity validation
│   └── save_calibration.py     # Alpha - Data persistence
└── docs/
    ├── ARCHITECTURE.md          # System design
    ├── QUALITY_LEVELS.md        # Promotion criteria
    └── CONTRIBUTING.md          # How to contribute
```

## 🎯 What This Integration Does

1. **Self-Calibration:** Automatically tests lights in your home to learn their contribution to room illumination
2. **Real-time Estimation:** Provides accurate estimated lux levels based on which lights are on
3. **Multi-room Support:** Independent calibration for each room
4. **Adaptive:** Accounts for light spillover from adjacent rooms

## 🔧 Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Install "Adaptive ELL Integration"
3. Restart Home Assistant
4. Add integration via UI: Settings → Devices & Services → Add Integration → Adaptive ELL

### Manual

1. Copy `custom_components/adaptive_ell` to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Add integration via UI

## 📖 Usage

### Initial Setup

1. **Add Integration:** Settings → Devices & Services → Add Integration → Adaptive ELL
2. **Select Room:** Choose the target room to calibrate
3. **Select Sensor:** Choose your most accurate lux sensor (you'll move this during calibration)
4. **Select Additional Areas:** Choose adjoining areas that might affect this room's lighting

### Calibration

1. **Move Sensor:** Physically place your lux sensor in the target room
2. **Start Calibration:** Go to the integration → Configure → Enable "Start Calibration"
3. **Wait:** Calibration takes 5-15 minutes depending on light count
4. **Done:** Integration now provides real-time estimated lux levels

### Using the Data

After calibration, you'll have:
- `sensor.adaptive_ell_{room}` - Current estimated light level
- `sensor.adaptive_ell_{room}_calibration` - Calibration status

Use these sensors in automations to make intelligent lighting decisions.

## 🤝 Contributing

We welcome contributions! This project uses a modular architecture where each calibration phase can be developed and tested independently.

**Current Priority:** Fixing `restore_state.py` and `test_individual_lights.py`

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for:
- How to work on individual modules
- Quality level promotion process
- Testing requirements
- Code standards

## 📋 Requirements

- Home Assistant 2024.1.0 or newer
- At least one lux sensor (recommend Third Reality Multi-Function Night Light)
- Smart lights in Home Assistant (any integration)

## 🐛 Reporting Issues

**Please include:**
1. Which module/phase failed (check logs for "QUALITY LEVEL: Alpha" headers)
2. Full error log from Home Assistant
3. Number of lights being tested
4. Light brands/types involved

## 📜 License

MIT License - See LICENSE file

## 🙏 Acknowledgments

- Home Assistant Community
- Blueprint developers who inspired this approach
- Early testers working through alpha issues

---

**Note:** This is alpha software under active development. Expect bugs, breaking changes, and significant refactoring. Not recommended for production use until we reach Beta/Silver quality levels.
