from fastapi import APIRouter, Request, Response
from core_common import core_process_request, core_prepare_response, E
import time

router = APIRouter(prefix="/polaris/mst", tags=["mst"])
router.model_whitelist = ["LAV", "XIF"]

SHOP_TEST_EVENT_IDS = (
    "shop.00000",
    # "shop.00001",
    # "shop.00002",
    # "shop.00003",
    # "shop.00004",
    # "shop.00005",
    # "shop.00006",
    # "shop.00007",
    # "shop.00008",
    # "shop.00009",
    # "shop.00010",
    # "shop.00011",
    # "shop.00012",
    # "shop.20001",
)

@router.post("")
@router.post("/")
@router.post("/{path:path}")
async def polaris_mst_dispatch(request: Request):
    request_info = await core_process_request(request)
    method = request_info["method"]
    if method == "get": method = "get_common" # Mapping for mst.get -> get_common
    
    func_name = f"polaris_mst_{method}"
    if func_name in globals():
        return await globals()[func_name](request)
    else:
        print(f"MST Dispatch: Function {func_name} not found")
        return Response(status_code=404)

async def polaris_mst_get_common(request: Request):
    request_info = await core_process_request(request)
    response = E.response(
        E.mst(
            E.now_date(time.strftime("%Y-%m-%d %H:%M:%S"), __type="str"),
            E.mst_music(
                *[
                    E.music(
                        E.music_id(music_id, __type="s32"),
                        E.charts(
                            *[
                                E.chart(
                                    E.chart_difficulty_type(diff, __type="s32"),
                                    E.limitation_type(2, __type="s32"), # 2 = Open
                                    E.open_at("2000-01-01 00:00:00", __type="str"),
                                    E.close_at("2099-12-31 23:59:59", __type="str")
                                ) for diff in range(5)
                            ]
                        )
                    ) for music_id in list(range(1, 401)) + [99900, 99901]
                ]
            ),
            E.mst_demo(),
            E.mst_event(
                *[
                    E.event_item(
                        E.id(event_id, __type="str"),
                        E.param("", __type="str"),
                        E.open_at("2026-01-01 00:00:00", __type="str"),
                        E.close_at("2040-12-31 14:59:59", __type="str"),
                    )
                    for event_id in SHOP_TEST_EVENT_IDS
                ]
            ),
            E.mst_patch(
                E.version(0, __type="s32"),
                E.patch("", __type="str")
            )
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
