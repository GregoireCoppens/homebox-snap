"""homebox-snap: quick photo intake for Homebox (v0.27+, entity model).

The phone talks only to this server; this server talks to Homebox on the internal network.
The Homebox session token lives in an HttpOnly cookie, so page scripts never see it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

log = logging.getLogger("homebox_snap")

HOMEBOX_URL = os.environ.get("HOMEBOX_URL", "http://homebox:7745").rstrip("/")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
MAX_PHOTOS = int(os.environ.get("MAX_PHOTOS", "12"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "60"))
STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "hbsnap"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=HOMEBOX_URL + "/api", timeout=60)


class HomeboxError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def token_of(request: Request) -> str:
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HomeboxError(401, "Niet aangemeld")
    return token


async def hb(request: Request, method: str, path: str, **kw) -> httpx.Response:
    client: httpx.AsyncClient = request.app.state.client
    headers = kw.pop("headers", {})
    headers["Authorization"] = "Bearer " + token_of(request)
    r = await client.request(method, path, headers=headers, **kw)
    if r.status_code == 401:
        raise HomeboxError(401, "Sessie verlopen, meld opnieuw aan")
    if r.status_code >= 400:
        raise HomeboxError(502, f"Homebox gaf fout {r.status_code}: {r.text[:200]}")
    return r


async def error_handler(request: Request, exc: HomeboxError) -> JSONResponse:
    resp = JSONResponse({"error": exc.message}, status_code=exc.status)
    if exc.status == 401:
        resp.delete_cookie(COOKIE, path="/")
    return resp


# ------------------------------------------------------------------ auth
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    client: httpx.AsyncClient = request.app.state.client
    r = await client.post("/v1/users/login", json={
        "username": body.get("username", ""),
        "password": body.get("password", ""),
        "stayLoggedIn": True,
    })
    if r.status_code != 200:
        return JSONResponse({"error": "Aanmelden mislukt: controleer e-mail en wachtwoord"}, status_code=401)
    data = r.json()
    token = str(data.get("token", "")).removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse({"error": "Homebox gaf geen sessie terug"}, status_code=502)
    max_age = 60 * 60 * 24 * 30
    expires = data.get("expiresAt")
    if expires:
        try:
            delta = datetime.fromisoformat(expires.replace("Z", "+00:00")) - datetime.now(timezone.utc)
            max_age = max(60, int(delta.total_seconds()))
        except ValueError:
            pass
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/")
    return resp


async def logout(request: Request) -> JSONResponse:
    try:
        await hb(request, "POST", "/v1/users/logout")
    except HomeboxError:
        pass
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


async def me(request: Request) -> JSONResponse:
    r = await hb(request, "GET", "/v1/users/self")
    data = r.json()
    user = data.get("item", data)
    return JSONResponse({"name": user.get("name") or user.get("email") or ""})


# ------------------------------------------------------------- reference
def flatten_locations(nodes: list, trail: list[str], out: list) -> None:
    for n in nodes or []:
        if str(n.get("type", "")).lower() != "location":
            continue
        path = trail + [n.get("name", "")]
        out.append({"id": n["id"], "name": n.get("name", ""), "path": " › ".join(path)})
        flatten_locations(n.get("children"), path, out)


async def reference(request: Request) -> JSONResponse:
    tree, tags, types = await asyncio.gather(
        hb(request, "GET", "/v1/entities/tree"),
        hb(request, "GET", "/v1/tags"),
        hb(request, "GET", "/v1/entity-types"),
    )
    locations: list = []
    flatten_locations(tree.json(), [], locations)
    locations.sort(key=lambda l: l["path"].lower())
    tag_list = sorted(({"id": t["id"], "name": t.get("name", "")} for t in tags.json()), key=lambda t: t["name"].lower())
    item_types = [{"id": t["id"], "name": t.get("name", "")} for t in types.json() if not t.get("isLocation")]
    return JSONResponse({"locations": locations, "tags": tag_list, "itemTypes": item_types})


# ------------------------------------------------------------------ items
async def create_item(request: Request) -> JSONResponse:
    token_of(request)
    form = await request.form(max_files=MAX_PHOTOS, max_fields=100, max_part_size=MAX_UPLOAD_MB * 1024 * 1024)
    name = str(form.get("name", "")).strip()
    location_id = str(form.get("locationId", "")).strip()
    if not name:
        return JSONResponse({"error": "Naam is verplicht"}, status_code=400)
    if not location_id:
        return JSONResponse({"error": "Kies eerst een locatie"}, status_code=400)
    try:
        quantity = max(1, int(str(form.get("quantity", "1")) or 1))
    except ValueError:
        quantity = 1
    payload = {
        "name": name,
        "parentId": location_id,
        "quantity": quantity,
        "tagIds": [str(t) for t in form.getlist("tagIds") if str(t)],
    }
    if form.get("entityTypeId"):
        payload["entityTypeId"] = str(form.get("entityTypeId"))
    photos = [f for f in form.getlist("photos") if hasattr(f, "read")]

    created = (await hb(request, "POST", "/v1/entities", json=payload)).json()
    item_id = created["id"]

    failed_photos = 0
    for i, photo in enumerate(photos):
        content = await photo.read()
        try:
            await hb(request, "POST", f"/v1/entities/{item_id}/attachments",
                     data={"type": "photo", "primary": "true" if i == 0 else "false", "name": f"{name} {i + 1}.jpg"},
                     files={"file": (f"photo-{i + 1}.jpg", content, photo.content_type or "image/jpeg")})
        except HomeboxError as exc:
            log.warning("Photo %s for %s failed: %s", i + 1, item_id, exc.message)
            failed_photos += 1

    printed = None
    if str(form.get("print", "")).lower() in ("1", "true", "on", "yes"):
        try:
            await hb(request, "GET", f"/v1/labelmaker/item/{item_id}", params={"print": "true"})
            printed = True
        except HomeboxError as exc:
            log.warning("Label print for %s failed: %s", item_id, exc.message)
            printed = False

    await form.close()
    return JSONResponse({
        "id": item_id,
        "name": created.get("name", name),
        "assetId": created.get("assetId"),
        "photos": len(photos) - failed_photos,
        "failedPhotos": failed_photos,
        "printed": printed,
    }, status_code=201)


async def print_label(request: Request) -> JSONResponse:
    await hb(request, "GET", f"/v1/labelmaker/item/{request.path_params['id']}", params={"print": "true"})
    return JSONResponse({"printed": True})


# ------------------------------------------------------------------- misc
async def config(request: Request) -> JSONResponse:
    return JSONResponse({"homeboxUrl": os.environ.get("HOMEBOX_PUBLIC_URL", ""), "maxPhotos": MAX_PHOTOS})


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def index(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def manifest(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


async def service_worker(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


def create_app(client_factory=make_client) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.client = client_factory()
        yield
        await app.state.client.aclose()

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/healthz", healthz),
            Route("/manifest.webmanifest", manifest),
            Route("/sw.js", service_worker),
            Route("/api/config", config),
            Route("/api/login", login, methods=["POST"]),
            Route("/api/logout", logout, methods=["POST"]),
            Route("/api/me", me),
            Route("/api/reference", reference),
            Route("/api/items", create_item, methods=["POST"]),
            Route("/api/items/{id}/print", print_label, methods=["POST"]),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        exception_handlers={HomeboxError: error_handler},
        lifespan=lifespan,
    )
    return app


app = create_app()
