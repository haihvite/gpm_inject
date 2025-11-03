# app.py
# Usage:
# 1) (optional) create venv and activate
# 2) pip install fastapi uvicorn requests python-multipart
# 3) pip install playwright
# 4) python -m playwright install chromium
# 5) uvicorn app:app --reload --host 127.0.0.1 --port 8080

import asyncio
import time
import os
from functools import partial
from typing import Dict, Optional

import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ====== CONFIG ======
GPM_API_BASE = "http://127.0.0.1:19995"   # chỉnh nếu GPM local API chạy port khác
GPM_API_TOKEN = ""                        # nếu local không cần token -> để rỗng
WIN_WIDTH, WIN_HEIGHT = 1920, 1080
WIN_POS_X, WIN_POS_Y = 0, 0
WIN_SCALE = 1.0
MAX_CONCURRENT = 3
# ====================

semaphore = asyncio.Semaphore(MAX_CONCURRENT)
running_profiles: Dict[str, Dict] = {}  # profile_id -> info dict


# ---- GPM start ----
def _call_gpm_start(profile_id: str) -> dict:
    """
    Start a profile via GPM local API:
    GET /api/v3/profiles/start/{id}?win_size=WxH&win_pos=X,Y&win_scale=S
    Returns parsed JSON from GPM.
    """
    url = f"{GPM_API_BASE}/api/v3/profiles/start/{profile_id}"
    headers = {}
    if GPM_API_TOKEN:
        headers["Authorization"] = f"Bearer {GPM_API_TOKEN}"

    params = {
        "win_size": f"{WIN_WIDTH},{WIN_HEIGHT}",
        "win_pos": f"{WIN_POS_X},{WIN_POS_Y}",
        "win_scale": f"{WIN_SCALE}",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _get_ws_from_port(host: str, port: int, timeout_s: float = 6.0) -> Optional[str]:
    base = f"http://{host}:{port}"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/json/version", timeout=2)
            if r.ok:
                j = r.json()
                if "webSocketDebuggerUrl" in j:
                    return j["webSocketDebuggerUrl"]
            r2 = requests.get(f"{base}/json", timeout=2)
            if r2.ok:
                arr = r2.json()
                if isinstance(arr, list):
                    for it in arr:
                        if "webSocketDebuggerUrl" in it:
                            return it["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.25)
    return None


def start_profile_sync(profile_id: str) -> dict:
    info = {"profile_id": profile_id, "status": "starting", "started_at": time.time()}
    try:
        resp = _call_gpm_start(profile_id)
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data:
            info["status"] = "error"
            info["error"] = f"Unexpected response: {resp}"
            return info

        # docs: data.remote_debugging_address = "127.0.0.1:XXXXX"
        rda = data.get("remote_debugging_address")
        if rda and ":" in rda:
            host, port = rda.split(":", 1)
            info["debug_host"] = host
            try:
                info["debug_port"] = int(port)
            except Exception:
                info["debug_port"] = port
            info["status"] = "started"
            ws = _get_ws_from_port(host, int(port))
            info["websocket"] = ws
        else:
            info["status"] = "started (no debug info)"
            info["raw_response"] = data
    except Exception as e:
        info["status"] = "error"
        info["error"] = str(e)
    return info


async def start_profile_task(profile_id: str):
    async with semaphore:
        running_profiles[profile_id] = {
            "status": "queued",
            "profile_id": profile_id,
            "started_at": time.time(),
        }
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(start_profile_sync, profile_id))
        running_profiles[profile_id].update(result)
        return running_profiles[profile_id]


# ---- Playwright inject helper ----
def _inject_into_all_pages(ws_or_http: str, script_url: Optional[str], inline_js: Optional[str]) -> dict:
    """
    Attach via Playwright CDP and inject into all contexts/pages.
    Returns stats dict.
    """
    stats = {"contexts": 0, "pages": 0, "injected_url": 0, "injected_inline": 0}
    try:
        # import inside function to avoid failing startup if playwright not installed
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError("Playwright not installed or not configured. Install with 'pip install playwright' and run 'playwright install chromium'") from e

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(ws_or_http)
        contexts = browser.contexts or []
        stats["contexts"] = len(contexts)
        # if no contexts, still operate with browser -- but loop contexts list (could be empty)
        if not contexts:
            # create a temp context so we can inject
            ctx = browser.new_context()
            contexts = [ctx]

        for ctx in contexts:
            pages = ctx.pages
            if not pages:
                pages = [ctx.new_page()]
            for page in pages:
                stats["pages"] += 1
                # try external script first
                if script_url:
                    try:
                        page.add_script_tag(url=script_url)
                        stats["injected_url"] += 1
                    except Exception:
                        # ignore and try inline fallback
                        pass
                if inline_js:
                    try:
                        # prefer add_script_tag(content=...) so it appears as <script>
                        page.add_script_tag(content=inline_js)
                        stats["injected_inline"] += 1
                    except Exception:
                        try:
                            page.evaluate(inline_js)
                            stats["injected_inline"] += 1
                        except Exception:
                            # cannot inject into this page
                            pass
    return stats


# ---- FastAPI endpoints ----
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "running": running_profiles, "max_concurrent": MAX_CONCURRENT})


@app.post("/start_profile")
async def start_profile_endpoint(profile_id: str = Form(...)):
    if profile_id in running_profiles and running_profiles[profile_id].get("status") not in ("error",):
        return JSONResponse({"ok": False, "message": f"Profile {profile_id} đang chạy/đang hàng đợi.", "state": running_profiles[profile_id]})
    asyncio.create_task(start_profile_task(profile_id))
    return JSONResponse({"ok": True, "message": f"Starting profile {profile_id}...", "profile_id": profile_id})


@app.get("/status")
async def status():
    out = {}
    for pid, info in running_profiles.items():
        copy = info.copy()
        if "started_at" in copy:
            try:
                copy["started_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(copy["started_at"]))
            except Exception:
                pass
        out[pid] = copy
    return out


@app.get("/status/{profile_id}")
async def status_one(profile_id: str):
    info = running_profiles.get(profile_id)
    if not info:
        return {"exists": False}
    copy = info.copy()
    if "started_at" in copy:
        try:
            copy["started_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(copy["started_at"]))
        except Exception:
            pass
    copy["exists"] = True
    return copy


@app.post("/inject")
async def inject_endpoint(
    profile_id: str = Form(...),
    script_url: str = Form("", description="optional"),
    inline_js: str = Form("", description="optional"),
):
    info = running_profiles.get(profile_id)
    if not info or not info.get("debug_host") or not info.get("debug_port"):
        return JSONResponse({"ok": False, "message": "Profile chưa start hoặc thiếu debug host/port."})

    host = info["debug_host"]
    port = int(info["debug_port"])
    ws = info.get("websocket") or _get_ws_from_port(host, port)
    if not ws:
        # Playwright connect_over_cdp accepts http://host:port as well
        ws = f"http://{host}:{port}"

    final_inline_js = inline_js.strip() if inline_js and inline_js.strip() else None
    final_script_url = script_url.strip() if script_url and script_url.strip() else None

    # nếu không có url và không có inline -> đọc file ./script.js
    if not final_inline_js and not final_script_url:
        local_path = os.path.join(os.path.dirname(__file__), "script.js")
        if os.path.isfile(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    final_inline_js = f.read()
            except Exception as e:
                return JSONResponse({"ok": False, "message": f"Không đọc được file script.js: {e}"})
        else:
            return JSONResponse({"ok": False, "message": "Không có script_url, inline_js, và file ./script.js không tồn tại."})

    loop = asyncio.get_event_loop()
    try:
        stats = await loop.run_in_executor(
            None,
            lambda: _inject_into_all_pages(ws, final_script_url, final_inline_js)
        )
        return JSONResponse({"ok": True, "stats": stats})
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Inject failed: {e}"})
