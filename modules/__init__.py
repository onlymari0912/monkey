from importlib import util
from os import path
from glob import glob

from fastapi import APIRouter, Request, Response
from typing import Optional

routers = []
for module_path in sorted(
    f
    for f in glob(path.join(path.dirname(__file__), "**/*.py"), recursive=True)
    if path.basename(f) != "__init__.py"
):
    spec = util.spec_from_file_location("", module_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    router = getattr(module, "router", None)
    if router is not None:
        routers.append(router)

    if path.basename(module_path) != "api.py":
        for obj in dir(module):
            globals()[obj] = module.__dict__[obj]

router = APIRouter(tags=["slashless_forwarder"])


@router.post("/fwdr")
async def forward_slashless(
    request: Request,
    model: Optional[str] = None,
    f: Optional[str] = None,
    module: Optional[str] = None,
    method: Optional[str] = None,
):
    if f is not None:
        module, method = f.split(".")

    try:
        find_response = globals()[f"{module}_{method}".lower()]
        return await find_response(request)
    except KeyError:
        try:
            find_response = globals()[f"polaris_{module}_{method}"]
            return await find_response(request)
        except (KeyError, UnboundLocalError) as e:
            print(f"FWDR DEBUG: 404 Error. f='{f}' model='{model}'")
            print(f"FWDR DEBUG: Parsed -> module='{module}' method='{method}'")
            print(f"FWDR DEBUG: Exception: {e}")
            target_key = f"polaris_{module}_{method}"
            if request.url.path == "/fwdr":
                 print(f"FWDR DEBUG: Target globals key expected: '{target_key}'")
                 print(f"FWDR DEBUG: Key in globals: {target_key in globals()}")
            
            print("Try URL Slash 1 (On).")
            return Response(status_code=404)


routers.append(router)
