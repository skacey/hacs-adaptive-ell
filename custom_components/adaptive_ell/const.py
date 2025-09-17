"""Constants for Adaptive ELL integration."""

DOMAIN = "adaptive_ell"

# Configuration keys
CONF_ROOM_NAME = "room_name"
CONF_CALIBRATION_SENSOR = "calibration_sensor"
CONF_TARGET_LUX = "target_lux"
CONF_SUN_CONTRIBUTION = "sun_contribution"

# Default values
DEFAULT_TARGET_LUX = 300
DEFAULT_SUN_CONTRIBUTION = 0.5
DEFAULT_UPDATE_INTERVAL = 300  # 5 minutes

# Service names
SERVICE_START_CALIBRATION = "start_calibration"
SERVICE_STOP_CALIBRATION = "stop_calibration"
SERVICE_MANUAL_WEIGHT_ADJUST = "manual_weight_adjust"

# Entity names
ELL_SENSOR_NAME = "Estimated Light Level"
CALIBRATION_MODE_SWITCH = "Calibration Mode"

# Data storage keys
DATA_ROOM_WEIGHTS = "room_weights"
DATA_CALIBRATION_DATA = "calibration_data"
DATA_COORDINATOR = "coordinator"

# Calibration constants
CALIBRATION_SETTLE_TIME = 5  # seconds
CALIBRATION_LUX_THRESHOLD = 10  # minimum lux change to record
LIGHT_DISCOVERY_BRIGHTNESS = 50  # percent brightness for discovery