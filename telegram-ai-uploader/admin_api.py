"""HTTP API exposed by the bot for the admin panel to push live content updates.

The Telegram bot already holds the GITHUB_TOKEN. Rather than embedding that token
in the browser, the admin sends signed POSTs to this endpoint and the bot forwards
the change to GitHub.

Endpoint:
    POST /api/admin/contacts
        Headers:
            X-Admin-Secret: <ADMIN_API_SECRET>
            Content-Type: application/json
        Body:
            { whatsapp, instagram, igUsername, whatsappGroup,
              twoGisUrl, twoGisWidgetUrl, city, hours, followers, posts }
        Response:
            200 { ok: true,  commit: "..." }   on success
            401 { ok: false, error: "..." }    on bad secret
            500 { ok: false, error: "..." }    on upstream failure

The admin panel runs on stben00.github.io, so the response includes permissive
CORS headers (it's safe because the endpoint itself is gated by a shared secret).
"""
import logging
import os
import secrets

from aiohttp import web

from github_client import read_site_data, write_site_data

log = logging.getLogger(__name__)

ALLOWED_CONTACT_KEYS = {
    "whatsapp",
    "instagram",
    "igUsername",
    "whatsappGroup",
    "twoGisUrl",
    "twoGisWidgetUrl",
    "city",
    "hours",
    "followers",
    "posts",
    "leadWebhook",
}


def _json(status: int, payload: dict) -> web.Response:
    return web.json_response(
        payload,
        status=status,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, X-Admin-Secret",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


async def _options(_request: web.Request) -> web.Response:
    return _json(200, {"ok": True})


async def _health(_request: web.Request) -> web.Response:
    return _json(200, {"ok": True, "service": "tilek-auto-bot"})


def _check_auth(request: web.Request) -> bool:
    expected = os.getenv("ADMIN_API_SECRET", "").strip()
    if not expected:
        log.warning("ADMIN_API_SECRET not set; rejecting all admin requests")
        return False
    provided = request.headers.get("X-Admin-Secret", "").strip()
    if not provided:
        return False
    return secrets.compare_digest(expected, provided)


async def _post_contacts(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return _json(401, {"ok": False, "error": "Unauthorized"})

    try:
        payload = await request.json()
    except Exception:
        return _json(400, {"ok": False, "error": "Invalid JSON body"})
    if not isinstance(payload, dict):
        return _json(400, {"ok": False, "error": "Body must be a JSON object"})

    contacts = {k: str(v).strip() for k, v in payload.items() if k in ALLOWED_CONTACT_KEYS and v is not None}
    if not contacts:
        return _json(400, {"ok": False, "error": "No recognized contact fields"})

    try:
        site = await read_site_data()
        existing = site.get("content") if isinstance(site.get("content"), dict) else {}
        merged = {**existing, **contacts}
        site["content"] = merged
        result = await write_site_data(site, "Admin: update contacts")
    except Exception as e:
        log.exception("Failed to update contacts on GitHub")
        return _json(500, {"ok": False, "error": str(e)})

    sha = ""
    try:
        sha = (result or {}).get("commit", {}).get("sha", "")
    except Exception:
        pass

    log.info("Admin contacts updated: %s", sorted(contacts.keys()))
    return _json(200, {"ok": True, "commit": sha, "fields": sorted(contacts.keys())})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_options("/api/admin/contacts", _options)
    app.router.add_post("/api/admin/contacts", _post_contacts)
    return app


async def start_server(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    """Start the aiohttp server in the existing event loop. Returns the runner so the caller can shut it down."""
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Admin HTTP API listening on %s:%d", host, port)
    return runner
