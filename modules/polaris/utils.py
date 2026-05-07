from datetime import datetime, timezone


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def safe_bool(value):
    if str(value).lower() == "true":
        return 1
    try:
        return 1 if int(value or 0) else 0
    except Exception:
        return 0


def now_date_string():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
