"""Failure run should mark unknown and update totals."""

import sqlite3
import time
from freezegun import freeze_time


def test_failure_run_marks_unknown_and_updates_totals(manager):
    tracker = manager.playtime_tracker
    assert tracker is not None

    with freeze_time("2025-01-01 02:00:00"):
        base = int(time.time())
        sid = tracker.start_session(
            capsule_id="b1",
            capsule_name="Capsule",
            capsule_path="/capsule",
            program_name="Game",
            program_path="C:/Game/game.exe",
        )
        # Simulate failure
        tracker.mark_failure(sid, status="unknown")

    con = sqlite3.connect(tracker.db_path)
    cur = con.cursor()
    cur.execute("SELECT status FROM sessions WHERE id=?", (sid,))
    assert cur.fetchone()[0] == "unknown"
    cur.execute(
        "SELECT total_seconds, sessions_count FROM playtime_totals WHERE capsule_id=?",
        ("b1",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] == 1


def test_multiple_failures_different_capsules(manager):
    tracker = manager.playtime_tracker
    assert tracker is not None

    with freeze_time("2025-01-01 03:00:00"):
        # Capsule b1
        sid1 = tracker.start_session(
            capsule_id="b1",
            capsule_name="Capsule1",
            capsule_path="/b1",
            program_name="Game",
            program_path="C:/Game/game.exe",
        )
        tracker.mark_failure(sid1, status="unknown")

        # Capsule b2 (same program path, different capsule)
        sid2 = tracker.start_session(
            capsule_id="b2",
            capsule_name="Capsule2",
            capsule_path="/b2",
            program_name="Game",
            program_path="C:/Game/game.exe",
        )
        tracker.mark_failure(sid2, status="unknown")

    con = sqlite3.connect(tracker.db_path)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM playtime_totals")
    assert cur.fetchone()[0] == 2
    # each should have sessions_count=1
    cur.execute("SELECT sessions_count FROM playtime_totals WHERE capsule_id=?", ("b1",))
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT sessions_count FROM playtime_totals WHERE capsule_id=?", ("b2",))
    assert cur.fetchone()[0] == 1


def test_multiple_failures_same_capsule_different_programs(manager):
    tracker = manager.playtime_tracker
    assert tracker is not None

    with freeze_time("2025-01-01 04:00:00"):
        sid1 = tracker.start_session(
            capsule_id="b1",
            capsule_name="Capsule",
            capsule_path="/capsule",
            program_name="Game1",
            program_path="C:/Game1/game1.exe",
        )
        tracker.mark_failure(sid1, status="unknown")

        sid2 = tracker.start_session(
            capsule_id="b1",
            capsule_name="Capsule",
            capsule_path="/capsule",
            program_name="Game2",
            program_path="C:/Game2/game2.exe",
        )
        tracker.mark_failure(sid2, status="unknown")

    con = sqlite3.connect(tracker.db_path)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM playtime_totals WHERE capsule_id=?", ("b1",))
    assert cur.fetchone()[0] == 2


