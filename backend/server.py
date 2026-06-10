#!/usr/bin/env python3
"""Trend-Radar Server — Port 9126"""

import json
import os
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from database import get_db, init_db, seed_dummy_data, migrate_db, set_processing_step, DB_PATH
from pipeline import process_url

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"
PORT = 9126


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length).decode())
        return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # --- API Routes ---
        if path == "/api/health":
            self._send_json({"status": "ok", "port": PORT})
            return

        # /api/entries/:id (must come before /api/entries exact match due to routing order)
        if path.startswith("/api/entries/"):
            parts = path.split("/")
            if len(parts) == 4:
                entry_id = parts[3]
                db = get_db()
                row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                db.close()
                if row:
                    entry = dict(row)
                    if entry.get("analysis"):
                        try:
                            entry["analysis"] = json.loads(entry["analysis"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    self._send_json(entry)
                else:
                    self._send_json({"error": "Not Found"}, 404)
                return

        if path == "/api/entries":
            db = get_db()
            status_filter = params.get("status", [None])[0]
            source_filter = params.get("source", [None])[0]
            limit = int(params.get("limit", [100])[0])

            query = "SELECT * FROM entries"
            conditions = []
            args = []
            if status_filter:
                conditions.append("status = ?")
                args.append(status_filter)
            if source_filter:
                conditions.append("source_type = ?")
                args.append(source_filter)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT ?"
            args.append(limit)

            rows = db.execute(query, args).fetchall()
            entries = [dict(r) for r in rows]
            # Parse analysis JSON for frontend
            for e in entries:
                if e.get("analysis"):
                    try:
                        e["analysis"] = json.loads(e["analysis"])
                    except (json.JSONDecodeError, TypeError):
                        pass
            db.close()
            self._send_json(entries)
            return

        if path == "/api/sources":
            db = get_db()
            rows = db.execute("SELECT * FROM sources ORDER BY name").fetchall()
            db.close()
            self._send_json([dict(r) for r in rows])
            return

        if path == "/api/scan/status":
            db = get_db()
            row = db.execute(
                "SELECT created_at as last_scan FROM sources ORDER BY last_scanned_at DESC LIMIT 1"
            ).fetchone()
            count = db.execute("SELECT COUNT(*) as total FROM entries").fetchone()["total"]
            unread = db.execute("SELECT COUNT(*) as cnt FROM entries WHERE status='unread'").fetchone()["cnt"]
            db.close()
            self._send_json({
                "last_scan": row["last_scan"] if row else None,
                "total_entries": count,
                "unread_entries": unread,
                "next_scan": "Noch kein Cron-Scanner aktiv",
            })
            return

        # --- CIO API ---
        if path == "/api/cio/entries":
            db = get_db()
            rows = db.execute(
                "SELECT id, title, source_type, url, status, analysis, created_at "
                "FROM entries WHERE status != 'discussed' "
                "ORDER BY created_at DESC"
            ).fetchall()
            entries = []
            for r in rows:
                e = dict(r)
                if e.get("analysis"):
                    try:
                        e["analysis"] = json.loads(e["analysis"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                entries.append(e)
            db.close()
            self._send_json(entries)
            return

        # --- Static Frontend ---
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/entries":
            body = self._read_body()
            url = body.get("url", "").strip()
            if not url:
                self._send_json({"error": "url is required"}, 400)
                return
            source_type = body.get("source_type", "web")
            title = body.get("title", "")

            db = get_db()
            try:
                db.execute(
                    "INSERT OR IGNORE INTO entries (source_type, url, title, status) VALUES (?, ?, ?, 'unread')",
                    (source_type, url, title)
                )
                db.commit()
                entry_row = db.execute("SELECT id, source_type FROM entries WHERE url = ?", (url,)).fetchone()
                db.close()
                if entry_row:
                    entry_id = entry_row["id"]
                    source_type = entry_row["source_type"]

                    # Trigger pipeline in background for supported types
                    is_youtube = "youtube" in url or "youtu.be" in url
                    if source_type == "youtube" or is_youtube:
                        # Set processing status immediately
                        db2 = get_db()
                        db2.execute(
                            "UPDATE entries SET status = 'processing', processing_step = 'queued' WHERE id = ?",
                            (entry_id,)
                        )
                        db2.commit()
                        db2.close()
                        threading.Thread(
                            target=_process_entry_background,
                            args=(entry_id, url, source_type, title),
                            daemon=True
                        ).start()

                    self._send_json({"id": entry_id, "message": "Eintrag erstellt"}, 201)
                else:
                    self._send_json({"message": "Eintrag existiert bereits"}, 200)
            except Exception as e:
                db.close()
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/sources":
            body = self._read_body()
            required = ["type", "url"]
            if not all(k in body for k in required):
                self._send_json({"error": "type and url are required"}, 400)
                return
            db = get_db()
            db.execute(
                "INSERT INTO sources (type, url, name, interval) VALUES (?, ?, ?, ?)",
                (body["type"], body["url"], body.get("name", ""), body.get("interval", "daily"))
            )
            db.commit()
            source_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.close()
            self._send_json({"id": source_id, "message": "Quelle hinzugefügt"}, 201)
            return

        if path == "/api/scan/now":
            # Placeholder — real cron scanner comes in Issue #6
            self._send_json({"message": "Scan gestartet (Platzhalter — echter Scanner folgt in Issue #6)"})
            return

        self._send_json({"error": "Not Found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # /api/cio/entries/:id
        if path.startswith("/api/cio/entries/"):
            parts = path.split("/")
            if len(parts) == 4:
                entry_id = parts[3]
                body = self._read_body()
                new_status = body.get("status")
                if new_status not in ("read", "discussed"):
                    self._send_json({"error": "status must be 'read' or 'discussed'"}, 400)
                    return
                db = get_db()
                db.execute("UPDATE entries SET status = ? WHERE id = ?", (new_status, entry_id))
                db.commit()
                db.close()
                self._send_json({"message": f"Status auf '{new_status}' gesetzt"})
                return

        self._send_json({"error": "Not Found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/sources/"):
            parts = path.split("/")
            if len(parts) == 4:
                source_id = parts[3]
                db = get_db()
                db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                db.commit()
                db.close()
                self._send_json({"message": "Quelle gelöscht"})
                return

        self._send_json({"error": "Not Found"}, 404)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")


def _process_entry_background(entry_id: int, url: str, source_type: str, title: str):
    """Background worker: run pipeline, update entry with transcript + analysis + Obsidian note."""
    print(f"🧵 Starte Pipeline für Entry #{entry_id}: {url[:60]}...")

    set_processing_step(entry_id, "downloading")
    result = process_url(url, source_type, title, entry_id=entry_id)

    if result.get("status") == "error":
        print(f"⚠️  Pipeline fehlgeschlagen für #{entry_id}: {result.get('error')}")
        set_processing_step(entry_id, None)  # reset to unread
        return

    transcript = result.get("transcript")
    analysis = result.get("analysis")
    obsidian_note = result.get("obsidian_note")
    thumbnail_url = result.get("thumbnail_url")
    channel = result.get("channel")

    if not transcript and not analysis:
        # Not a YouTube URL or nothing extracted — just reset
        set_processing_step(entry_id, None)
        return

    set_processing_step(entry_id, "saving")
    db = get_db()
    try:
        if transcript:
            db.execute("UPDATE entries SET content = ? WHERE id = ?",
                       (transcript, entry_id))
        if analysis:
            db.execute("UPDATE entries SET analysis = ? WHERE id = ?",
                       (json.dumps(analysis), entry_id))
        if obsidian_note:
            db.execute("UPDATE entries SET obsidian_note_path = ? WHERE id = ?",
                       (obsidian_note, entry_id))
        if thumbnail_url:
            db.execute("UPDATE entries SET thumbnail_url = ? WHERE id = ?",
                       (thumbnail_url, entry_id))
        if channel:
            db.execute("UPDATE entries SET author = ? WHERE id = ?",
                       (channel, entry_id))
        db.commit()
        print(f"✅ Pipeline abgeschlossen für Entry #{entry_id}")
        if thumbnail_url:
            print(f"   🖼️ Thumbnail: {thumbnail_url[:60]}...")
        if obsidian_note:
            print(f"   📝 Obsidian: {obsidian_note}")
    except Exception as e:
        print(f"❌ Pipeline-Update fehlgeschlagen für #{entry_id}: {e}")
    finally:
        db.close()
    # Done — reset processing status
    set_processing_step(entry_id, None)


def main():
    # Ensure DB exists
    if not DB_PATH.exists():
        print("📦 Initialisiere Datenbank...")
        init_db()
        seed_dummy_data()
    else:
        migrate_db()  # non-destructive upgrades

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🚀 Trend-Radar läuft auf http://0.0.0.0:{PORT}")
    print(f"   API:    http://localhost:{PORT}/api/health")
    print(f"   DB:     {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer gestoppt.")
        server.server_close()


if __name__ == "__main__":
    main()