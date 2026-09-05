"""The admin HTTP surface: auth, JSON snapshot, and the page itself."""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bridge_for_agents import admin
from bridge_for_agents.store import Store

TOKEN = "t" * 32


async def client(token: str = "") -> TestClient:
    st = Store()
    st.record({"session_id": "abcdef123456", "hook_event_name": "PermissionRequest",
               "cwd": "/home/me/myrepo", "tool_name": "Bash"}, "rm -rf /tmp/x")
    app = web.Application()
    admin.attach(app, st, token)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


@pytest.fixture
async def open_client():
    c = await client()
    yield c
    await c.close()


@pytest.fixture
async def guarded_client():
    c = await client(TOKEN)
    yield c
    await c.close()


async def test_page_is_served_when_no_token_is_configured(open_client):
    r = await open_client.get("/admin")
    assert r.status == 200
    assert "bridge-for-agents" in await r.text()


async def test_state_reports_the_recorded_event(open_client):
    d = await (await open_client.get("/admin/api/state")).json()
    assert d["totals"]["sessions"] == 1
    assert d["feed"][0]["summary"] == "rm -rf /tmp/x"
    assert d["sessions"][0]["project"] == "myrepo"


async def test_state_can_be_filtered_to_one_session(open_client):
    d = await (await open_client.get("/admin/api/state?session=abcdef123456")).json()
    assert d["selected"]["short_id"] == "abcdef12"
    assert len(d["selected"]["events"]) == 1


async def test_page_without_a_token_is_rejected(guarded_client):
    r = await guarded_client.get("/admin")
    assert r.status == 401
    assert "unauthorized" in (await r.text()).lower()


async def test_state_without_a_token_is_rejected(guarded_client):
    r = await guarded_client.get("/admin/api/state")
    assert r.status == 401
    assert await r.json() == {"error": "unauthorized"}


async def test_bearer_header_is_accepted(guarded_client):
    r = await guarded_client.get("/admin/api/state",
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status == 200


async def test_wrong_token_is_rejected(guarded_client):
    r = await guarded_client.get("/admin/api/state",
                                 headers={"Authorization": "Bearer " + "x" * 32})
    assert r.status == 401


async def test_query_token_is_accepted_and_stored_in_a_cookie(guarded_client):
    r = await guarded_client.get(f"/admin?token={TOKEN}")
    assert r.status == 200
    cookie = r.cookies[admin.COOKIE]
    assert cookie.value == TOKEN
    assert cookie["httponly"]
    assert cookie["samesite"] == "Strict"
    # the cookie now carries the session
    assert (await guarded_client.get("/admin/api/state")).status == 200


async def test_admin_routes_are_absent_unless_attached():
    app = web.Application()
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        assert (await c.get("/admin")).status == 404
    finally:
        await c.close()
