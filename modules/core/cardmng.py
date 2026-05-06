from fastapi import APIRouter, Request, Response
from tinydb import where

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

router = APIRouter(prefix="/core", tags=["cardmng"])


def to_refid(cid):
    # Generate a deterministic 16-digit numeric ID from the hex card ID
    return str(int(cid, 16)).zfill(16)[-16:]


def get_profile(cid):
    profile = get_db().table("polaris_profile").get(where("card") == cid)

    if profile is None:
        profile = {
            "card": cid,
            "version": {},
        }

    return profile


def get_profile_by_refid(refid):
    return get_db().table("polaris_profile").get(where("refid") == refid)


def get_game_profile(game_version, cid):
    profile = get_profile(cid)

    if str(game_version) not in profile["version"]:
        profile["version"][str(game_version)] = {}

    return profile["version"][str(game_version)]


def create_profile(cid, pin, refid=None):
    profile = get_profile(cid)

    profile["pin"] = pin
    if refid:
        profile["refid"] = refid

    get_db().table("polaris_profile").upsert(profile, where("card") == cid)


@router.post("/{gameinfo}/cardmng/authpass")
async def cardmng_authpass(request: Request):
    request_info = await core_process_request(request)

    refid = request_info["root"][0].attrib["refid"]
    passwd = request_info["root"][0].attrib["pass"]

    profile = get_profile_by_refid(refid)
    if profile is None or passwd != profile.get("pin", None):
        status = 116
    else:
        status = 0

    response = E.response(E.cardmng(status=status))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/bindmodel")
async def cardmng_bindmodel(request: Request):
    request_info = await core_process_request(request)

    response = E.response(E.cardmng(dataid=1))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/getrefid")
async def cardmng_getrefid(request: Request):
    request_info = await core_process_request(request)

    cid = request_info["root"][0].attrib["cardid"]
    passwd = request_info["root"][0].attrib["passwd"]
    refid = to_refid(cid)

    create_profile(cid, passwd, refid=refid)

    response = E.response(
        E.cardmng(
            dataid=cid,
            refid=refid,
            pcode=refid,
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/inquire")
async def cardmng_inquire(request: Request):
    request_info = await core_process_request(request)

    cid = request_info["root"][0].attrib["cardid"].strip() # Validate/Strip

    profile = get_profile(cid)
    
    # Check if this is a registered card (has pin or dataid) AND has user profile (name or usr_id)
    # This prevents 'Card Registered but User Unregistered' state which confuses some games (like Polaris)
    is_registered = ("pin" in profile or "dataid" in profile) and ("name" in profile or "usr_id" in profile)

    if is_registered:
        binded = 1
        newflag = 0
        status = 0
    else:
        binded = 0
        newflag = 1
        status = 112
    
    refid = to_refid(cid)
    print(f"cardmng_inquire: cid={cid}, refid={refid}, status={status}")

    response = E.response(
        E.cardmng(
            dataid=cid,
            ecflag=1,
            expired=0,
            binded=binded,
            newflag=newflag,
            refid=refid,
            pcode=refid,
            status=status,
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
