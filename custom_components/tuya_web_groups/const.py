"""Constants for the Tuya WebGroups integration."""
from __future__ import annotations

DOMAIN = "tuya_web_groups"

CONF_COOKIE = "cookie"
CONF_MICRO_APP_ID = "micro_app_id"
CONF_HOME_ID = "home_id"
CONF_GROUP_IDS = "group_ids"
CONF_GROUP_NAMES = "group_names"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EXCLUDED_GROUPS = "excluded_groups"

DEFAULT_BASE = "https://cn.device.tuyasmart.com"
DEFAULT_MICRO_APP_ID = "2021044239474884698"   # "群组管理" micro-app
DEFAULT_MAIN_APP_ID = "main-app"
DEFAULT_SCAN_INTERVAL = 15

# --- DP numbers used by the web group API --------------------------------
# The App addresses group DPs by their *numeric* ability id, which we read
# from  GET /v1.0/m/sdf/ss/panels/device/{home}/{dev}/model
#   abilityId 1 -> switch_led
#   abilityId 2 -> work_mode
#   abilityId 3 -> bright_value
#   abilityId 4 -> temp_value
DP_SWITCH = "switch_led"
DP_WORK_MODE = "work_mode"
DP_BRIGHT = "bright_value"
DP_TEMP = "temp_value"

# fallback mapping if the model call fails (it is stable per product)
DEFAULT_DP_NUMBERS = {
    DP_SWITCH: 1,
    DP_WORK_MODE: 2,
    DP_BRIGHT: 3,
    DP_TEMP: 4,
}

# brightness / colour temperature ranges
GROUP_BRIGHT_MIN = 10
GROUP_BRIGHT_MAX = 1000
GROUP_TEMP_MIN = 0
GROUP_TEMP_MAX = 1000
MIN_KELVIN = 2700
MAX_KELVIN = 6500

GROUP_COLOUR_MODES = ("white", "colour", "scene", "music")
