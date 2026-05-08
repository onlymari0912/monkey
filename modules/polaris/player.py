from core_database import get_db
from modules.polaris.utils import now_date_string, safe_int
from tinydb import where
import random
import uuid

ITEM_CHANGE_LOG_HISTORY_LIMIT = 200

def get_profile(dataid=None, refid=None, usr_id=None):
    db = get_db().table("polaris_profile")
    usr_id = safe_int(usr_id, 0)
    dataid = str(dataid or "").strip()
    refid = str(refid or "").strip()

    if usr_id > 0:
        p = db.get(where("usr_id") == usr_id)
        if p:
            return p

    if dataid:
        p = db.get(where("card") == dataid)
        if p:
            return p

    if refid:
        p = db.get(where("refid") == refid)
        if p:
            return p

    return None


def save_profile(profile):
    if not profile:
        return None

    db = get_db().table("polaris_profile")
    if safe_int(profile.get("usr_id"), 0) > 0:
        db.upsert(profile, where("usr_id") == profile["usr_id"])
    else:
        db.upsert(profile, where("card") == profile.get("card"))
    return profile


def generate_unique_crew_id(current_usr_id=None):
    db = get_db().table("polaris_profile")
    for _ in range(1024):
        candidate = f"{random.randint(0, 99999999):08d}"
        conflicts = db.search(where("crew_id") == candidate)
        if not any(profile.get("usr_id") != current_usr_id for profile in conflicts):
            return candidate
    raise RuntimeError("Failed to allocate unique crew_id")

def ensure_signup_profile(profile, dataid, refid, name, pin=""):
    db = get_db().table("polaris_profile")
    if not profile:
        print("polaris_usr_sign_up: Creating NEW profile")
        profile = {}

    profile["card"] = dataid
    if refid:
        profile["refid"] = refid
    if pin and not profile.get("pin"):
        profile["pin"] = pin
    if not profile.get("name"):
        profile["name"] = name or "PLAYER"

    usr_id = safe_int(profile.get("usr_id"), 0)
    if usr_id <= 0:
        existing_usr_ids = {
            safe_int(item.get("usr_id"), 0)
            for item in db.all()
        }
        while True:
            usr_id = random.randint(100000, 999999)
            if usr_id not in existing_usr_ids:
                break
        profile["usr_id"] = usr_id

    if not profile.get("crew_id"):
        profile["crew_id"] = generate_unique_crew_id(usr_id)
    profile.setdefault("mira", 0)
    profile.setdefault("items", {})
    profile.setdefault("counts", {})
    profile.setdefault("action_counts", {})
    profile.setdefault("character_cards", [])
    profile.setdefault("characters", [])
    profile.setdefault("decks", [])

    return profile

def append_unique_entries(entries, new_entries, key_name="uuid"):
    seen = {
        entry.get(key_name)
        for entry in entries
        if isinstance(entry, dict) and entry.get(key_name)
    }
    for entry in new_entries:
        entry_key = entry.get(key_name)
        if entry_key and entry_key in seen:
            continue
        entries.append(entry)
        if entry_key:
            seen.add(entry_key)

def normalize_usr_character_entry(entry):
    if not isinstance(entry, dict):
        return None
    chara_id = str(entry.get("chara_id", "")).strip()
    if not chara_id:
        return None
    return {
        "chara_id": chara_id,
        "closeness": safe_int(entry.get("closeness", 0), 0),
        "home_touch_count": safe_int(entry.get("home_touch_count", 0), 0),
    }

def _ensure_item_state(profile):
    profile.setdefault("items", {})
    if not isinstance(profile["items"], dict):
        profile["items"] = {}
    processed_log_ids = profile.setdefault("processed_item_change_log_uuids", [])
    if not isinstance(processed_log_ids, list):
        processed_log_ids = []
        profile["processed_item_change_log_uuids"] = processed_log_ids
    return processed_log_ids


def _trim_processed_item_logs(profile):
    processed_log_ids = profile.get("processed_item_change_log_uuids") or []
    if len(processed_log_ids) > ITEM_CHANGE_LOG_HISTORY_LIMIT:
        profile["processed_item_change_log_uuids"] = processed_log_ids[-ITEM_CHANGE_LOG_HISTORY_LIMIT:]


def apply_item_change_log(profile, usr_item_change_log, get_text, get_int):
    if profile is None or usr_item_change_log is None:
        return

    processed_log_ids = _ensure_item_state(profile)
    processed_log_id_set = {
        str(log_id)
        for log_id in processed_log_ids
        if str(log_id or "").strip()
    }

    for item in usr_item_change_log.findall("item"):
        log_uuid = str(get_text(item, "uuid") or "").strip()
        if log_uuid and log_uuid in processed_log_id_set:
            continue
        item_id = str(get_text(item, "item_id") or "").strip()
        if not item_id:
            continue
        change_count = get_int(item, "change_count")
        if item_id == "money.mira":
            profile["mira"] = max(0, safe_int(profile.get("mira"), 0) + change_count)
            if log_uuid:
                processed_log_id_set.add(log_uuid)
                processed_log_ids.append(log_uuid)
            continue
        current_count = safe_int(profile["items"].get(item_id), 0)
        next_count = max(0, current_count + change_count)
        if next_count > 0:
            profile["items"][item_id] = next_count
        else:
            profile["items"].pop(item_id, None)
        if log_uuid:
            processed_log_id_set.add(log_uuid)
            processed_log_ids.append(log_uuid)

    _trim_processed_item_logs(profile)


def apply_item_snapshot(profile, usr_item, get_text, get_int):
    if profile is None or usr_item is None:
        return

    profile.setdefault("items", {})
    if not isinstance(profile["items"], dict):
        profile["items"] = {}
    for item in usr_item.findall("item"):
        item_id = str(get_text(item, "item_id") or "").strip()
        if not item_id:
            continue
        count = max(0, get_int(item, "count"))
        if item_id == "money.mira":
            profile["mira"] = count
            continue
        if item_id.startswith("chart."):
            continue
        if count > 0:
            profile["items"][item_id] = count
        else:
            profile["items"].pop(item_id, None)


def apply_uploaded_item_data(player_data_node):
    usr_id = safe_int(player_data_node.findtext("usr_id"), 0)
    if usr_id <= 0:
        return None

    profile = get_profile(usr_id=usr_id)
    if not profile:
        return None

    def get_text(node, tag, default=""):
        child = node.find(tag)
        return child.text if child is not None else default

    def get_int(node, tag, default=0):
        child = node.find(tag)
        if child is None or not child.text:
            return default
        return safe_int(child.text, default)

    apply_item_change_log(profile, player_data_node.find("usr_item_change_log"), get_text, get_int)
    apply_item_snapshot(profile, player_data_node.find("usr_item"), get_text, get_int)
    save_profile(profile)
    return profile


def grant_character_card(profile, card_id):
    if not profile:
        return
    profile.setdefault("character_cards", [])
    profile["character_cards"].append({
        "index": str(uuid.uuid4()),
        "item_id": f"chara_card.{card_id}",
        "card_limit_over_count": 0,
        "character_card_exp": 0,
        "character_card_skill_exp": 0,
        "additional_skills": [],
        "is_favorite": False,
        "source": 0,
        "created_at": now_date_string(),
    })
    save_profile(profile)
