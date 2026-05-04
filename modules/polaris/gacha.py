from fastapi import APIRouter, Request, Response
from core_common import core_process_request, core_prepare_response, E
from core_database import get_db
from datetime import datetime, timezone
from modules.polaris import usr as polaris_usr
from pathlib import Path
from tinydb import where
import json
import random
import time
import uuid

router = APIRouter(prefix="/polaris/gacha", tags=["gacha"])
router.model_whitelist = ["LAV", "XIF"]

CHARACTER_DATA_PATH = Path(__file__).resolve().parent / "data" / "character.json"
GACHA_DATA_PATH = Path(__file__).resolve().parent / "data" / "gacha.json"

GACHA_TRANSACTIONS = {}

GACHA_RARITY_TYPES = {
    "N": 0,
    "R": 1,
    "SR": 2,
    "SSR": 3,
}
GACHA_RESULT_RARITY_PARAMS = {
    "N": "1",
    "R": "2",
    "SR": "3",
    "SSR": "4",
}

with CHARACTER_DATA_PATH.open("r", encoding="utf-8") as f:
    CHARACTER_CARDS = tuple(
        dict(entry, type=card_type)
        for card_type, entries in json.load(f).items()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    )
CHARACTER_CARD_BY_ID = {
    card["card_id"]: card
    for card in CHARACTER_CARDS
    if card.get("card_id")
}

with GACHA_DATA_PATH.open("r", encoding="utf-8") as f:
    GACHA_DATA = json.load(f)
GACHA_ENTRIES = tuple(GACHA_DATA["gachas"])
GACHA_RARITY_WEIGHTS_BY_CATEGORY = GACHA_DATA["rarity_weights_by_category"]
for entry in GACHA_ENTRIES:
    rarity_weights = GACHA_RARITY_WEIGHTS_BY_CATEGORY[entry["category"]]
    for rarity in ("R", "SR", "SSR"):
        rarity_weights[rarity] = int(rarity_weights[rarity])
GACHA_BY_ID = {
    int(entry["id"]): entry
    for entry in GACHA_ENTRIES
    if str(entry.get("id", "")).isdigit()
}
DEFAULT_GACHA_ID = next(iter(GACHA_BY_ID), 0)
GACHA_PICKUP_RATE_PERCENT = min(1000, max(0, int(GACHA_DATA["pickup_rate_percent"])))

def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def draw_gacha_card(gacha_id):
    entry = GACHA_BY_ID.get(gacha_id)
    drawable_cards = [
        CHARACTER_CARD_BY_ID[card_id]
        for card_id in entry.get("items", [])
        if card_id in CHARACTER_CARD_BY_ID
    ] if entry else []
    if not drawable_cards:
        category = entry.get("category", "Contenter") if entry else "Contenter"
        return {
            "card_id": "00010001" if category == "Snapshot" else "00060001",
            "rarity": "R",
        }

    rarity_weights = GACHA_RARITY_WEIGHTS_BY_CATEGORY[entry["category"]]
    buckets = {
        rarity: [card for card in drawable_cards if card.get("rarity") == rarity]
        for rarity in rarity_weights
    }
    weighted_rarities = [
        (rarity, weight)
        for rarity, weight in rarity_weights.items()
        if weight > 0 and buckets.get(rarity)
    ]
    if weighted_rarities:
        total_weight = sum(weight for _, weight in weighted_rarities)
        threshold = random.uniform(0, total_weight)
        cumulative = 0
        for rarity, weight in weighted_rarities:
            cumulative += weight
            if threshold <= cumulative:
                drawable_cards = buckets[rarity]
                break
        else:
            drawable_cards = buckets[weighted_rarities[-1][0]]

    pickup_ids = set(entry.get("pickups", []))
    pickup_cards = [card for card in drawable_cards if card["card_id"] in pickup_ids]
    regular_cards = [card for card in drawable_cards if card["card_id"] not in pickup_ids]
    if pickup_cards and regular_cards:
        return random.choice(
            pickup_cards
            if random.uniform(0, 100) < (GACHA_PICKUP_RATE_PERCENT / 10)
            else regular_cards
        )
    return random.choice(drawable_cards)

def grant_gacha_card(profile, card_id):
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
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    })
    get_db().table("polaris_profile").upsert(profile, where("usr_id") == profile["usr_id"])


@router.post("")
@router.post("/")
@router.post("/{path:path}")
async def polaris_gacha_dispatch(request: Request):
    try:
        request_info = await core_process_request(request)
        method = request_info["method"]
        if method == "get": method = "get_gacha_info" # Mapping for gacha.get -> get_gacha_info (if applicable)

        func_name = f"polaris_gacha_{method}"
        if func_name in globals():
            return await globals()[func_name](request)
        else:
            print(f"GACHA Dispatch: Function {func_name} not found")
            return Response(status_code=404)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        with open("debug_log.txt", "a") as f:
            f.write(f"\n[GACHA] {traceback.format_exc()}")
        return Response(status_code=500)

async def polaris_gacha_get_gacha_info(request: Request):
    await core_process_request(request)
    gacha_nodes = []
    for entry in GACHA_ENTRIES:
        gacha_id = _safe_int(entry.get("id"))
        rarity_weights = GACHA_RARITY_WEIGHTS_BY_CATEGORY[entry["category"]]
        pickup_ids = set(entry.get("pickups", []))
        cards = [
            CHARACTER_CARD_BY_ID[card_id]
            for card_id in entry.get("items", [])
            if card_id in CHARACTER_CARD_BY_ID
        ]
        pickup_count = sum(1 for card in cards if card["card_id"] in pickup_ids)
        regular_count = len(cards) - pickup_count
        pickup_rate = GACHA_PICKUP_RATE_PERCENT / 10
        prob_weight_pickup = 1
        if pickup_count > 0 and regular_count > 0 and 0 < pickup_rate < 100:
            prob_weight_pickup = max(
                1,
                round((pickup_rate * regular_count) / (pickup_count * (100 - pickup_rate))),
            )
        elif pickup_count > 0 and pickup_rate >= 100:
            prob_weight_pickup = 1000000
        gacha_nodes.append(
            E.gacha(
                E.gacha_id(gacha_id, __type="s32"),
                E.name(f"{entry.get('category', 'Gacha')} Hunt {gacha_id}", __type="str"),
                E.payment_type(0, __type="s32"),
                E.prob_weight_r(_safe_int(rarity_weights.get("R"), 0), __type="s32"),
                E.prob_weight_sr(_safe_int(rarity_weights.get("SR"), 0), __type="s32"),
                E.prob_weight_ssr(_safe_int(rarity_weights.get("SSR"), 0), __type="s32"),
                E.prob_weight_pickup(prob_weight_pickup, __type="s32"),
                E.guarantee_serial_limit(0, __type="s32"),
                E.gacha_consume_item_id(str(entry.get("consume_item_id") or "money.mira"), __type="str"),
                E.gacha_consume_item_count(_safe_int(entry.get("consume_item_count"), 1000), __type="s32"),
                E.open_at("2026-01-01 00:00:00", __type="str"),
                E.close_at("2040-12-31 14:59:59", __type="str"),
                E.start_softcode("0000000000", __type="str"),
                E.end_softcode("9999999999", __type="str"),
                E.drawable_item_type(0, __type="s32"),
                E.items(*[
                    E.item(
                        E.item_id(f"chara_card.{card['card_id']}", __type="str"),
                        E.rarity_type(GACHA_RARITY_TYPES.get(card.get("rarity", ""), 0), __type="s32"),
                        E.is_pickup(1 if card["card_id"] in pickup_ids else 0, __type="s32"),
                    )
                    for card in cards
                ]),
            )
        )

    response = E.response(
        E.gacha(
            E.gacha_list(*gacha_nodes)
        )
    )
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_gacha_begin_gacha(request: Request):
    await core_process_request(request)
    response = E.response(
        E.gacha(
            E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str"),
            E.transaction_id(str(uuid.uuid4()), __type="str"),
            E.error(
                E.code(0, __type="s32"),
                E.message("", __type="str")
            )
        )
    ) 
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_gacha_draw_gacha(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    transaction_id = str(root.findtext("transaction_id") or "").strip()
    gacha_id = _safe_int(root.findtext("gacha_id"), DEFAULT_GACHA_ID)
    print(f"\uAC00\uCC60 \uC644\uB8CC: {gacha_id}")
    if transaction_id:
        GACHA_TRANSACTIONS[transaction_id] = gacha_id
    response = E.response(
        E.gacha(
            E.error(
                E.code(0, __type="s32"),
                E.message("", __type="str")
            )
        )
    )
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_gacha_end_gacha(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    usr_id = _safe_int(root.findtext("usr_id"), 0)
    transaction_id = str(root.findtext("transaction_id") or "").strip()
    gacha_id = GACHA_TRANSACTIONS.pop(transaction_id, DEFAULT_GACHA_ID)
    if gacha_id not in GACHA_BY_ID:
        gacha_id = DEFAULT_GACHA_ID
    card = draw_gacha_card(gacha_id)
    card_id = card["card_id"]
    profile = (
        get_db().table("polaris_profile").get(where("usr_id") == usr_id)
        if usr_id > 0
        else None
    )
    grant_gacha_card(profile, card_id)
    response = E.response(
        E.gacha(
            E.gacha_result(
                E.items(
                    E.item(f"chara_card.{card_id}", __type="str"),
                ),
                E.item_counts(
                    E.count(1, __type="s32"),
                ),
                E.item_params(
                    E.param(GACHA_RESULT_RARITY_PARAMS.get(card.get("rarity", ""), ""), __type="str"),
                ),
                E.error(
                    E.code(0, __type="s32"),
                    E.message("", __type="str")
                ),
            ),
            polaris_usr._build_profile_data_node(profile, E.player_data),
        )
    )
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
