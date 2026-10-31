import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from birdframe import main as birdframe_main
from birdframe.main import create_app, in_quiet_hours
from birdframe.schemas import DetectionCreate, PublicSettings
from tests.helpers import authed


def _settings(client: TestClient, **updates) -> dict:
    settings = client.get("/api/v1/settings").json()
    settings.update(updates)
    return client.put("/api/v1/settings", json=settings).json()


def test_confidence_threshold_gates_artwork_but_keeps_history(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, confidence_threshold=0.9)
        low = client.post("/api/v1/detections", json={
            "common_name": "Quiet Finch", "scientific_name": "Fringilla quietus",
            "confidence": 0.4, "source_event_id": "low",
        })
        assert low.status_code == 201
        current = client.get("/api/v1/display/current.json").json()
        assert current["species"] == []
        high = client.post("/api/v1/detections", json={
            "common_name": "Loud Thrush", "scientific_name": "Turdus loudus",
            "confidence": 0.99, "source_event_id": "high",
        })
        assert high.status_code == 201
        current = client.get("/api/v1/display/current.json").json()
        assert [item["common_name"] for item in current["species"]] == ["Loud Thrush"]
        names = {item["common_name"] for item in client.get("/api/v1/detections").json()}
        assert names == {"Quiet Finch", "Loud Thrush"}
        recent = client.get("/api/v1/birds/recent").json()
        assert [item["common_name"] for item in recent] == ["Loud Thrush"]


def test_duplicate_cooldown_skips_rerender_for_repeat_species(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, duplicate_cooldown_minutes=60)

        def revision() -> int:
            return client.get("/api/v1/compositions/current").json()["revision"]

        before = revision()
        client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "one",
        })
        after_first = revision()
        assert after_first > before
        second = client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "two",
        })
        assert second.status_code == 201
        assert revision() == after_first
        client.post("/api/v1/detections", json={
            "common_name": "Eurasian Magpie", "scientific_name": "Pica pica",
            "confidence": 1.0, "source_event_id": "three",
        })
        assert revision() > after_first


def test_display_exclusion_reason_distinguishes_confidence_and_cooldown(tmp_path):
    app = create_app(tmp_path)
    service = app.state.service
    low = DetectionCreate(common_name="Quiet Finch", scientific_name="Fringilla quietus", confidence=0.7)
    assert service.display_exclusion_reason(low) == "confidence 0.700 below threshold 0.800"
    service.store.add_detection(DetectionCreate(
        common_name="Common Swift", scientific_name="Apus apus", confidence=0.99,
        source_event_id="first-swift", detected_at=datetime.now(UTC),
    ))
    repeat = DetectionCreate(common_name="Common Swift", scientific_name="Apus apus", confidence=0.957)
    assert service.display_exclusion_reason(repeat) == "duplicate cooldown (5 min)"


def test_quiet_hours_window_overnight_and_timezone():
    base = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    overnight = PublicSettings(timezone="UTC", quiet_hours_start="22:30", quiet_hours_end="07:00")
    assert in_quiet_hours(overnight, base.replace(hour=23)) is True
    assert in_quiet_hours(overnight, base.replace(hour=3)) is True
    assert in_quiet_hours(overnight, base.replace(hour=12)) is False

    same_day = PublicSettings(timezone="UTC", quiet_hours_start="13:00", quiet_hours_end="14:00")
    assert in_quiet_hours(same_day, base.replace(hour=13, minute=30)) is True
    assert in_quiet_hours(same_day, base.replace(hour=14, minute=30)) is False

    assert in_quiet_hours(PublicSettings(quiet_hours_start="", quiet_hours_end="")) is False
    assert in_quiet_hours(PublicSettings(quiet_hours_start="nope", quiet_hours_end="07:00")) is False

    norway = PublicSettings(timezone="Europe/Oslo", quiet_hours_start="22:00", quiet_hours_end="06:00")
    assert in_quiet_hours(norway, datetime(2026, 7, 31, 21, 0, tzinfo=UTC)) is True
    assert in_quiet_hours(norway, datetime(2026, 7, 31, 14, 0, tzinfo=UTC)) is False


class FakeEO1:
    """Test double for EO1Client; records the URLs it is asked to show."""
    shown: list[tuple[str, str]] = []

    def __init__(self, host: str, **kwargs):
        self.host = host
        self.base_url = f"http://{host}:12345/"

    async def show(self, url):
        FakeEO1.shown.append((self.host, url))
        return {"mode": "single"}

    async def aclose(self):
        return None


EO1_SETTINGS = {"eo1_host": "10.0.0.3", "birdframe_url": "http://birdframe.test:8000/"}


@pytest.mark.asyncio
async def test_automatic_display_sync_respects_quiet_hours(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, display_auto_update_enabled=True, display_update_minutes=1, **EO1_SETTINGS)
        client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "one",
        })
        service = app.state.service
        monkeypatch.setattr(birdframe_main, "EO1Client", FakeEO1)

        now = datetime.now(UTC)
        _settings(client,
                  quiet_hours_start=(now - timedelta(minutes=5)).strftime("%H:%M"),
                  quiet_hours_end=(now + timedelta(minutes=5)).strftime("%H:%M"))
        FakeEO1.shown = []
        await service.sync_display_if_due()
        assert FakeEO1.shown == []

        _settings(client, quiet_hours_start="", quiet_hours_end="")
        await service.sync_display_if_due()
        revision = client.get("/api/v1/compositions/current").json()["revision"]
        assert FakeEO1.shown == [("10.0.0.3", f"http://birdframe.test:8000/api/v1/display/compositions/{revision}.jpg")]

        await service.sync_display_if_due()
        assert len(FakeEO1.shown) == 1  # already showing the current revision


@pytest.mark.asyncio
async def test_automatic_display_sync_waits_for_the_update_interval(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, display_update_minutes=30, **EO1_SETTINGS)
        monkeypatch.setattr(birdframe_main, "EO1Client", FakeEO1)
        FakeEO1.shown = []
        service = app.state.service
        await service.sync_display_if_due()
        assert len(FakeEO1.shown) == 1
        client.post("/api/v1/compositions/rebuild")
        await service.sync_display_if_due()
        assert len(FakeEO1.shown) == 1  # newer revision waits for the 30-minute interval


def test_eo1_push_is_queued_as_a_background_job(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, **EO1_SETTINGS)
        assert client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "push-one",
        }).status_code == 201
        monkeypatch.setattr(birdframe_main, "EO1Client", FakeEO1)
        FakeEO1.shown = []
        response = client.post("/api/v1/eo1/push")
        assert response.status_code == 202
        job_id = response.json()["id"]
        job = None
        for _ in range(30):
            job = next((item for item in client.get("/api/v1/jobs").json() if item["id"] == job_id), None)
            if job and job["status"] != "running":
                break
            time.sleep(0.1)
        assert job is not None
        assert job["kind"] == "eo1_push"
        assert job["status"] == "completed"
        assert len(FakeEO1.shown) == 1
        logs = client.get("/api/v1/logs", params={"limit": 10}).json()
        assert any("EO1 push job" in entry["message"] for entry in logs)

        # The frame fetches exactly the URL it was given.
        image = client.get(FakeEO1.shown[0][1].removeprefix("http://birdframe.test:8000"))
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert client.get("/api/v1/display/compositions/999.jpg").status_code == 404


def test_eo1_push_needs_host_and_birdframe_address(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        assert "EO1 host" in client.post("/api/v1/eo1/push").json()["detail"]
        _settings(client, eo1_host="10.0.0.3")
        assert "BirdFrame address" in client.post("/api/v1/eo1/push").json()["detail"]


@pytest.mark.asyncio
async def test_eo1_url_carries_display_token_when_required(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, display_api_require_token=True, display_api_token="frame-token-0123456789", **EO1_SETTINGS)
        monkeypatch.setattr(birdframe_main, "EO1Client", FakeEO1)
        FakeEO1.shown = []
        result = await app.state.service.push_to_display()
        assert FakeEO1.shown[0][1].endswith("?token=frame-token-0123456789")
        assert "token" not in result["url"]


def _current(client: TestClient) -> dict:
    return client.get("/api/v1/compositions/current").json()


def _shown_revisions() -> set[int]:
    return {int(url.rsplit("/", 1)[1].removesuffix(".jpg")) for _host, url in FakeEO1.shown}


@pytest.mark.asyncio
async def test_visitor_spotlight_shows_each_bird_then_resumes_the_collage(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        monkeypatch.setattr(birdframe_main, "EO1Client", FakeEO1)
        FakeEO1.shown = []
        _settings(client, display_update_minutes=30, duplicate_cooldown_minutes=0,
                  visitor_spotlight_enabled=True, visitor_spotlight_minutes=5, **EO1_SETTINGS)
        service = app.state.service
        await service.sync_display_if_due()
        collage = _current(client)
        assert collage["mode"] == "collage"

        client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "swift-1",
        })
        swift = _current(client)
        assert swift["mode"] == "spotlight"
        assert [item["common_name"] for item in swift["species"]] == ["Common Swift"]
        await service.sync_display_if_due()
        assert swift["revision"] in _shown_revisions()  # ignores the 30-minute interval

        # The same species again only extends the window; nothing new is sent.
        client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus",
            "confidence": 1.0, "source_event_id": "swift-2",
        })
        assert _current(client)["revision"] == swift["revision"]

        client.post("/api/v1/detections", json={
            "common_name": "Eurasian Magpie", "scientific_name": "Pica pica",
            "confidence": 1.0, "source_event_id": "magpie",
        })
        magpie = _current(client)
        assert magpie["mode"] == "spotlight"
        assert [item["common_name"] for item in magpie["species"]] == ["Eurasian Magpie"]
        await service.sync_display_if_due()
        assert magpie["revision"] in _shown_revisions()
        assert service.spotlight_until - datetime.now(UTC) > timedelta(minutes=4)

        service.spotlight_until = datetime.now(UTC) - timedelta(seconds=1)
        await service.expire_spotlight()
        resumed = _current(client)
        assert resumed["mode"] == "collage"
        assert {item["common_name"] for item in resumed["species"]} == {"Common Swift", "Eurasian Magpie"}
        await service.sync_display_if_due()
        assert resumed["revision"] in _shown_revisions()


def test_visitor_spotlight_ignores_old_detections_and_latest_visitor_mode(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        authed(client)
        _settings(client, visitor_spotlight_enabled=True, visitor_spotlight_minutes=5)
        client.post("/api/v1/detections", json={
            "common_name": "Common Swift", "scientific_name": "Apus apus", "confidence": 1.0,
            "source_event_id": "old", "detected_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        })
        assert _current(client)["mode"] == "collage"

        _settings(client, display_mode="latest_visitor")
        client.post("/api/v1/detections", json={
            "common_name": "Eurasian Magpie", "scientific_name": "Pica pica",
            "confidence": 1.0, "source_event_id": "new",
        })
        assert _current(client)["mode"] == "latest_visitor"
