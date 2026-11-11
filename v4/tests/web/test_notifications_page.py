"""Notifications page (/notifications): ownership, unread filter, paging, mark read actions."""

import json
from datetime import date

import pytest

from apps.inbox.models import Request
from apps.notifications import pages
from apps.notifications.models import WebhookNotification

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


@pytest.fixture
def me(make_user, seeded):
    return make_user("VIEWER", username="me")


@pytest.fixture
def other(make_user, seeded):
    return make_user("ADMIN", username="other")


@pytest.fixture
def my_client(web_login, me):
    return web_login(me)


def notify(user, title="Hello", **kw):
    return WebhookNotification.objects.create(user=user, title=title, **kw)


class TestPage:
    def test_anonymous_redirected(self, client, seeded):
        resp = client.get("/notifications")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    def test_lists_only_own_notifications(self, my_client, me, other):
        notify(me, "Mine unread", message="m1", source="JIRA")
        notify(me, "Mine read", is_read=True)
        notify(other, "Not mine SECRET")
        resp = my_client.get("/notifications")
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "Mine unread" in body and "Mine read" in body and "Not mine SECRET" not in body
        assert "1 UNREAD · 2 TOTAL" in body
        assert 'class="badge badge-jira">JIRA' in body
        assert "Mark all read" in body

    def test_empty_states(self, my_client):
        assert "No notifications" in my_client.get("/notifications").content.decode()
        assert "No unread notifications" in my_client.get("/notifications?filter=unread").content.decode()

    def test_unread_filter(self, my_client, me):
        notify(me, "Fresh one")
        notify(me, "Old one", is_read=True)
        body = my_client.get("/notifications?filter=unread").content.decode()
        assert "Fresh one" in body and "Old one" not in body
        part = my_client.get("/notifications/results?filter=unread", headers=HX)
        assert part.status_code == 200 and "<html" not in part.content.decode()
        assert "Old one" not in part.content.decode()
        assert 'id="nt-status" class="ed-status-now" hx-swap-oob="true"' in part.content.decode()

    def test_bad_filter_and_page_fall_back(self, my_client, me):
        notify(me, "Shown")
        body = my_client.get("/notifications?filter=bogus&page=abc").content.decode()
        assert "Shown" in body

    def test_request_link(self, my_client, me):
        req = Request.objects.create(request_number="REQ-2026-001", title="R", requester_name="X",
                                     received_date=date.today())
        notify(me, "Imported", request=req, source="REDMINE")
        body = my_client.get("/notifications").content.decode()
        assert f'href="/requests/{req.pk}"' in body and "REQ-2026-001" in body

    def test_paging(self, my_client, me):
        for i in range(pages.PAGE_SIZE + 5):
            notify(me, f"Item {i:02d}")
        first = my_client.get("/notifications").content.decode()
        assert "Item 24" in first and "Item 04" not in first  # newest first
        assert "Page 1 / 2" in first
        second = my_client.get("/notifications/results?page=2", headers=HX).content.decode()
        assert "Item 04" in second and "Item 24" not in second and "Page 2 / 2" in second
        assert "Item 00" in my_client.get("/notifications?page=99").content.decode()  # clamped

    def test_methods(self, my_client):
        assert my_client.post("/notifications").status_code == 405
        assert my_client.get("/notifications/mark-all-read").status_code == 405


class TestActions:
    def test_mark_one_read(self, my_client, me):
        n = notify(me, "Read me")
        resp = my_client.post(f"/notifications/{n.pk}/mark-read", {"filter": "all", "page": "1"}, headers=HX)
        assert resp.status_code == 200
        n.refresh_from_db()
        assert n.is_read
        assert json.loads(resp["HX-Trigger"])["notifications-changed"] is True
        assert "0 UNREAD" in resp.content.decode()

    def test_mark_one_keeps_filter(self, my_client, me):
        a, b = notify(me, "First"), notify(me, "Second")
        resp = my_client.post(f"/notifications/{a.pk}/mark-read", {"filter": "unread"}, headers=HX)
        body = resp.content.decode()
        assert "Second" in body and "First" not in body
        assert b.pk and 'aria-current="true"' in body

    def test_cannot_mark_others(self, my_client, other):
        n = notify(other, "Theirs")
        resp = my_client.post(f"/notifications/{n.pk}/mark-read", headers=HX)
        assert resp.status_code == 404
        n.refresh_from_db()
        assert not n.is_read

    def test_unknown_404(self, my_client):
        assert my_client.post("/notifications/999999/mark-read", headers=HX).status_code == 404

    def test_mark_all_only_own(self, my_client, me, other):
        notify(me, "a")
        notify(me, "b")
        theirs = notify(other, "c")
        resp = my_client.post("/notifications/mark-all-read", {"filter": "unread"}, headers=HX)
        assert resp.status_code == 200
        assert not WebhookNotification.objects.filter(user=me, is_read=False).exists()
        theirs.refresh_from_db()
        assert not theirs.is_read
        trig = json.loads(resp["HX-Trigger"])
        assert trig["toast"]["message"] == "All notifications marked as read"
        assert trig["notifications-changed"] is True
        assert "No unread notifications" in resp.content.decode()

    def test_mark_all_when_none(self, my_client):
        resp = my_client.post("/notifications/mark-all-read", headers=HX)
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "No unread notifications"

    def test_bell_partials_unchanged(self, my_client, me):
        notify(me, "Bell")
        assert my_client.get("/notifications/badge").status_code == 200
        assert b"Bell" in my_client.get("/notifications/panel").content
