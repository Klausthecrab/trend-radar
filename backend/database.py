#!/usr/bin/env python3
"""Trend-Radar: SQLite database setup and helpers."""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
DATA_DIR = Path(os.environ.get('TREND_DATA_DIR', str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "trend-radar.db"


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def set_processing_step(entry_id: int, step: str | None):
    """Update processing_step and optionally status for an entry."""
    try:
        db = get_db()
        if step is None:
            db.execute("UPDATE entries SET status = 'unread', processing_step = NULL WHERE id = ?", (entry_id,))
        else:
            db.execute("UPDATE entries SET processing_step = ? WHERE id = ?", (step, entry_id))
        db.commit()
        db.close()
    except Exception as e:
        print(f"⚠️  set_processing_step Fehler für #{entry_id}: {e}")


def migrate_db():
    """Add columns that may not exist yet (non-destructive upgrades)."""
    conn = get_db()
    cursor = conn.execute("PRAGMA table_info(entries)")
    existing = {row[1] for row in cursor.fetchall()}
    if "processing_step" not in existing:
        conn.execute("ALTER TABLE entries ADD COLUMN processing_step TEXT")
        print("📦 Migration: processing_step hinzugefügt")
    if "thumbnail_url" not in existing:
        conn.execute("ALTER TABLE entries ADD COLUMN thumbnail_url TEXT")
        print("📦 Migration: thumbnail_url hinzugefügt")
    if "language" not in existing:
        conn.execute("ALTER TABLE entries ADD COLUMN language TEXT DEFAULT 'de'")
        print("📦 Migration: language hinzugefügt")
    conn.commit()
    conn.close()


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            name TEXT,
            interval TEXT DEFAULT 'daily',
            enabled INTEGER DEFAULT 1,
            last_scanned_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            source_type TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            content TEXT,
            author TEXT,
            published_at TEXT,
            analysis TEXT,
            obsidian_note_path TEXT,
            status TEXT DEFAULT 'unread',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES sources(id)
        );

        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            relevance_score INTEGER,
            summary TEXT,
            takeaways TEXT,
            action_items TEXT,
            obsidian_note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        );

        CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);
        CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source_type);
        CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at);
    """)
    conn.commit()
    conn.close()


def seed_dummy_data():
    """Insert dummy entries so the frontend has something to show."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if count > 0:
        conn.close()
        return  # already seeded

    dummies = [
        {
            "source_type": "reddit",
            "url": "https://reddit.com/r/selfhosted/comments/1abc/",
            "title": "Immich v2.0 veröffentlicht – Self-Hosted Google Photos Alternative",
            "content": "Immich 2.0 ist da! Neue KI-gestützte Gesichtserkennung, verbesserte Suche und ein komplett überarbeitetes Mobile-UI. Läuft auf Docker, empfiehlt 4GB RAM + GPU für ML-Features.",
            "author": "u/selfhosted_fan",
            "published_at": "2026-06-08T14:30:00",
            "analysis": json.dumps({
                "relevance": 4,
                "takeaways": ["Immich ist mittlerweile ernstzunehmende Google-Photos-Alternative", "KI-Features brauchen GPU"],
                "action_items": ["Test-Instanz auf Butler aufsetzen", "Nächste Butler-NAS-Backup-Runde einplanen"]
            }),
            "status": "unread"
        },
        {
            "source_type": "youtube",
            "url": "https://youtube.com/watch?v=dummy1",
            "title": "Home Assistant Voice Assistant – KompletterSetup-Guide",
            "content": "In diesem Video zeigt der Creator wie man einen vollständigen lokalen Voice-Assistant mit Home Assistant aufsetzt. Wyoming-Integration, Whisper STT, Piper TTS. Läuft alles lokal, kein Cloud-Zwang.",
            "author": "SmartHomeChannel",
            "published_at": "2026-06-07T10:00:00",
            "analysis": json.dumps({
                "relevance": 3,
                "takeaways": ["Lokaler Voice-Assistant ist reif für Homelab", "Braucht mind. 8GB RAM + GPU"],
                "action_items": ["Prüfen ob Home Assistant auf Butler läuft", "Whisper-Modell runterladen"]
            }),
            "status": "unread"
        },
        {
            "source_type": "rss",
            "url": "https://blog.example.com/opentelemetry-update",
            "title": "OpenTelemetry 2.0: Neue Features für Self-Hosted Monitoring",
            "content": "OpenTelemetry 2.0 bringt native Unterstützung für LLM-Tracing, verbesserte Sampling-Strategien und eine neue Batch-Export-API. Ermöglicht detaillierte Einblicke in KI-Pipelines ohne Vendor-Lock-in.",
            "author": "CNCF Blog",
            "published_at": "2026-06-06T08:00:00",
            "analysis": json.dumps({
                "relevance": 2,
                "takeaways": ["OTel 2.0 könnte für Hermes-Monitoring interessant sein", "LLM-Tracing wäre nützlich für CIO-Debugging"],
                "action_items": ["OTel-Collector auf Butler testen", "Hermes-Logs an OTel anbinden"]
            }),
            "status": "read"
        },
    ]
    for d in dummies:
        conn.execute(
            """INSERT OR IGNORE INTO entries
               (source_type, url, title, content, author, published_at, analysis, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (d["source_type"], d["url"], d["title"], d["content"],
             d["author"], d["published_at"], d["analysis"], d["status"])
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    seed_dummy_data()
    print("✅ Trend-Radar DB initialisiert mit Dummy-Daten")