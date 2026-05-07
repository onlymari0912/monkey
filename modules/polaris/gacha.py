import csv
import json
import random
import time
import uuid
from fastapi import APIRouter, Request, Response
from core_common import core_process_request, core_prepare_response, E
from modules.polaris import player
from modules.polaris import usr as polaris_usr
from pathlib import Path

router = APIRouter(prefix="/polaris/gacha", tags=["gacha"])

DATA_DIR = Path(__file__).resolve().parent / "data"
CARDS_DATA_PATH = DATA_DIR / "cards.csv"
GACHA_OPTIONS_PATH = DATA_DIR / "gacha_options.json"
GACHA_CONTENT_TYPE_FILES = (
    ("Contenter", DATA_DIR / "gacha_contenter.json"),
    ("Snapshot", DATA_DIR / "gacha_snapshot.json"),
)

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

# 클라이언트 DTO는 새로 추가하지 않고 기존 get_gacha_info.payment_type 필드를 사용한다.
# JSON 설정: ["credit", "paseli", "item"] 문자열 배열
# 클라이언트로 보낼 때 기존 GachaPaymentMethodFlags 비트마스크로 변환한다.
# 0(fallback): 클라이언트 bundle 값
# -1: 비활성
# 2: Credit
# 4: Paseli
# 8: Item.
GACHA_PAYMENT_TYPE_FALLBACK_TO_CLIENT = 0
GACHA_PAYMENT_TYPE_NONE = -1
GACHA_PAYMENT_TYPE_FLAGS = {
    "credit": 2,
    "paseli": 4,
    "item": 8,
}

def _load_cards():
    with CARDS_DATA_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        return tuple(
            {
                "card_id": row["Id"],
                "name": row["Name"],
                "name_kr": row["NameKr"],
                "card_name": row["CardName"],
                "rarity": row["Rarity"],
                "type": row["Type"],
            }
            for row in csv.DictReader(f)
        )

CHARACTER_CARDS = _load_cards()
CHARACTER_CARD_BY_ID = {
    card["card_id"]: card
    for card in CHARACTER_CARDS
    if card.get("card_id")
}

def _load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _load_gacha_entries():
    entries = []
    for category, path in GACHA_CONTENT_TYPE_FILES:
        data = _load_json(path)
        for entry in data or []:
            if not isinstance(entry, dict):
                continue
            entries.append(dict(entry, category=category))
    return tuple(entries)

GACHA_OPTIONS = _load_json(GACHA_OPTIONS_PATH)
GACHA_ENTRIES = _load_gacha_entries()
GACHA_RARITY_WEIGHTS_BY_CATEGORY = GACHA_OPTIONS["rarity_weights_by_category"]
for rarity_weights in GACHA_RARITY_WEIGHTS_BY_CATEGORY.values():
    for rarity in ("R", "SR", "SSR"):
        rarity_weights[rarity] = int(rarity_weights[rarity])
GACHA_BY_ID = {
    int(entry["id"]): entry
    for entry in GACHA_ENTRIES
    if str(entry.get("id", "")).isdigit()
}
DEFAULT_GACHA_ID = next(iter(GACHA_BY_ID), 0)
GACHA_PICKUP_RATE_PERCENT = min(1000, max(0, int(GACHA_OPTIONS["pickup_rate_percent"])))

def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def _get_consume_item(entry):
    consume_item = entry.get("consume_item") or {}
    if not isinstance(consume_item, dict):
        return "", 0
    return (
        str(consume_item.get("id") or "").strip(),
        max(0, _safe_int(consume_item.get("count"), 0)),
    )

def _get_gacha_payment_type(entry):
    payment_type = entry.get("payment_type", "fallback")
    if isinstance(payment_type, int):
        return payment_type
    if isinstance(payment_type, list):
        flags = 0
        for token in [str(value).strip().lower() for value in payment_type]:
            if token in GACHA_PAYMENT_TYPE_FLAGS:
                flags |= GACHA_PAYMENT_TYPE_FLAGS[token]
        return flags
    else:
        return GACHA_PAYMENT_TYPE_FALLBACK_TO_CLIENT

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
        consume_item_id, consume_item_count = _get_consume_item(entry)
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
                E.payment_type(_get_gacha_payment_type(entry), __type="s32"),
                E.prob_weight_r(_safe_int(rarity_weights.get("R"), 0), __type="s32"),
                E.prob_weight_sr(_safe_int(rarity_weights.get("SR"), 0), __type="s32"),
                E.prob_weight_ssr(_safe_int(rarity_weights.get("SSR"), 0), __type="s32"),
                E.prob_weight_pickup(prob_weight_pickup, __type="s32"),
                E.guarantee_serial_limit(0, __type="s32"),
                E.gacha_consume_item_id(consume_item_id, __type="str"),
                E.gacha_consume_item_count(consume_item_count, __type="s32"),
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
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    player_data = root.find("player_data")
    if player_data is not None:
        player.apply_uploaded_item_data(player_data)
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
    entry = GACHA_BY_ID[gacha_id]
    profile = player.get_profile(usr_id=usr_id) if usr_id > 0 else None
    required_item_id, required_count = _get_consume_item(entry)
    if required_item_id and required_count > 0:
        profile_items = profile.setdefault("items", {}) if profile else {}
        if not isinstance(profile_items, dict):
            profile_items = {}
            profile["items"] = profile_items

        profile_count = _safe_int(profile_items.get(required_item_id), 0)
        short_balance = (
            not profile
            or profile_count < required_count
        )
        if short_balance:
            response = E.response(
                E.gacha(
                    E.gacha_result(
                        E.items(),
                        E.item_counts(),
                        E.item_params(),
                        E.error(
                            E.code(1, __type="s32"),
                            E.message("Item short balance", __type="str")
                        ),
                    ),
                    polaris_usr._build_profile_data_node(profile, E.player_data),
                )
            )
            response_body, response_headers = await core_prepare_response(request, response)
            return Response(content=response_body, headers=response_headers)

        next_count = profile_count - required_count
        if next_count > 0:
            profile_items[required_item_id] = next_count
        else:
            profile_items.pop(required_item_id, None)

    card = draw_gacha_card(gacha_id)
    card_id = card["card_id"]
    player.grant_character_card(profile, card_id)
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
