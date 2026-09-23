"""Constants for the Tuya Cloud Groups integration."""
from __future__ import annotations

DOMAIN = "tuya_cloud_groups"

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_USER_ID = "user_id"
CONF_REGION = "region"
CONF_SPACE_ID = "space_id"          # Tuya "space" = a home
CONF_HOME_ID = "home_id"            # legacy alias kept for compatibility
CONF_GROUP_IDS = "group_ids"
CONF_GROUP_NAMES = "group_names"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EXCLUDED_GROUPS = "excluded_groups"   # groups the user removed by hand

DEFAULT_REGION = "cn"
DEFAULT_SCAN_INTERVAL = 15

REGION_ENDPOINTS = {
    "cn": "https://openapi.tuyacn.com",
    "us": "https://openapi.tuyaus.com",
    "eu": "https://openapi.tuyaeu.com",
    "in": "https://openapi.tuyain.com",
    "ueaz": "https://openapi-ueaz.tuyaus.com",
    "weaz": "https://openapi-weaz.tuyaeu.com",
    "sg": "https://openapi-sg.iotbing.com",
}

# --- group-level property codes (Tuya cloud group, v2.1) -------------------
# These are what the mobile App itself writes, so the group shadow stays in
# sync.  Note they carry **no** `_v2` suffix -- that suffix only exists on the
# member devices' own DP names.
DP_SWITCH = "switch_led"
DP_BRIGHT = "bright_value"
DP_TEMP = "temp_value"
DP_WORK_MODE = "work_mode"

# property that must be present for brightness control to work at all
GROUP_HAS_BRIGHT = "bright_value"

# brightness / colour-temperature ranges used by the group API
GROUP_BRIGHT_MIN = 10
GROUP_BRIGHT_MAX = 1000
GROUP_TEMP_MIN = 0
GROUP_TEMP_MAX = 1000

# the App shows brightness as value/10 (600 -> "60%"), so map HA's 0..255
# onto the same range LocalTuya uses
LOWER_BRIGHTNESS = 10
UPPER_BRIGHTNESS = 1000
MIN_KELVIN = 2700
MAX_KELVIN = 6500

# work modes the group understands
GROUP_COLOUR_MODES = ("white", "colour", "scene", "music")
