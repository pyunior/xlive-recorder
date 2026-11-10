import os.path
import environ


env = environ.Env()
if os.path.exists('.env'):
    environ.Env.read_env('.env')


DOWNLOADS_DIR = env.str("STRMNTR_DOWNLOAD_DIR", "downloads")
MIN_FREE_DISK_PERCENT = env.float("STRMNTR_MIN_FREE_SPACE", 5.0)  # in %
DEBUG = env.bool("STRMNTR_DEBUG", False)

# The camsoda bot ignores this setting in favor of a chrome useragent generated with the fake-useragent library
HTTP_USER_AGENT = env.str("STRMNTR_USER_AGENT", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0")

# Specify the full path to the ffmpeg binary. By default, ffmpeg found on PATH is used.
FFMPEG_PATH = env.str("STRMNTR_FFMPEG_PATH", 'ffmpeg')

# You can enter a number to select a specific height.
# Use a huge number here and closest match to get the highest resolution variant
# Eg: 240, 360, 480, 720, 1080, 1440, 99999
WANTED_RESOLUTION = env.int("STRMNTR_RESOLUTION", 1080)

# Specify match type when specified height
# Possible values: exact, exact_or_least_higher, exact_or_highest_lower, closest
# Beware of the exact policy. Nothing gets downloaded if the wanted resolution is not available
WANTED_RESOLUTION_PREFERENCE = env.str("STRMNTR_RESOLUTION_PREF", 'closest')

# Specify output container here
# Suggested values are 'mkv' or 'mp4'
CONTAINER = env.str("STRMNTR_CONTAINER", 'mp4')

# Add auto-generated VR format suffix to files
VR_FORMAT_SUFFIX = env.bool("STRMNTR_VR_FORMAT_SUFFIX", True)

# Set ffmpeg readrate to whatever works for you.
# Usually this should be either 0, 1 or 1.3 depending on the network
# Setting it to 0 can result in very fragmented recordings.
# 1 can result in skipped segments
# 1.3 should be the sweet spot but use what works
FFMPEG_READRATE = env.int("STRMNTR_FFMPEG_READRATE", 1.3)

# Specify the segment time in seconds
# If None, the video will be downloaded as a single file
# Example:
# 5 minutes
#SEGMENT_TIME = 700
# 1 hour
# SEGMENT_TIME = 3600
# Also see the ffmpeg documentation for the segment_time option
# You can specify time in hh:mm:ss format
# Example:
# 1 hour
#SEGMENT_TIME = '0:12:00'
SEGMENT_TIME = env.str("STRMNTR_SEGMENT_TIME", None)

# HTTP Manager configuration

# Bind address for the web server
# 0.0.0.0 for remote access from all host
WEBSERVER_HOST = env.str("STRMNTR_HOST", "127.0.0.1")
WEBSERVER_PORT = env.int("STRMNTR_PORT", 5000)

# Web UI skin
# Available options:
# - kseen715 - 2nd skin, currently broken
# - truck-kun (default) - 3rd skin, row oriented
# - shaftoverflow - 4th skin, card layout, links in menus
WEBSERVER_SKIN = env.str("STRMNTR_SKIN", "truck-kun")

# set frequency in seconds of how often the streamer list will update
WEB_LIST_FREQUENCY = env.int("STRMNTR_LIST_FREQ", 30)

# set frequency in seconds of how often the streamer's status will update on the recording page
WEB_STATUS_FREQUENCY = env.int("STRMNTR_STATUS_FREQ", 5)

# set theater_mode
WEB_THEATER_MODE = env.bool("STRMNTR_THEATER_MODE", False)

# confirm deletes, default to mobile-only.
# set to empty string to disable
# set to "MOBILE" to explicitly confirm deletes only on mobile
# set to any other non-falsy value to always check
WEB_CONFIRM_DELETES = env.str("STRMNTR_CONFIRM_DEL", "MOBILE")

# Password for the web server
# If empty no auth required, else username admin and choosen password
WEBSERVER_PASSWORD = env.str("STRMNTR_PASSWORD", "admin")


# --------------------------------------------------------------------------
# Watchdog: connect/disconnect alerts + daily broadcast-hours summary
# --------------------------------------------------------------------------

WATCHDOG_ENABLED = env.bool("STRMNTR_WATCHDOG", True)

# Telegram bot credentials. Create the bot with @BotFather, then get the chat
# id by writing to the bot and opening
#   https://api.telegram.org/bot<TOKEN>/getUpdates
# Group chats have a negative id (e.g. -1001234567890).
TELEGRAM_BOT_TOKEN = env.str("STRMNTR_TG_TOKEN", "")
TELEGRAM_CHAT_ID = env.str("STRMNTR_TG_CHAT_ID", "")

# Seconds between messages. Telegram throttles a bot to ~20 messages/minute
# per chat, so keep this above 3 to survive a morning wave of connections.
TELEGRAM_MIN_INTERVAL = env.float("STRMNTR_TG_MIN_INTERVAL", 3.5)

# Timezone used for alert timestamps, the report window and the report time.
WATCHDOG_TIMEZONE = env.str("STRMNTR_WD_TZ", "America/Bogota")

# Where the event log lives. Keep it off the recordings volume so a full disk
# does not take the history with it.
WATCHDOG_DB = env.str("STRMNTR_WD_DB", "watchdog.db")

# How often the room statuses are sampled, in seconds.
WATCHDOG_POLL_INTERVAL = env.int("STRMNTR_WD_POLL", 5)

# Statuses that count as "transmitting", comma separated (see enums/status.py).
# PRIVATE is included because a private show is still airtime.
WATCHDOG_ONLINE_STATUSES = env.str("STRMNTR_WD_ONLINE", "PUBLIC,PRIVATE")

# A change must hold this many seconds before it is confirmed and announced.
# This absorbs flapping; the event is still timestamped at the real change.
WATCHDOG_DEBOUNCE_ONLINE = env.int("STRMNTR_WD_DEBOUNCE_ON", 10)
WATCHDOG_DEBOUNCE_OFFLINE = env.int("STRMNTR_WD_DEBOUNCE_OFF", 90)

# Errors and rate limits mean "unknown", not "offline". After this many
# seconds without a usable status the room is finally treated as offline.
WATCHDOG_UNKNOWN_TIMEOUT = env.int("STRMNTR_WD_UNKNOWN_TIMEOUT", 900)

# Per-event alerts. Turn off to keep only the daily summary.
WATCHDOG_ALERTS = env.bool("STRMNTR_WD_ALERTS", True)

# Seconds to wait at startup before taking the baseline of who is online.
# The site bots need a moment to report their first status; without this the
# watchdog would read them all as offline and then announce every room as a
# fresh connection on every restart.
WATCHDOG_STARTUP_GRACE = env.int("STRMNTR_WD_GRACE", 90)

# Send a roll-call of who is online when the watchdog starts.
WATCHDOG_ALERT_ON_START = env.bool("STRMNTR_WD_ALERT_ON_START", False)

# Daily summary: published at REPORT_HOUR:REPORT_MINUTE local time, covering
# DAY_START_HOUR the previous day through DAY_START_HOUR the same day.
# Defaults: sent at 15:00, covering yesterday 06:00 -> today 06:00.
WATCHDOG_REPORT_HOUR = env.int("STRMNTR_WD_REPORT_HOUR", 15)
WATCHDOG_REPORT_MINUTE = env.int("STRMNTR_WD_REPORT_MINUTE", 0)
WATCHDOG_DAY_START_HOUR = env.int("STRMNTR_WD_DAY_START_HOUR", 6)

# Days of history kept in the event log.
WATCHDOG_RETENTION_DAYS = env.int("STRMNTR_WD_RETENTION", 120)
