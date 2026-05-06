from fastapi import APIRouter, Request, Response
from datetime import datetime, timezone
from core_common import core_process_request, core_prepare_response, E
from core_database import get_db
from modules.polaris import player
from pathlib import Path
from tinydb import where
import json
import time

router = APIRouter(prefix="/polaris/usr", tags=["usr"])

with (Path(__file__).resolve().parent / "data" / "music.json").open(encoding="utf-8") as fp:
    MUSIC_CHARTS = tuple(
        (int(music_id), int(diff))
        for music_id, diffs in json.load(fp)
        for diff in diffs
    )

@router.post("")
@router.post("/")
@router.post("/{path:path}")
async def polaris_usr_dispatch(request: Request):
    try:
        request_info = await core_process_request(request)
        method = request_info["method"]

        func_name = f"polaris_usr_{method}"
        if func_name in globals():
            return await globals()[func_name](request)
        else:
            print(f"USR Dispatch: Function {func_name} not found")
            return Response(status_code=404)
    except Exception as e:
        import traceback
        with open("debug_log.txt", "w") as f:
            f.write(traceback.format_exc())
            f.write(f"\nLast known step: Dispatcher Failed")
        print(traceback.format_exc())
        return Response(status_code=500)

def _extract_usr_identity(root):
    dataid = ""
    refid = ""
    name = "PLAYER"
    pin = ""

    if len(root) > 0:
        usr_node = root[0]
        for child in usr_node:
            text = str(child.text or "").strip()
            if child.tag == "data_id":
                dataid = text
            elif child.tag == "ref_id":
                refid = text
            elif child.tag == "usr_name":
                name = text or "PLAYER"
            elif child.tag == "pin":
                pin = text

    if not dataid:
        dataid = str(root.attrib.get("srcid") or "").strip()

    return dataid, refid, name, pin

def _ensure_signup_profile(profile, dataid, refid, name, pin=""):
    return player.ensure_signup_profile(profile, dataid, refid, name, pin)

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

async def polaris_usr_sign_up(request: Request):
    try:
        request_info = await core_process_request(request)
        root = request_info["root"]

        dataid, refid, name, pin = _extract_usr_identity(root)
        if not dataid:
            print("polaris_usr_sign_up: Error - data_id missing")
            return Response(status_code=400)

        print(f"polaris_usr_sign_up: Processing signup for card='{dataid}' name='{name}'")

        profile = _ensure_signup_profile(player.get_profile(dataid, refid), dataid, refid, name, pin)
        player.save_profile(profile, fallback_card=dataid)
        print(f"polaris_usr_sign_up: Saved. usr_id={profile['usr_id']}")

        # Response (Matches SignUpModeler.cs)
        response = E.response(
            E.usr(
                E.usr_id(int(profile["usr_id"]), __type="s32"),
                E.crew_id(str(profile["crew_id"]), __type="str")
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    except Exception:
        import traceback
        print(traceback.format_exc())
        return Response(status_code=500)

def _build_profile_data_node(p, node_factory=E.usr):
    if not p:
        return node_factory(
            E.result(1, __type="s32"),
            E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str"),
        )
    # --- Data Preparation & Corrections ---

    # 1. Sync Tutorial Flags
    # Client needs tutorial_skipped=1 if cleared, but never sends it. Explicitly sync.
    # Also sync Gacha Ticket as it often signals tutorial completion.

    def safe_bool(k):
        val = p.get(k)
        if str(val).lower() == "true": return 1
        try: return 1 if int(val or 0) else 0
        except: return 0

    p_cleared = safe_bool("is_tutorial_cleared")
    p_skipped = safe_bool("tutorial_skipped")
    p_gacha = safe_bool("gacha_ticket_received")

    is_cleared = 1 if (p_cleared or p_gacha) else 0

    # 3. Safe Sub-Object Access (Moved UP for calculation)
    play_info = p.get("play_info") or {}
    main_opt = p.get("main_option") or {}
    counts = p.get("counts") or {}
    action_counts = p.get("action_counts") or {}
    privacy = p.get("privacy") or {}
    nametag = p.get("nametag") or {}
    sort_setting = p.get("sort_setting") or {}
    name_titles = p.get("name_titles") or []
    items = p.get("items") or {}
    music_missions = p.get("music_missions") or []
    pa_skill_data = p.get("pa_skill") or {}
    mira_balance = max(0, safe_int(p["mira"], 0))
    item_nodes = [
        E.item(
            E.item_id("money.mira", __type="str"),
            E.count(mira_balance, __type="s32"),
            E.income(0, __type="s32"),
            E.expense(0, __type="s32")
        )
    ]
    if isinstance(items, dict):
        for item_id, count in items.items():
            item_id = str(item_id or "").strip()
            count = safe_int(count, 0)
            if not item_id or item_id == "money.mira" or count <= 0:
                continue
            item_nodes.append(
                E.item(
                    E.item_id(item_id, __type="str"),
                    E.count(count, __type="s32"),
                    E.income(0, __type="s32"),
                    E.expense(0, __type="s32")
                )
            )
    item_nodes.extend(
        E.item(
            E.item_id(f"chart.{music_id}.{diff}", __type="str"),
            E.count(1, __type="s32"),
            E.income(0, __type="s32"),
            E.expense(0, __type="s32")
        )
        for music_id, diff in MUSIC_CHARTS
    )

    # Helper for Play Info (Strict Types & Crash Proof)
    def pi_s(k, d=""): return str(play_info.get(k, d))
    def pi_i(k, d=0):
        val = play_info.get(k, d)
        try: return int(val or 0)
        except: return 1 if str(val).lower() == "true" else 0

    is_skipped = 1 if p_skipped else 0
    gacha_ticket = 1 if p_gacha else 0

    # Helper for Main Option
    def mo_s(k, d=""): return str(main_opt.get(k, d))
    def mo_i(k, d=0):
        val = main_opt.get(k, d)
        try: return int(val or 0)
        except: return 1 if str(val).lower() == "true" else 0
    def mo_b(k, d=0):
        val = main_opt.get(k, d)
        if str(val).lower() == "true": return True
        try: return bool(int(val or 0))
        except: return False
    def pv_i(k, d=0):
        val = privacy.get(k, d)
        try: return int(val or 0)
        except: return 1 if str(val).lower() == "true" else 0
    def ss_i(k, d=0):
        val = sort_setting.get(k, d)
        try: return int(val or 0)
        except: return 0
    def ss_positive(k, d):
        value = ss_i(k, d)
        return value if value > 0 else d

    music_mission_nodes = []
    for mission in music_missions:
        if not isinstance(mission, dict):
            continue
        chart_id = safe_int(mission.get("chart_id", 0), 0)
        if chart_id <= 0:
            continue
        music_mission_nodes.append(
            E.music_mission(
                E.chart_id(chart_id, __type="s32"),
                E.achievements(safe_int(mission.get("achievements", 0), 0), __type="s32"),
            )
        )

    pa_skill_children = [
        E.pa_skill_history(
            *[
                E.data(safe_int(value, 0), __type="s64")
                for value in (pa_skill_data.get("pa_skill_history") or [])
            ]
        ),
        E.pa_skill_history_index(safe_int(pa_skill_data.get("pa_skill_history_index", 0), 0), __type="s32"),
        E.skill(safe_int(pa_skill_data.get("skill", 0), 0), __type="s32"),
    ]
    pa_skill_chart_nodes = []
    for chart in pa_skill_data.get("charts") or []:
        if not isinstance(chart, dict):
            continue
        pa_skill_chart_nodes.append(
            E.chart(
                E.rank(safe_int(chart.get("rank", 0), 0), __type="s32"),
                E.music_id(safe_int(chart.get("music_id", 0), 0), __type="s32"),
                E.chart_difficulty_type(safe_int(chart.get("chart_difficulty_type", 0), 0), __type="s32"),
                E.skill(safe_int(chart.get("skill", 0), 0), __type="s32"),
            )
        )
    if pa_skill_chart_nodes:
        pa_skill_children.append(E.charts(*pa_skill_chart_nodes))

    character_nodes = []
    for item in p.get("characters", []) or []:
        entry = player.normalize_usr_character_entry(item)
        if entry is None:
            continue
        character_nodes.append(
            E.chara(
                E.chara_id(entry["chara_id"], __type="str"),
                E.closeness(entry["closeness"], __type="s32"),
                E.home_touch_count(entry["home_touch_count"], __type="s32"),
            )
        )

    character_card_nodes = []
    for card in p.get("character_cards", []) or []:
        if not isinstance(card, dict):
            continue
        additional_skills = card.get("additional_skills") or []
        if not isinstance(additional_skills, list):
            additional_skills = []
        character_card_nodes.append(
            E.card(
                E.index(str(card.get("index") or ""), __type="str"),
                E.item_id(str(card.get("item_id") or ""), __type="str"),
                E.card_limit_over_count(safe_int(card.get("card_limit_over_count"), 0), __type="s32"),
                E.character_card_exp(safe_int(card.get("character_card_exp"), 0), __type="s32"),
                E.character_card_skill_exp(safe_int(card.get("character_card_skill_exp"), 0), __type="s32"),
                E.additional_skills(*[
                    E.skill_id(str(skill_id), __type="str")
                    for skill_id in additional_skills
                ]),
                E.is_favorite(bool(safe_bool(card.get("is_favorite", False))), __type="bool"),
                E.source(safe_int(card.get("source"), 0), __type="s32"),
                E.deleted(False, __type="bool"),
                E.created_at(str(card.get("created_at") or now_date_string()), __type="str"),
            )
        )

    def deck_bool(value):
        if isinstance(value, bool):
            return value
        try:
            return bool(int(value))
        except Exception:
            return str(value).lower() == "true"

    deck_nodes = []
    for saved_deck in p.get("decks", []) or []:
        saved_deck = saved_deck if isinstance(saved_deck, dict) else {}
        if any(
            not str(saved_deck.get(key) or "").strip()
            for key in (
                "contenter_index",
                "supportsnap1_index",
                "supportsnap2_index",
                "supportsnap3_index",
                "supportsnap4_index",
            )
        ):
            continue
        deck_nodes.append(
            E.deck(
                E.deck_number(safe_int(saved_deck.get("deck_number", 1), 1), __type="s32"),
                E.is_main(deck_bool(saved_deck.get("is_main")), __type="bool"),
                E.is_select(deck_bool(saved_deck.get("is_select")), __type="bool"),
                E.deck_name(saved_deck.get("deck_name") or "", __type="str"),
                E.contenter_index(saved_deck.get("contenter_index") or "", __type="str"),
                E.supportsnap1_index(saved_deck.get("supportsnap1_index") or "", __type="str"),
                E.supportsnap2_index(saved_deck.get("supportsnap2_index") or "", __type="str"),
                E.supportsnap3_index(saved_deck.get("supportsnap3_index") or "", __type="str"),
                E.supportsnap4_index(saved_deck.get("supportsnap4_index") or "", __type="str"),
                E.frame_id(saved_deck.get("frame_id") or "", __type="str"),
                E.pose_id(saved_deck.get("pose_id") or "", __type="str"),
                E.another_costume_id(saved_deck.get("another_costume_id") or "", __type="str"),
            )
        )

    return node_factory(
        E.result(0, __type="s32"),
        E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str"),
        E.usr_id(int(p.get("usr_id", 0)), __type="s32"),
        E.crew_id(str(p.get("crew_id", "0")), __type="str"),
        E.gacha_ticket_received(gacha_ticket, __type="s32"),
        E.tutorial_skipped(is_skipped, __type="s32"),
        E.usr_profile(
            E.usr_name(str(p.get("name", "PLAYER")), __type="str"),
            E.usr_rank(int(p.get("rank", 1)), __type="s32"),
            E.exp(int(p.get("exp", 0)), __type="s32"),
            E.comment(str(p.get("comment", "")), __type="str"),
            E.is_tutorial_cleared(bool(is_cleared), __type="bool"),
        ),
        E.usr_play_info(
            E.softcode(pi_s("softcode"), __type="str"),
            E.asset_version(pi_i("asset_version"), __type="s32"),
            E.start_date(pi_s("start_date"), __type="str"),
            E.end_date(pi_s("end_date"), __type="str"),
            E.play_days(pi_i("play_days"), __type="s32"),
            E.consecutive_days(pi_i("consecutive_days"), __type="s32"),
            E.consecutive_weeks(pi_i("consecutive_weeks"), __type="s32"),
            E.last_play_week(pi_s("last_play_week"), __type="str"),
            E.today_play_count(pi_i("today_play_count"), __type="s32"),
            E.mode_id(pi_i("mode_id"), __type="s32"),
            E.music_id(pi_i("music_id", 3), __type="s32"),
            E.folder_id(pi_i("folder_id", 1), __type="s32"),
            E.chart_difficulty_type(pi_i("chart_difficulty_type"), __type="s32"),
            E.pcb_id(pi_s("pcb_id"), __type="str"),
            E.loc_id(pi_s("loc_id"), __type="str"),
            E.shop_name(pi_s("shop_name"), __type="str"),
            E.beginner_play_count(pi_i("beginner_play_count"), __type="s32"),
            E.standard_play_count(pi_i("standard_play_count"), __type="s32"),
            E.freetime4_play_count(pi_i("freetime4_play_count"), __type="s32"),
            E.freetime6_play_count(pi_i("freetime6_play_count"), __type="s32"),
            E.freetime8_play_count(pi_i("freetime8_play_count"), __type="s32"),
            E.freetime12_play_count(pi_i("freetime12_play_count"), __type="s32"),
            E.local_matching_play_count(pi_i("local_matching_play_count"), __type="s32"),
            E.global_matching_play_count(pi_i("global_matching_play_count"), __type="s32"),
            E.freetime_play_count(pi_i("freetime_play_count"), __type="s32"),
            E.freetime_play_total_time(pi_i("freetime_play_total_time"), __type="s32"),
        ),
        E.usr_main_option(
            E.notes_design_type(mo_i("notes_design_type"), __type="s32"),
            E.tap_se_type(mo_i("tap_se_type"), __type="s32"),
            E.tap_effect_type(mo_i("tap_effect_type"), __type="s32"),
            E.right_fader_color(mo_i("right_fader_color"), __type="s32"),
            E.left_fader_color(mo_i("left_fader_color"), __type="s32"),
            E.chart_option(mo_i("chart_option"), __type="s32"),
            E.high_speed(mo_i("high_speed"), __type="s32"),
            E.notes_display_timing(mo_i("notes_display_timing"), __type="s32"),
            E.judge_timing(mo_i("judge_timing"), __type="s32"),
            E.judge_display_position(mo_i("judge_display_position"), __type="s32"),
            E.display_fast_slow(mo_i("display_fast_slow"), __type="s32"),
            E.lane_alpha(mo_i("lane_alpha"), __type="s32"),
            E.movie_brightness(mo_i("movie_brightness"), __type="s32"),
            E.skill_cut_in(mo_i("skill_cut_in"), __type="s32"),
            E.is_voice_active(mo_b("is_voice_active"), __type="bool"),
            E.combo_special_display(mo_i("combo_special_display"), __type="s32"),
            E.music_volume(mo_i("music_volume"), __type="s32"),
            E.se_volume(mo_i("se_volume"), __type="s32"),
            E.voice_volume(mo_i("voice_volume"), __type="s32"),
            E.out_game_music_volume(mo_i("out_game_music_volume"), __type="s32"),
            E.out_game_se_volume(mo_i("out_game_se_volume"), __type="s32"),
            E.out_game_voice_volume(mo_i("out_game_voice_volume"), __type="s32"),
            E.master_volume(mo_i("master_volume"), __type="s32"),
            E.headphone_volume(mo_i("headphone_volume"), __type="s32"),
            E.bass_shaker_volume(mo_i("bass_shaker_volume"), __type="s32"),
            E.force_open_prev_in_game_option(mo_b("force_open_prev_in_game_option"), __type="bool"),
            E.display_bar_line(mo_i("display_bar_line"), __type="s32"),
            E.bga_id(mo_s("bga_id"), __type="str"),
        ),
        E.usr_privacy(
             E.disp_name_to_other(pv_i("disp_name_to_other", 1), __type="s32"),
             E.disp_shop_to_other(pv_i("disp_shop_to_other", 1), __type="s32"),
             E.disp_shop_to_me(pv_i("disp_shop_to_me", 1), __type="s32"),
             E.disp_skill_to_other(pv_i("disp_skill_to_other", 1), __type="s32"),
             E.disp_skill_to_me(pv_i("disp_skill_to_me", 1), __type="s32"),
             E.allow_music_ranking(pv_i("allow_music_ranking", 0), __type="s32"),
             E.allow_pa_skill_ranking(pv_i("allow_pa_skill_ranking", 0), __type="s32"),
        ),
        E.usr_nametag(
             E.nametag_badge1_id(str(nametag.get("nametag_badge1_id", "")), __type="str"),
             E.nametag_badge2_id(str(nametag.get("nametag_badge2_id", "")), __type="str"),
             E.nametag_badge3_id(str(nametag.get("nametag_badge3_id", "")), __type="str"),
             E.nametag_plate_id(str(nametag.get("nametag_plate_id", "nametag.plate.00000000")), __type="str"),
             E.nametag_title_id(str(nametag.get("nametag_title_id", "nametag.title.00000000000")), __type="str"),
             E.set_title_name(str(nametag.get("set_title_name", "")), __type="str"),
             E.set_title_rarity(str(nametag.get("set_title_rarity", "N")), __type="str")
        ),
        E.usr_sort_setting(
             E.musicselect_sort(ss_i("musicselect_sort", 0), __type="s32"),
             E.musicselect_filter(ss_positive("musicselect_filter", 3617), __type="s32"),
             E.musicselect_order(ss_i("musicselect_order", 0), __type="s32"),
             E.character_training_list_sort(ss_i("character_training_list_sort", 0), __type="s32"),
             E.character_training_list_filter(ss_positive("character_training_list_filter", 33554431), __type="s32"),
             E.character_training_list_order(ss_i("character_training_list_order", 0), __type="s32"),
             E.character_replacement_list_sort(ss_positive("character_replacement_list_sort", 1), __type="s32"),
             E.character_replacement_list_filter(ss_positive("character_replacement_list_filter", 33554431), __type="s32"),
             E.character_replacement_list_order(ss_i("character_replacement_list_order", 0), __type="s32"),
             E.character_material_list_sort(ss_positive("character_material_list_sort", 6), __type="s32"),
             E.character_material_list_filter(ss_positive("character_material_list_filter", 33554431), __type="s32"),
             E.character_material_list_order(ss_i("character_material_list_order", 0), __type="s32")
        ),
        E.usr_unlock_music(
            *[ E.music(E.music_id(music_id, __type="s32"), E.chart_difficulty_type(diff, __type="s32"), E.unlock_type(0, __type="s32"))
               for music_id, diff in MUSIC_CHARTS ]
        ),
        E.usr_item(*item_nodes),
        E.usr_name_titles(
            *[
                E.title(str(title), __type="str")
                for title in name_titles
                if title is not None and str(title) != ""
            ]
        ),
        E.usr_deck(*deck_nodes),
        E.usr_character_card(*character_card_nodes),
        E.usr_character(*character_nodes),
        E.usr_login_bonus(),
        E.usr_music_mission(*music_mission_nodes),
        E.usr_extend_music_mission(),
        E.usr_count(
            *[ E.count(E.key(k, __type="str"), E.value(int(v), __type="s32")) for k, v in counts.items() ]
        ),
        E.usr_chatstamp(),
        E.usr_action_count(
            *[
                E.action_count(
                    E.key(str(k), __type="str"),
                    E.count(safe_int(v, 0), __type="s32"),
                )
                for k, v in action_counts.items()
                if k is not None and str(k) != ""
            ]
        ),
        E.pa_skill(*pa_skill_children)
    )

async def polaris_usr_get(request: Request):
    try:
        request_info = await core_process_request(request)
        root = request_info["root"]
        dataid, refid, _, _ = _extract_usr_identity(root)

        p = player.get_profile(dataid, refid)

        if not p or not p.get("name"):
            print(f"polaris_usr_get: Profile NOT found for card={dataid} refid={refid}")
            response = E.response(E.usr(E.result(1, __type="s32")))
            response_body, response_headers = await core_prepare_response(request, response)
            return Response(content=response_body, headers=response_headers)

        print(f"polaris_usr_get: Generating response for {p.get('name')}")

        response = E.response(_build_profile_data_node(p, E.usr))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        with open("debug_log.txt", "w") as f:
            f.write(traceback.format_exc())
            f.write(f"\nLast known step: Response Generation Failed")
        return Response(status_code=500)

async def polaris_usr_save(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]

    try:
        usr_id = int(root.find("usr_id").text)
        print(f"polaris_usr_save: usr_id={usr_id}")
    except Exception as e:
        print(f"polaris_usr_save: Failed to get usr_id: {e}")
        return Response(status_code=400)

    profile = player.get_profile(usr_id=usr_id)

    if profile:
        print(f"polaris_usr_save: Profile found for usr_id={usr_id}")
        profile["mira"] = max(0, safe_int(profile["mira"], 0))

        # Helper to extract text safely
        def get_text(node, tag, default=""):
            child = node.find(tag)
            return child.text if child is not None else default

        def get_int(node, tag, default=0):
            child = node.find(tag)
            if child is None or not child.text: return default
            try:
                return int(child.text)
            except ValueError:
                return default

        def get_bool(node, tag, default=0):
            child = node.find(tag)
            if child is None or not child.text: return default
            val = child.text.lower()
            if val in ["true", "1"]: return 1
            if val in ["false", "0"]: return 0
            try: return int(val)
            except: return default

        # --- Update Root Fields ---
        # tutorial_skipped is NOT in SavePlayData schema, so we can't read it here.
        # But gacha_ticket_received IS.
        if root.find("gacha_ticket_received") is not None:
            profile["gacha_ticket_received"] = get_int(root, "gacha_ticket_received")

        # --- Update Profile ---
        usr_profile = root.find("usr_profile")
        if usr_profile is not None:
            if usr_profile.find("usr_name") is not None:
                profile["name"] = get_text(usr_profile, "usr_name")
            if usr_profile.find("is_tutorial_cleared") is not None:
                val = get_bool(usr_profile, "is_tutorial_cleared")
                profile["is_tutorial_cleared"] = val
                print(f"polaris_usr_save: Updated is_tutorial_cleared={val}")

            if usr_profile.find("usr_rank") is not None: profile["rank"] = get_int(usr_profile, "usr_rank")
            if usr_profile.find("exp") is not None: profile["exp"] = get_int(usr_profile, "exp")
            if usr_profile.find("comment") is not None: profile["comment"] = get_text(usr_profile, "comment")

        # --- Update Play Info ---
        usr_play_info = root.find("usr_play_info")
        if usr_play_info is not None:
            profile.setdefault("play_info", {})
            pi = profile["play_info"]

            # Non-counter fields: Update directly
            pi["softcode"] = get_text(usr_play_info, "softcode")
            pi["asset_version"] = get_int(usr_play_info, "asset_version")
            pi["start_date"] = get_text(usr_play_info, "start_date")
            pi["end_date"] = get_text(usr_play_info, "end_date")
            pi["play_days"] = get_int(usr_play_info, "play_days")
            pi["consecutive_days"] = get_int(usr_play_info, "consecutive_days")
            pi["consecutive_weeks"] = get_int(usr_play_info, "consecutive_weeks")
            pi["last_play_week"] = get_text(usr_play_info, "last_play_week")
            pi["today_play_count"] = get_int(usr_play_info, "today_play_count")

            pi["mode_id"] = get_int(usr_play_info, "mode_id")
            pi["boost_item_id"] = get_int(usr_play_info, "boost_item_id")
            pi["music_id"] = get_int(usr_play_info, "music_id")
            pi["folder_id"] = get_int(usr_play_info, "folder_id")
            pi["chart_difficulty_type"] = get_int(usr_play_info, "chart_difficulty_type")
            pi["musicselect_difficulty_type"] = get_int(usr_play_info, "musicselect_difficulty_type")
            pi["pcb_id"] = get_text(usr_play_info, "pcb_id")
            pi["loc_id"] = get_text(usr_play_info, "loc_id")
            pi["shop_name"] = get_text(usr_play_info, "shop_name")
            pi["latest_caravan_location_id"] = get_int(usr_play_info, "latest_caravan_location_id")
            pi["latest_caravan_location_type"] = get_int(usr_play_info, "latest_caravan_location_type")
            pi["play_count_on_name_changed"] = get_int(usr_play_info, "play_count_on_name_changed")

            # Helper to update counts monotonically (MAX strategy)
            def update_count(field):
                req_val = get_int(usr_play_info, field)
                old_val = pi.get(field, 0)
                try: old_val = int(old_val)
                except: old_val = 0
                pi[field] = max(req_val, old_val)

            update_count("beginner_play_count")
            update_count("standard_play_count")
            update_count("freetime4_play_count")
            update_count("freetime6_play_count")
            update_count("freetime8_play_count")
            update_count("freetime12_play_count")
            update_count("local_matching_play_count")
            update_count("global_matching_play_count")
            update_count("freetime_play_count")
            # Time is cumulative, safe to take max
            update_count("freetime_play_total_time")

            # [Fix] Apply game_play_count delta from Action Log to specific mode counter
            # If client snapshot was 0 but log says +1, this ensures we count it.
            usr_action_log = root.find("usr_action_count_change_log")
            game_play_delta = 0
            if usr_action_log is not None:
                for action in usr_action_log.findall("action_log"):
                     if get_text(action, "key") == "game_play_count":
                         game_play_delta += get_int(action, "change_count")

            if game_play_delta > 0:
                mid = pi.get("mode_id", 0)
                print(f"polaris_usr_save: ActionLog Delta +{game_play_delta} for Mode {mid}")

                # Mapping ModeID -> Field
                mode_field = None
                if mid == 10: mode_field = "standard_play_count"
                elif mid == 20: mode_field = "freetime6_play_count"
                elif mid == 21: mode_field = "freetime8_play_count"
                elif mid == 23: mode_field = "freetime12_play_count"
                elif mid == 30: mode_field = "local_matching_play_count"
                elif mid == 40: mode_field = "global_matching_play_count"

                def safe_add(field, delta):
                    try: val = int(pi.get(field, 0))
                    except: val = 0
                    pi[field] = val + delta

                if mode_field:
                    safe_add(mode_field, game_play_delta)
                    if mid in [20, 21, 23]:
                        safe_add("freetime_play_count", game_play_delta)

                # Ensure Total count tracks
                # (Client might compute total sum, but we should be consistent)

            print(f"polaris_usr_save: [DEBUG] Play Counts Update -> "
                  f"Std={pi.get('standard_play_count')} "
                  f"F4={pi.get('freetime4_play_count')} "
                  f"F6={pi.get('freetime6_play_count')} "
                  f"F8={pi.get('freetime8_play_count')} "
                  f"F12={pi.get('freetime12_play_count')} "
                  f"TotalTimer={pi.get('freetime_play_count')}")

        # --- Generic Updates ---
        for tag, key in [("usr_main_option", "main_option"),
                         ("usr_privacy", "privacy"),
                         ("usr_nametag", "nametag"),
                         ("usr_sort_setting", "sort_setting")]:
            node = root.find(tag)
            if node is not None:
                profile.setdefault(key, {})
                for child in node:
                    profile[key][child.tag] = child.text

        # --- Update Unlocked Music ---
        usr_unlock_music = root.find("usr_unlock_music")
        if usr_unlock_music is not None:
            profile.setdefault("unlock_music", [])
            current_unlocks = {}
            for u in profile["unlock_music"]:
                if isinstance(u, dict) and "music_id" in u:
                    current_unlocks[u["music_id"]] = u

            for item in usr_unlock_music.findall("music"):
                mid = get_int(item, "music_id")
                entry = {
                    "music_id": mid,
                    "unlock_phase": get_int(item, "unlock_phase"),
                    "can_buying": get_int(item, "can_buying"),
                    "is_new": get_int(item, "is_new"),
                    "use_item_id": get_int(item, "use_item_id"),
                    "use_item_num": get_int(item, "use_item_num")
                }

                if mid in current_unlocks:
                    current_unlocks[mid].update(entry)
                else:
                    profile["unlock_music"].append(entry)
                    current_unlocks[mid] = entry

        # --- Update Items ---
        player.apply_item_change_log(profile, root.find("usr_item_change_log"), get_text, get_int)
        player.apply_item_snapshot(profile, root.find("usr_item"), get_text, get_int)

        usr_name_titles = root.find("usr_name_titles")
        if usr_name_titles is not None:
            profile["name_titles"] = [
                str(title.text).strip()
                for title in usr_name_titles.findall("title")
                if title.text and str(title.text).strip()
            ]

        # --- Update Decks ---
        usr_deck = root.find("usr_deck")
        if usr_deck is not None:
            profile["decks"] = [] # valid strategy to replace full list if client sends all
            for deck in usr_deck.findall("deck"):
                d = {}
                for child in deck:
                    d[child.tag] = child.text
                profile["decks"].append(d)

        # --- Update Character Stats ---
        usr_character = root.find("usr_character")
        if usr_character is not None and len(usr_character) > 0:
            characters = []
            for character in usr_character.findall("chara"):
                entry = player.normalize_usr_character_entry({
                    "chara_id": character.findtext("chara_id"),
                    "closeness": character.findtext("closeness"),
                    "home_touch_count": character.findtext("home_touch_count"),
                })
                if entry is not None:
                    characters.append(entry)
            profile["characters"] = characters

        # --- Update Character Cards ---
        # usr_character_card is a full snapshot when present.
        usr_character_card = root.find("usr_character_card")
        if usr_character_card is not None:
            incoming_cards = []
            created_at_by_index = {
                str(card.get("index", "")).strip(): card.get("created_at")
                for card in profile.get("character_cards", [])
                if isinstance(card, dict)
            }
            for card in usr_character_card.findall("card"):
                index = str(card.findtext("index") or "").strip()
                deleted = card.findtext("deleted")
                if safe_bool(deleted):
                    continue
                incoming_cards.append({
                    "index": index,
                    "item_id": card.findtext("item_id"),
                    "card_limit_over_count": card.findtext("card_limit_over_count"),
                    "character_card_exp": card.findtext("character_card_exp"),
                    "character_card_skill_exp": card.findtext("character_card_skill_exp"),
                    "additional_skills": [
                        str(skill.text).strip()
                        for skill in card.findall("./additional_skills/*")
                        if skill.text and str(skill.text).strip()
                    ],
                    "is_favorite": card.findtext("is_favorite"),
                    "source": card.findtext("source"),
                    "created_at": created_at_by_index.get(index),
                })

            profile["character_cards"] = incoming_cards

        # --- Update Music Missions ---
        usr_music_mission = root.find("usr_music_mission")
        if usr_music_mission is not None:
            profile.setdefault("music_missions", [])
            # Replace or merge? Strategy: Replace usually safe for lists sent in full
            # But XML log shows "usr_music_mission" empty tag?
            # If it has children, parse.
            if len(usr_music_mission) > 0:
                profile["music_missions"] = []
                for mm in usr_music_mission.findall("music_mission"):
                    m = {}
                    for child in mm:
                       m[child.tag] = child.text
                    profile["music_missions"].append(m)

        # --- Update PA Skill ---
        pa_skill = root.find("pa_skill")
        if pa_skill is not None:
            skill_data = {
                "pa_skill_history": [
                    safe_int(data.text, 0)
                    for data in pa_skill.findall("./pa_skill_history/data")
                    if data.text
                ],
                "pa_skill_history_index": get_int(pa_skill, "pa_skill_history_index"),
                "skill": get_int(pa_skill, "skill"),
                "charts": [],
            }
            for chart in pa_skill.findall("./charts/chart"):
                skill_data["charts"].append({
                    "rank": get_int(chart, "rank"),
                    "music_id": get_int(chart, "music_id"),
                    "chart_difficulty_type": get_int(chart, "chart_difficulty_type"),
                    "skill": get_int(chart, "skill"),
                })
            profile["pa_skill"] = skill_data

        # --- Update Action Change Log (Play Counts Logic) ---
        usr_action_log = root.find("usr_action_count_change_log")
        if usr_action_log is not None:
            profile.setdefault("action_logs", [])
            profile.setdefault("action_counts", {})
            new_action_logs = []
            for action in usr_action_log.findall("action_log"):
                action_uuid = get_text(action, "uuid")
                key = get_text(action, "key")
                count = get_int(action, "change_count")
                if not key:
                    continue
                new_action_logs.append({
                    "uuid": action_uuid,
                    "key": key,
                    "change_count": count,
                })

                cur = safe_int(profile["action_counts"].get(key, 0), 0)
                profile["action_counts"][key] = cur + count

                # SPECIAL: game_play_count mapping
                if key == "game_play_count":
                    print(f"polaris_usr_save: ActionLog 'game_play_count' +{count}")
                    # If we need to update standard_play_count in play_info as well?
                    # Usually play_info is the snapshot.
                    # If client sent snapshot 0 but log +1, we trust the log?
                    # But client snapshot should be correct next time if we save generic count.
                    pass
            player.append_unique_entries(profile["action_logs"], new_action_logs)

        usr_max_action_count = root.find("usr_max_action_count")
        if usr_max_action_count is not None:
            profile.setdefault("action_counts", {})
            for action_count in usr_max_action_count.findall("action_count"):
                key = get_text(action_count, "key")
                if not key:
                    continue
                count = get_int(action_count, "count")
                profile["action_counts"][key] = max(
                    safe_int(profile["action_counts"].get(key, 0), 0),
                    count,
                )

        # --- Update Generic Counts ---
        usr_count = root.find("usr_count")
        if usr_count is not None:
            profile.setdefault("counts", {})
            for count_item in usr_count.findall("count"):
                key_node = count_item.find("key")
                val_node = count_item.find("value") # [Fix] Tag provided by client is "value"
                if key_node is not None and val_node is not None:
                    try:
                        profile["counts"][key_node.text] = int(val_node.text)
                    except: pass

        # --- PERSIST TO DB ---
        player.save_profile(profile)
        print(f"polaris_usr_save: Profile SAVED to DB for usr_id={usr_id}")

    else:
        print(f"polaris_usr_save: Profile NOT found for usr_id={usr_id}")

    response = E.response(
        E.usr(
            E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str")
        )
    )
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_usr_get_usr_music(request: Request):
    try:
        request_info = await core_process_request(request)
        root = request_info["root"][0]
        usr_id = int(root.find("usr_id").text)

        db = get_db().table("polaris_score")
        scores = db.search(where("usr_id") == usr_id)

        # Aggregate scores to find the best per (music_id, difficulty)
        best_scores = {}
        for s in scores:
            mid = s.get("music_id")
            diff = s.get("difficulty", 0)
            key = (mid, diff)

            if key not in best_scores:
                best_scores[key] = {
                    "music_id": mid,
                    "difficulty": diff,
                    "score": s.get("score", 0),
                    "achievement_rate": s.get("achievement_rate", 0),
                    "clear_status": s.get("clear_status", 0),
                    "combo": s.get("combo", 0),
                    "score_rank": s.get("score_rank", 0),
                    "combo_rank": s.get("combo_rank", 0),
                    "play_count": 1,
                    "clear_count": 1 if s.get("clear_status", 0) >= 10 else 0, # Assuming >=10 is clear
                }
            else:
                entry = best_scores[key]
                entry["score"] = max(entry["score"], s.get("score", 0))
                entry["achievement_rate"] = max(entry["achievement_rate"], s.get("achievement_rate", 0))
                entry["clear_status"] = max(entry["clear_status"], s.get("clear_status", 0))
                entry["combo"] = max(entry["combo"], s.get("combo", 0))
                entry["score_rank"] = max(entry["score_rank"], s.get("score_rank", 0))
                entry["combo_rank"] = max(entry["combo_rank"], s.get("combo_rank", 0))
                entry["play_count"] += 1
                if s.get("clear_status", 0) >= 10:
                    entry["clear_count"] += 1

        music_logs = []
        for key, val in best_scores.items():
            music_logs.append(
                E.music(
                    E.music_id(val["music_id"], __type="s32"),
                    E.chart_difficulty_type(val["difficulty"], __type="s32"),
                    E.achievement_rate(val["achievement_rate"], __type="s32"),
                    E.highscore(val["score"], __type="s32"),
                    E.score_rank(val["score_rank"], __type="s32"),
                    E.maxcombo(val["combo"], __type="s32"),
                    E.combo_rank(val["combo_rank"], __type="s32"),
                    E.clear_status(val["clear_status"], __type="s32"),
                    E.play_count(val["play_count"], __type="s32"),
                    E.clear_count(val["clear_count"], __type="s32"),
                    E.perfect_clear_count(0, __type="s32"),
                    E.full_combo_count(0, __type="s32"),
                )
            )

        response = E.response(
            E.usr(
                 E.usr_music_highscore(*music_logs)
            )
        )
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return Response(status_code=500)

async def polaris_usr_save_musicscore(request: Request):
    try:
        request_info = await core_process_request(request)
        root = request_info["root"][0]
        usr_id = int(root.find("usr_id").text)

        logs = root.find("usr_music_play_log")
        if logs is not None:
            db = get_db().table("polaris_score")
            for log in logs.findall("music"):
                def gi(t):
                    n = log.find(t)
                    return int(n.text) if n is not None and n.text else 0

                def gs(t):
                    n = log.find(t)
                    return n.text or "" if n is not None else ""

                mid = gi("music_id")
                diff = gi("chart_difficulty_type")
                score = gi("score")
                inputs = []
                for input_node in log.findall("./inputs/input"):
                    inputs.append({
                        "note_type": safe_int(input_node.findtext("note_type"), 0),
                        "judge_type": safe_int(input_node.findtext("judge_type"), 0),
                        "count": safe_int(input_node.findtext("count"), 0),
                    })

                cards = []
                for card_node in log.findall("./cards/card"):
                    card_data = {
                        "index": card_node.findtext("index") or "",
                        "item_id": card_node.findtext("item_id") or "",
                        "power": safe_int(card_node.findtext("power"), 0),
                        "level": safe_int(card_node.findtext("level"), 0),
                        "skill_level": safe_int(card_node.findtext("skill_level"), 0),
                        "rank": safe_int(card_node.findtext("rank"), 0),
                        "skills": [
                            str(skill.text)
                            for skill in card_node.findall("./skills/skill")
                            if skill.text is not None
                        ],
                        "triggered": safe_int(card_node.findtext("triggered"), 0),
                    }
                    cards.append(card_data)

                request_id = gs("request_id")

                score_data = {
                    "usr_id": usr_id,
                    "music_id": mid,
                    "difficulty": diff,
                    "score": score,
                    "hard_mode": gi("hard_mode"),
                    "end_reason": gi("end_reason"),
                    "retry_count": gi("retry_count"),
                    "perfect": gi("perfect"),
                    "great": gi("great"),
                    "good": gi("good"),
                    "bad": gi("bad"),
                    "miss": gi("miss"),
                    "clear_status": gi("clear_status"),
                    "combo": gi("combo"),
                    "achievement_rate": gi("achievement_rate"),
                    "score_rank": gi("score_rank"),
                    "combo_rank": gi("combo_rank"),
                    "audience_start": gi("audience_start"),
                    "audience_end": gi("audience_end"),
                    "inputs": inputs,
                    "cards": cards,
                    "frame_id": gs("frame_id"),
                    "pose_id": gs("pose_id"),
                    "another_costume_id": gs("another_costume_id"),
                    "fps_min": gi("fps_min"),
                    "fps_50": gi("fps_50"),
                    "fps_90": gi("fps_90"),
                    "fps_95": gi("fps_95"),
                    "fps_99": gi("fps_99"),
                    "fps_ave": gi("fps_ave"),
                    "mode_id": gi("mode_id"),
                    "stage_no": gi("stage_no"),
                    "loc_id": gs("loc_id"),
                    "shopname": gs("shopname"),
                    "music_select_total_time": gi("music_select_total_time"),
                    "music_select_used_time": gi("music_select_used_time"),
                    "music_select_remain_time": gi("music_select_remain_time"),
                    "music_category": gs("music_category"),
                    "request_id": request_id,
                    "timestamp": int(time.time())
                }

                if request_id:
                    existing = db.get(
                        (where("usr_id") == usr_id)
                        & (where("request_id") == request_id)
                    )
                    if existing is not None:
                        print(
                            f"polaris_usr_save_musicscore: Duplicate request_id {request_id} "
                            f"for usr_id {usr_id}, skipping insert"
                        )
                        continue

                db.insert(score_data)
                print(f"polaris_usr_save_musicscore: Saved music {mid} (diff {diff}) score {score}")

        response = E.response(
            E.usr(
                E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str")
            )
        )
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return Response(status_code=500)

async def polaris_usr_checkin(request: Request):
    request_info = await core_process_request(request)
    response = E.response(
        E.usr()
    )
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_usr_checkout(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.usr())
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_usr_get_temp(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.usr())
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)

async def polaris_usr_save_temp(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.usr())
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
