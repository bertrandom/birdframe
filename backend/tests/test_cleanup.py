import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from birdframe.storage import Store


def _insert_composition(store: Store, *, revision: int, created_at: datetime, path: Path) -> None:
    with store.connection() as db:
        db.execute(
            """INSERT INTO compositions(revision, mode, width, height, path, sha256, species_json, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (revision, "collage", 640, 360, str(path), "0" * 64, "[]", created_at.isoformat()),
        )


def _insert_log(store: Store, created_at: datetime, message: str = "test event") -> None:
    with store.connection() as db:
        db.execute("INSERT INTO logs(level,message,created_at) VALUES('INFO',?,?)", (message, created_at.isoformat()))


def _insert_display_push(store: Store, composition_id: int, created_at: datetime) -> None:
    with store.connection() as db:
        db.execute(
            "INSERT INTO display_pushes(composition_id,created_at) VALUES(?,?)",
            (composition_id, created_at.isoformat()),
        )


def _revisions(store: Store) -> list[int]:
    with store.connection() as db:
        return [row["revision"] for row in db.execute("SELECT revision FROM compositions ORDER BY revision")]


def _log_count(store: Store) -> int:
    with store.connection() as db:
        return int(db.execute("SELECT COUNT(*) FROM logs").fetchone()[0])


def test_cleanup_keeps_one_composition_per_day_for_one_year(tmp_path: Path):
    store = Store(tmp_path)
    now = datetime.now(UTC)
    files: dict[int, Path] = {}

    def add(revision: int, when: datetime) -> None:
        path = tmp_path / f"composition-{revision}.jpg"
        path.write_bytes(b"jpeg")
        files[revision] = path
        _insert_composition(store, revision=revision, created_at=when, path=path)

    add(1, now - timedelta(days=400))            # older than the retention window
    add(2, now - timedelta(days=2, hours=1))     # same day as 3, older revision
    add(3, now - timedelta(days=2))              # latest of that day: kept
    add(4, now - timedelta(days=1))
    add(5, now)                                  # today's/current composition: always kept
    _insert_display_push(store, 2, now - timedelta(days=2))
    _insert_display_push(store, 5, now)
    _insert_log(store, now - timedelta(days=40))
    _insert_log(store, now - timedelta(days=1))

    result = store.cleanup()

    assert result == {"compositions": 2, "files": 2, "logs": 1}
    assert _revisions(store) == [3, 4, 5]
    assert not files[1].exists() and not files[2].exists()
    assert files[3].exists() and files[4].exists() and files[5].exists()
    assert _log_count(store) == 1
    with store.connection() as db:
        remaining = [row["composition_id"] for row in db.execute("SELECT composition_id FROM display_pushes")]
    assert remaining == [5]


def test_cleanup_is_idempotent_and_tolerates_missing_files(tmp_path: Path):
    store = Store(tmp_path)
    now = datetime.now(UTC)
    missing = tmp_path / "composition-ghost.jpg"  # row exists but file was already removed
    _insert_composition(store, revision=1, created_at=now - timedelta(days=400), path=missing)
    _insert_log(store, now - timedelta(days=100))

    first = store.cleanup()
    second = store.cleanup()

    assert first["compositions"] == 1 and first["files"] == 0 and first["logs"] == 1
    assert second == {"compositions": 0, "files": 0, "logs": 0}
    assert _revisions(store) == []


def test_samsung_era_database_is_migrated(tmp_path: Path):
    with sqlite3.connect(tmp_path / "birdframe.db") as db:
        db.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, secret INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE compositions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL UNIQUE, mode TEXT NOT NULL,
              width INTEGER NOT NULL, height INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
              species_json TEXT NOT NULL, created_at TEXT NOT NULL, tv_confirmed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE tv_uploads (id INTEGER PRIMARY KEY, composition_id INTEGER, content_id TEXT, created_at TEXT);
            INSERT INTO compositions(revision,mode,width,height,path,sha256,species_json,created_at,tv_confirmed)
              VALUES(1,'collage',640,360,'a.jpg','0','[]','2026-01-01T00:00:00+00:00',1);
            INSERT INTO settings(key,value,secret) VALUES
              ('tv_host','"10.0.0.3"',0), ('tv_mac','"aa:bb:cc:dd:ee:ff"',0), ('tv_update_minutes','15',0),
              ('tv_auto_update_enabled','false',0), ('tv_quiet_hours_start','"22:00"',0), ('samsung_token','x',1);
            """
        )
    store = Store(tmp_path)
    settings = store.get_settings()
    assert settings.display_update_minutes == 15
    assert settings.display_auto_update_enabled is False
    assert settings.quiet_hours_start == "22:00"
    assert store.current_composition()["revision"] == 1
    with store.connection() as db:
        keys = {row["key"] for row in db.execute("SELECT key FROM settings")}
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row["name"] for row in db.execute("PRAGMA table_info(compositions)")}
    assert not any(key.startswith("tv_") or key == "samsung_token" for key in keys)
    assert "tv_uploads" not in tables and "display_pushes" in tables
    assert "tv_confirmed" not in columns
