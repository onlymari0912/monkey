import config

import time
from pathlib import Path

from lxml.builder import ElementMaker

from kbinxml import KBinXML

from utils.arc4 import EamuseARC4
from utils.lz77 import lz77_decode, lz77_encode


def _add_val_as_str(elm, val):
    new_val = str(val)

    if elm is not None:
        elm.text = new_val

    else:
        return new_val


def _add_bool_as_str(elm, val):
    return _add_val_as_str(elm, 1 if val else 0)

def _add_list_as_str(elm, vals):
    new_val = " ".join([str(val) for val in vals])

    if elm is not None:
        elm.text = new_val
        elm.attrib["__count"] = str(len(vals))

    else:
        return new_val

def _prng():
    state = 0x41C64E6D
    while True:
        x = (state * 0x838C9CDA) + 0x6072
        # state = (state * 0x41C64E6D + 0x3039)
        # state = (state * 0x41C64E6D + 0x3039)
        state = (state * 0xC2A29A69 + 0xD3DC167E) & 0xFFFFFFFF
        yield (x & 0x7FFF0000) | state >> 0xF & 0xFFFF

prng_init = _prng()
HTTP_TRACE_LOG_DIR = Path("logs") / "http_trace"

E = ElementMaker(
    typemap={
        int: _add_val_as_str,
        bool: _add_bool_as_str,
        list: _add_list_as_str,
        float: _add_val_as_str,
    }
)

def _format_http_headers(headers):
    return "\n".join(f"{k}: {v}" for k, v in headers.items())

def _sanitize_log_part(value):
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value or "unknown"))
    return safe.strip("_") or "unknown"

def _append_http_log(request, body):
    log_path = getattr(request.state, "http_log_path", None)
    if not log_path:
        return

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(body)
        if not body.endswith("\n"):
            log_file.write("\n")
        log_file.write("\n")

async def core_process_request(request):
    cached_request_info = getattr(request.state, "core_request_info", None)
    if cached_request_info is not None:
        return cached_request_info

    cl = request.headers.get("Content-Length")
    data = await request.body()

    if not cl or not data:
        return {}

    request.compress = request.headers.get("X-Compress", "none") # intentionally lowercase 'none' (NOT None)

    if "X-Eamuse-Info" in request.headers:
        xeamuseinfo = request.headers.get("X-Eamuse-Info")
        version, unix_time, prng = xeamuseinfo.split("-")
        xml_dec = EamuseARC4(bytes.fromhex(unix_time), bytes.fromhex(prng)).decrypt(data[: int(cl)])
        request.is_encrypted = True
    else:
        xml_dec = data[: int(cl)]
        request.is_encrypted = False

    if request.compress == "lz77":
        xml_dec = lz77_decode(xml_dec)

    xml = KBinXML(xml_dec, convert_illegal_things=True)
    root = xml.xml_doc
    xml_text = xml.to_text()
    request.is_binxml = KBinXML.is_binary_xml(xml_dec)

    if config.verbose_log:
        print()
        print("\033[94mREQUEST\033[0m:")
        print(xml_text)

    model_parts = (root.attrib["model"], *root.attrib["model"].split(":"))
    module = root[0].tag
    method = root[0].attrib["method"] if "method" in root[0].attrib else None
    command = root[0].attrib["command"] if "command" in root[0].attrib else None

    request_info = {
        "root": root,
        "text": xml_text,
        "module": module,
        "method": method,
        "command": command,
        "model": model_parts[1],
        "dest": model_parts[2],
        "spec": model_parts[3],
        "rev": model_parts[4],
        "ext": model_parts[5],
        "game_version": 1,
    }
    request.state.core_request_info = request_info

    if (
        config.http_trace
        and not getattr(request.state, "http_request_logged", False)
    ):
        request_headers = _format_http_headers(request.headers)
        inbound_log = (
            f"=== HTTP TRACE ===\n"
            f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"path: {request.url.path}\n"
            f"method: {method or ''}\n"
            f"command: {command or ''}\n"
            f"\n"
            f"[request headers]\n{request_headers}\n"
            f"\n"
            f"[request xml]\n{xml_text}\n"
        )
        _append_http_log(request, inbound_log)
        request.state.http_request_logged = True

    return request_info


async def core_prepare_response(request, xml):
    binxml = KBinXML(xml)

    if False and request.is_binxml:
        # [Fix] Force UTF-8 encoding for Binary XML to match Client expectation (XrpcBase.cs uses UTF8)
        # Default is 'cp932', which causes Mojibake.
        # NOTE: KBinXML requires uppercase 'UTF-8', lowercase raises KeyError.
        xml_binary = binxml.to_binary()
    else:
        # Client (Unity/Mono) expects UTF-8 (XrpcBase.cs: Encoding.UTF8.GetString)
        xml_text = binxml.to_text() 
        # Ensure declaration matches (KBinXML might default to UTF-8, which is good)
        if 'encoding="UTF-8"' not in xml_text and "encoding='UTF-8'" not in xml_text:
             xml_text = xml_text.replace("?>", ' encoding="UTF-8"?>', 1)
        
        xml_binary = xml_text.encode("utf-8")

    if config.verbose_log:
        print("\033[91mRESPONSE\033[0m:")
        print(binxml.to_text())

    response_headers = {"User-Agent": "EAMUSE.Httpac/1.0"}
    
    # Explicitly state UTF-8
    if not request.is_binxml:
        response_headers["Content-Type"] = "text/xml; charset=utf-8"

    if config.response_compression:
        response_headers["X-Compress"] = request.compress
        if request.compress == "lz77":
            response = lz77_encode(xml_binary) # very slow
        else:
            response = xml_binary
    else:
        response_headers["X-Compress"] = "none" # intentionally lowercase 'none' (NOT None)
        response = xml_binary

    if request.is_encrypted:
        version = 1
        unix_time = int(time.time())
        prng = next(prng_init) & 0xFFFF
        response_headers["X-Eamuse-Info"] = f"{version}-{unix_time:04x}-{prng:04x}"
        response = EamuseARC4(unix_time.to_bytes(4), prng.to_bytes(2)).encrypt(response)
    else:
        response = bytes(response)

    request_info = getattr(request.state, "core_request_info", None)
    response_text = binxml.to_text()
    if (
        request_info
        and config.http_trace
        and not getattr(request.state, "http_response_logged", False)
    ):
        response_header_text = _format_http_headers(response_headers)
        outbound_log = (
            f"[response headers]\n{response_header_text}\n"
            f"\n"
            f"[response xml]\n{response_text}\n"
        )
        _append_http_log(request, outbound_log)
        request.state.http_response_logged = True

    return response, response_headers
