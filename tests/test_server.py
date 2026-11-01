"""Tests against a fake Homebox (v0.27 entity API) using httpx.MockTransport."""

import json

import httpx
import pytest
from starlette.testclient import TestClient

from snap import server

TOKEN = "abc123"


class FakeHomebox:
    def __init__(self):
        self.entities = {}
        self.attachments = []
        self.printed = []
        self.fail_attachment = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/v1/users/login" and method == "POST":
            body = json.loads(request.content)
            if body["username"] == "g@example.com" and body["password"] == "secret":
                return httpx.Response(200, json={"token": "Bearer " + TOKEN, "expiresAt": "2099-01-01T00:00:00Z"})
            return httpx.Response(401, json={"error": "bad"})
        if request.headers.get("authorization") != "Bearer " + TOKEN:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/api/v1/users/self":
            return httpx.Response(200, json={"item": {"name": "Grégoire", "email": "g@example.com"}})
        if path == "/api/v1/users/logout":
            return httpx.Response(204)
        if path == "/api/v1/entities/tree":
            return httpx.Response(200, json=[
                {"id": "L1", "name": "Garage", "type": "location", "children": [
                    {"id": "L2", "name": "Rek A", "type": "location", "children": []},
                    {"id": "I9", "name": "Hamer", "type": "item", "children": []},
                ]},
                {"id": "L3", "name": "Bureau", "type": "location", "children": []},
            ])
        if path == "/api/v1/tags":
            return httpx.Response(200, json=[{"id": "T2", "name": "Tools"}, {"id": "T1", "name": "Elektrisch"}])
        if path == "/api/v1/entity-types":
            return httpx.Response(200, json=[{"id": "ET-L", "name": "Location", "isLocation": True},
                                             {"id": "ET-I", "name": "Item", "isLocation": False}])
        if path == "/api/v1/entities" and method == "POST":
            body = json.loads(request.content)
            eid = f"E{len(self.entities) + 1}"
            self.entities[eid] = body
            return httpx.Response(201, json={"id": eid, "name": body["name"], "assetId": "000-001"})
        if path.endswith("/attachments") and method == "POST":
            if self.fail_attachment:
                return httpx.Response(500, text="disk full")
            self.attachments.append((path.split("/")[4], request.content))
            return httpx.Response(201, json={})
        if path.startswith("/api/v1/labelmaker/item/"):
            self.printed.append((path.rsplit("/", 1)[1], request.url.params.get("print")))
            return httpx.Response(200, text="ok")
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeHomebox()


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.setattr(server, "COOKIE_SECURE", False)
    factory = lambda: httpx.AsyncClient(base_url="http://homebox/api", transport=httpx.MockTransport(fake.handler))
    with TestClient(server.create_app(factory)) as c:
        yield c


def login(client):
    r = client.post("/api/login", json={"username": "g@example.com", "password": "secret"})
    assert r.status_code == 200
    return r


def test_login_sets_httponly_cookie_without_bearer_prefix(client):
    r = login(client)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert f"{server.COOKIE}={TOKEN};" in cookie


def test_wrong_password(client):
    r = client.post("/api/login", json={"username": "g@example.com", "password": "nope"})
    assert r.status_code == 401 and "Aanmelden mislukt" in r.json()["error"]


def test_requires_login(client):
    assert client.get("/api/reference").status_code == 401
    assert client.post("/api/items", data={"name": "x", "locationId": "L1"}).status_code == 401


def test_reference_only_locations_with_paths(client):
    login(client)
    ref = client.get("/api/reference").json()
    assert [l["path"] for l in ref["locations"]] == ["Bureau", "Garage", "Garage › Rek A"]
    assert [t["name"] for t in ref["tags"]] == ["Elektrisch", "Tools"]
    assert ref["itemTypes"] == [{"id": "ET-I", "name": "Item"}]


def test_create_item_with_photos_tags_and_print(client, fake):
    login(client)
    r = client.post("/api/items",
                    data={"name": "Boormachine", "quantity": "2", "locationId": "L2", "entityTypeId": "ET-I",
                          "tagIds": ["T1", "T2"], "print": "true"},
                    files=[("photos", ("a.jpg", b"img1", "image/jpeg")), ("photos", ("b.jpg", b"img2", "image/jpeg"))])
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["photos"] == 2 and res["failedPhotos"] == 0 and res["printed"] is True
    created = fake.entities[res["id"]]
    assert created == {"name": "Boormachine", "parentId": "L2", "quantity": 2, "tagIds": ["T1", "T2"], "entityTypeId": "ET-I"}
    assert len(fake.attachments) == 2
    first = fake.attachments[0][1]
    assert b'name="primary"\r\n\r\ntrue' in first and b'name="type"\r\n\r\nphoto' in first
    assert b'name="primary"\r\n\r\nfalse' in fake.attachments[1][1]
    assert fake.printed == [(res["id"], "true")]


def test_create_item_without_print_or_photos(client, fake):
    login(client)
    r = client.post("/api/items", data={"name": "Kabel", "locationId": "L3"})
    assert r.status_code == 201
    assert r.json()["printed"] is None and fake.printed == [] and fake.attachments == []
    assert fake.entities[r.json()["id"]]["quantity"] == 1


def test_validation(client):
    login(client)
    assert client.post("/api/items", data={"name": "", "locationId": "L1"}).status_code == 400
    assert client.post("/api/items", data={"name": "x", "locationId": ""}).status_code == 400


def test_failed_photo_is_reported_but_item_kept(client, fake):
    login(client)
    fake.fail_attachment = True
    r = client.post("/api/items", data={"name": "Lamp", "locationId": "L1"},
                    files=[("photos", ("a.jpg", b"img", "image/jpeg"))])
    assert r.status_code == 201 and r.json()["failedPhotos"] == 1 and r.json()["photos"] == 0


def test_reprint(client, fake):
    login(client)
    assert client.post("/api/items/E7/print").json() == {"printed": True}
    assert fake.printed == [("E7", "true")]


def test_static_and_pwa(client):
    assert "Homebox" in client.get("/").text
    assert client.get("/manifest.webmanifest").json()["short_name"] == "Snap"
    assert client.get("/sw.js").headers["service-worker-allowed"] == "/"
    assert client.get("/healthz").json() == {"ok": True}
