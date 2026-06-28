#!/usr/bin/env python3
"""Trend-Radar Server — Port 9126"""

import json
import os
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from database import get_db, init_db, seed_dummy_data, migrate_db, set_processing_step, get_config, set_config, init_stage_progress, DB_PATH
from trilium import RESOURCES_NOTE_ID, get_resources_structure, format_structure_for_llm

def _detect_source_type(url: str) -> str:
    """Detect source type from URL."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "reddit.com" in url_lower:
        return "reddit"
    return "web"


def _update_stage_progress(entry_id, field, value=True):
    """Update a single field in stage_progress JSON and commit."""
    db = get_db()
    try:
        row = db.execute("SELECT stage_progress FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        sp = json.loads(row["stage_progress"]) if row["stage_progress"] else {}
        sp[field] = value
        db.execute("UPDATE entries SET stage_progress = ? WHERE id = ?",
                   (json.dumps(sp), entry_id))
        db.commit()
    finally:
        db.close()


def _send_to_kanban(entry_id):
    """Set status + processing_step to start Kanban processing.
    This is equivalent to 'pushing the entry to the Kanban'."""
    db = get_db()
    try:
        row = db.execute("SELECT source_type, status FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        source_type = row["source_type"]
        if source_type == "youtube":
            db.execute("UPDATE entries SET status = 'transcribing', processing_step = 'extracting' WHERE id = ?",
                       (entry_id,))
        else:
            db.execute("UPDATE entries SET status = 'analyzing', processing_step = 'analyzing' WHERE id = ?",
                       (entry_id,))
        db.commit()
        print(f"📤 Entry #{entry_id} ({source_type}) ins Kanban geschickt — processing_step gesetzt")
    finally:
        db.close()


def _run_youtube_pipeline(entry_id: int):
    """YouTube Pipeline: step by step with stage_progress updates.
    Steps: extracting → transcribing → analyzing → path_suggested → filed"""
    from pipeline import get_youtube_metadata, download_youtube_audio, transcribe_audio
    from analysis import analyze_with_llm, suggest_trilium_path
    from trilium import create_trilium_note, get_resources_structure, format_structure_for_llm

    db = get_db()
    try:
        row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        entry = dict(row)
        url = entry["url"]
        title = entry.get("title", "") or ""
    finally:
        db.close()

    print(f"\n{'='*50}")
    print(f"🎬 YouTube-Pipeline #${entry_id}: {title[:50] or url[:50]}...")
    set_processing_step(entry_id, "extracting")

    # Step 1: Extract metadata
    print("  Step 1/6: Metadaten extrahieren...")
    meta = get_youtube_metadata(url)
    thumbnail = meta.get("thumbnail_url", "")
    channel = meta.get("channel", "")
    video_language = meta.get("language") or "de"
    if thumbnail or channel:
        db = get_db()
        db.execute("UPDATE entries SET thumbnail_url = ?, author = ?, language = ? WHERE id = ?",
                   (thumbnail, channel, video_language, entry_id))
        db.commit()
        db.close()
    print(f"  ✅ Metadaten: {channel} ({video_language})")
    set_processing_step(entry_id, "transcribing")

    # Step 2: Download audio
    print("  Step 2/6: Audio herunterladen...")
    audio_path = download_youtube_audio(url)
    if not audio_path:
        db = get_db()
        db.execute("UPDATE entries SET status = 'failed' WHERE id = ?", (entry_id,))
        db.commit()
        db.close()
        set_processing_step(entry_id, None)
        _update_entry_status(entry_id, "failed")
        print(f"❌ YouTube-Pipeline #${entry_id}: Download fehlgeschlagen")
        return

    # Step 3: Transcribe
    print("  Step 3/6: Transkribieren...")
    transcript = transcribe_audio(audio_path, language=video_language)
    try:
        os.unlink(audio_path)
    except OSError:
        pass

    if not transcript:
        set_processing_step(entry_id, None)
        _update_entry_status(entry_id, "failed")
        print(f"❌ YouTube-Pipeline #${entry_id}: Transkription fehlgeschlagen")
        return

    # Save transcript + mark transcribed in stage_progress
    db = get_db()
    db.execute("UPDATE entries SET content = ?, status = 'transcribed' WHERE id = ?",
               (transcript, entry_id))
    db.commit()
    db.close()
    _update_stage_progress(entry_id, "transcribed", True)
    print(f"  ✅ Transkript: {len(transcript)} Zeichen")

    # Step 4: LLM Analysis
    print("  Step 4/6: LLM-Analyse...")
    set_processing_step(entry_id, "analyzing")
    analysis = analyze_with_llm(title or url, transcript, "youtube", language=video_language)
    if not analysis:
        set_processing_step(entry_id, None)
        _update_entry_status(entry_id, "failed")
        print(f"❌ YouTube-Pipeline #${entry_id}: Analyse fehlgeschlagen")
        return

    db = get_db()
    db.execute("UPDATE entries SET analysis = ? WHERE id = ?",
               (json.dumps(analysis), entry_id))
    db.commit()
    db.close()
    _update_stage_progress(entry_id, "analyzed", True)
    _update_entry_status(entry_id, "analyzed")
    print(f"  ✅ Analyse: Relevanz {analysis.get('relevance', '?')}/5")

    # Step 5: Trilium path suggestion
    print("  Step 5/6: Trilium-Pfad vorschlagen...")
    set_processing_step(entry_id, "path_suggesting")
    try:
        tree = get_resources_structure()
        structure_str = format_structure_for_llm(tree)
        path_suggestion = suggest_trilium_path(title or url, analysis, structure_str, language=video_language)
        if path_suggestion:
            db = get_db()
            db.execute(
                "UPDATE entries SET trilium_suggested_path = ?, trilium_target_note_id = ?, status = 'path_suggested' WHERE id = ?",
                (path_suggestion.get("path", ""), path_suggestion.get("noteId", ""), entry_id)
            )
            db.commit()
            db.close()
            _update_stage_progress(entry_id, "path_found", True)
            print(f"  ✅ Pfad: {path_suggestion.get('path', '📚 Resources')}")
    except Exception as e:
        print(f"  ⚠️ Pfad-Fehler: {e}")
        db = get_db()
        db.execute("UPDATE entries SET trilium_target_note_id = ?, status = 'path_suggested' WHERE id = ?",
                   (RESOURCES_NOTE_ID, entry_id))
        db.commit()
        db.close()
        _update_stage_progress(entry_id, "path_found", True)

    # Step 6: File in Trilium
    print("  Step 6/6: In Trilium ablegen...")
    set_processing_step(entry_id, "filing")
    try:
        db = get_db()
        entry_row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        db.close()
        if entry_row:
            entry_full = dict(entry_row)
            target_note_id = entry_full.get("trilium_target_note_id") or RESOURCES_NOTE_ID
            result = create_trilium_note(entry_full, analysis, target_note_id)
            if result["status"] == "ok":
                db = get_db()
                db.execute(
                    "UPDATE entries SET status = 'filed', trilium_note_id = ?, processing_step = NULL WHERE id = ?",
                    (result.get("note_id", ""), entry_id)
                )
                db.commit()
                db.close()
                _update_stage_progress(entry_id, "filed", True)
                print(f"  ✅ Abgelegt: {result.get('path', '📚 Resources')}")
            else:
                print(f"  ⚠️ Trilium-Fehler: {result.get('error')}")
                set_processing_step(entry_id, None)
    except Exception as e:
        print(f"  ⚠️ Trilium-Fehler: {e}")
        set_processing_step(entry_id, None)

    print(f"✅ YouTube-Pipeline #${entry_id} abgeschlossen")
    print(f"{'='*50}")


def _run_web_pipeline(entry_id: int):
    """Web Pipeline: step by step with stage_progress updates.
    Steps: analyzing → path_suggested → filed"""
    from analysis import analyze_with_llm, suggest_trilium_path
    from trilium import create_trilium_note, get_resources_structure, format_structure_for_llm
    import re
    import urllib.request

    db = get_db()
    try:
        row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        entry = dict(row)
        url = entry["url"]
        title = entry.get("title", "") or ""
        language = entry.get("language", "de")
    finally:
        db.close()

    print(f"\n{'='*50}")
    print(f"🌐 Web-Pipeline #${entry_id}: {title[:50] or url[:50]}...")
    set_processing_step(entry_id, "analyzing")

    # Step 1: Fetch page content
    print("  Step 1/4: Seiteninhalt laden...")
    content = None
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        content = re.sub(r'\s+', ' ', text).strip()[:4000]
        print(f"  ✅ Content: {len(content)} Zeichen")
    except Exception as e:
        print(f"  ⚠️ Fetch-Fehler: {e}")

    if content:
        db = get_db()
        db.execute("UPDATE entries SET content = ? WHERE id = ?", (content, entry_id))
        db.commit()
        db.close()
    else:
        # Still try analysis on title only
        print("  ℹ️  Kein Content — Analyse nur anhand Titel")

    # Step 2: LLM Analysis
    print("  Step 2/4: LLM-Analyse...")
    analysis = analyze_with_llm(title, content or title, "web", language=language)
    if not analysis:
        set_processing_step(entry_id, None)
        _update_entry_status(entry_id, "failed")
        print(f"❌ Web-Pipeline #${entry_id}: Analyse fehlgeschlagen")
        return

    db = get_db()
    db.execute("UPDATE entries SET analysis = ? WHERE id = ?",
               (json.dumps(analysis), entry_id))
    db.commit()
    db.close()
    _update_stage_progress(entry_id, "analyzed", True)
    _update_entry_status(entry_id, "analyzed")
    print(f"  ✅ Analyse: Relevanz {analysis.get('relevance', '?')}/5")

    # Step 3: Trilium path suggestion
    print("  Step 3/4: Trilium-Pfad vorschlagen...")
    set_processing_step(entry_id, "path_suggesting")
    try:
        tree = get_resources_structure()
        structure_str = format_structure_for_llm(tree)
        path_suggestion = suggest_trilium_path(title or url, analysis, structure_str, language=language)
        if path_suggestion:
            db = get_db()
            db.execute(
                "UPDATE entries SET trilium_suggested_path = ?, trilium_target_note_id = ?, status = 'path_suggested' WHERE id = ?",
                (path_suggestion.get("path", ""), path_suggestion.get("noteId", ""), entry_id)
            )
            db.commit()
            db.close()
            _update_stage_progress(entry_id, "path_found", True)
            print(f"  ✅ Pfad: {path_suggestion.get('path', '📚 Resources')}")
    except Exception as e:
        print(f"  ⚠️ Pfad-Fehler: {e}")
        db = get_db()
        db.execute("UPDATE entries SET trilium_target_note_id = ?, status = 'path_suggested' WHERE id = ?",
                   (RESOURCES_NOTE_ID, entry_id))
        db.commit()
        db.close()
        _update_stage_progress(entry_id, "path_found", True)

    # Step 4: File in Trilium
    print("  Step 4/4: In Trilium ablegen...")
    set_processing_step(entry_id, "filing")
    try:
        db = get_db()
        entry_row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        db.close()
        if entry_row:
            entry_full = dict(entry_row)
            target_note_id = entry_full.get("trilium_target_note_id") or RESOURCES_NOTE_ID
            result = create_trilium_note(entry_full, analysis, target_note_id)
            if result["status"] == "ok":
                db = get_db()
                db.execute(
                    "UPDATE entries SET status = 'filed', trilium_note_id = ?, processing_step = NULL WHERE id = ?",
                    (result.get("note_id", ""), entry_id)
                )
                db.commit()
                db.close()
                _update_stage_progress(entry_id, "filed", True)
                print(f"  ✅ Abgelegt: {result.get('path', '📚 Resources')}")
            else:
                print(f"  ⚠️ Trilium-Fehler: {result.get('error')}")
                set_processing_step(entry_id, None)
    except Exception as e:
        print(f"  ⚠️ Trilium-Fehler: {e}")
        set_processing_step(entry_id, None)

    print(f"✅ Web-Pipeline #${entry_id} abgeschlossen")
    print(f"{'='*50}")


def _auto_process_entry(entry_id: int):
    """Chained auto-processing: preanalyze → kanban → pipeline.
    Replaces the old parallel threads for new entries."""
    # Phase 1: Preanalyze (metadata enrichment)
    print(f"🚀 Auto-Process #{entry_id}: Phase 1 — Preanalyze")
    _preanalyze_entry(entry_id)

    # Phase 2: Send to Kanban (set status)
    print(f"🚀 Auto-Process #{entry_id}: Phase 2 — Kanban")
    _send_to_kanban(entry_id)

    # Phase 3: Run pipeline
    db = get_db()
    try:
        row = db.execute("SELECT source_type FROM entries WHERE id = ?", (entry_id,)).fetchone()
        source_type = row["source_type"] if row else "web"
    finally:
        db.close()
    print(f"🚀 Auto-Process #{entry_id}: Phase 3 — {source_type}-Pipeline")
    if source_type == "youtube":
        _run_youtube_pipeline(entry_id)
    else:
        _run_web_pipeline(entry_id)


def _update_entry_status(entry_id, new_status):
    """Update only the status field for an entry."""
    db = get_db()
    try:
        db.execute("UPDATE entries SET status = ? WHERE id = ?", (new_status, entry_id))
        db.commit()
    finally:
        db.close()


def _fetch_page_meta(url: str) -> dict:
    """Fetch page and extract og:title, og:url, <title>.
    Returns {'title': ..., 'canonical_url': ...} — both optional.
    """
    import urllib.request
    result = {"title": None, "canonical_url": None}
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"⚠️  Meta-Fetch Fehler für {url[:60]}...: {e}")
        return result

    import re
    # Try og:title first
    m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        result["title"] = m.group(1)
    else:
        # Fallback to <title>
        m = re.search(r'<title>([^<]+)</title>', html, re.I)
        if m:
            result["title"] = m.group(1).strip()

    # Try og:url for canonical URL
    m = re.search(r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        result["canonical_url"] = m.group(1)
    else:
        # Fallback: <link rel="canonical">
        m = re.search(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', html, re.I)
        if m:
            result["canonical_url"] = m.group(1)

    return result


def _preanalyze_entry(entry_id: int):
    """Background job: fetch metadata (title, thumbnail, type) for an entry.
    Updates the DB entry and sets status to 'pre_analyzed'."""
    from database import get_db
    db = get_db()
    try:
        row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return
        entry = dict(row)
        url = entry["url"]
        title = entry["title"] or ""
        source_type = entry["source_type"]

        print(f"🧵 Pre-Analyse für Entry #{entry_id}: {url[:60]}...")

        # YouTube: use existing metadata fetcher
        if source_type == "youtube":
            from pipeline import get_youtube_metadata
            meta = get_youtube_metadata(url)
            if meta.get("thumbnail_url"):
                db.execute("UPDATE entries SET thumbnail_url = ? WHERE id = ?",
                           (meta["thumbnail_url"], entry_id))
            if meta.get("channel"):
                db.execute("UPDATE entries SET author = ? WHERE id = ?",
                           (meta["channel"], entry_id))
            if not title and meta.get("channel"):
                # We got channel but not video title — yt-dlp can give us title too
                try:
                    import subprocess
                    result = subprocess.run(
                        ["yt-dlp", "--print", "title", url],
                        capture_output=True, text=True, timeout=15
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        title = result.stdout.strip()
                        db.execute("UPDATE entries SET title = ? WHERE id = ?",
                                   (title, entry_id))
                except Exception:
                    pass
        else:
            # Non-YouTube: fetch page meta (title + canonical URL)
            meta = _fetch_page_meta(url)

            # Titel aktualisieren falls fehlend
            if not title and meta.get("title"):
                title = meta["title"]
                db.execute("UPDATE entries SET title = ? WHERE id = ?",
                           (title, entry_id))
                print(f"   📝 Titel ermittelt: {title[:60]}")

            # Kanonische URL checken und ggf. korrigieren (Issue #17)
            canonical = meta.get("canonical_url")
            if canonical and canonical != url:
                print(f"   🔗 Kanonische URL gefunden: {canonical[:80]}...")
                db.execute("UPDATE entries SET url = ? WHERE id = ?",
                           (canonical, entry_id))
                url = canonical  # fürs Log unten

        # Set status to pre_analyzed
        db.execute("UPDATE entries SET status = 'pre_analyzed' WHERE id = ?", (entry_id,))
        db.commit()
        print(f"✅ Pre-Analyse abgeschlossen für #{entry_id}: {title[:60] if title else url[:60]}...")
    except Exception as e:
        print(f"⚠️  Pre-Analyse Fehler für #{entry_id}: {e}")
    finally:
        db.close()


def _analyze_and_suggest(entry_id: int, entry: dict):
    """Background job: run LLM analysis + Trilium path suggestion.
    
    For YouTube: full pipeline (download → transcribe → analyze → suggest path)
    For non-YouTube: fetch page content → analyze → suggest path
    """
    from database import get_db
    from analysis import analyze_with_llm, suggest_trilium_path
    import re
    
    url = entry["url"]
    source_type = entry["source_type"]
    title = entry.get("title", "") or ""
    language = entry.get("language", "de")
    
    print(f"🧵 Starte Analyse für Entry #{entry_id}: {url[:60]}...")
    set_processing_step(entry_id, "analyzing")
    
    content = None
    analysis = None
    
    if source_type == "youtube" or "youtube.com" in url or "youtu.be" in url:
        # YouTube: full pipeline
        from pipeline import process_youtube
        result = process_youtube(url, title, entry_id)
        if result.get("status") == "error":
            print(f"⚠️  YouTube-Pipeline fehlgeschlagen für #{entry_id}: {result.get('error')}")
            db = get_db()
            db.execute("UPDATE entries SET status = 'failed' WHERE id = ?", (entry_id,))
            db.commit()
            db.close()
            set_processing_step(entry_id, None)
            return
        
        content = result.get("transcript", "")
        analysis = result.get("analysis")
        thumbnail = result.get("thumbnail_url")
        channel = result.get("channel")
        lang = result.get("language", language)
        
        # Store in DB
        db = get_db()
        db.execute("UPDATE entries SET content = ?, language = ?, thumbnail_url = ?, author = ? WHERE id = ?",
                    (content, lang, thumbnail, channel, entry_id))
        db.commit()
        db.close()
    else:
        # Non-YouTube: fetch page and extract text
        import urllib.request
        print(f"🌐 Fetching page content: {url[:60]}...")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            
            # Simple HTML-to-text: strip tags, keep text
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            content = text[:4000]  # Cap at 4000 chars
            
            print(f"   📄 Extrahiert: {len(content)} Zeichen Content")
            
            # Store content in DB
            db = get_db()
            db.execute("UPDATE entries SET content = ? WHERE id = ?", (content, entry_id))
            db.commit()
            db.close()
        except Exception as e:
            print(f"⚠️  Page-Fetch Fehler für #{entry_id}: {e}")
            content = None
    
    # Step 2: LLM Analysis
    if content and not analysis:
        analysis = analyze_with_llm(title, content, source_type, language=language)
    
    if analysis:
        # Store analysis in DB
        db = get_db()
        db.execute("UPDATE entries SET analysis = ? WHERE id = ?",
                   (json.dumps(analysis), entry_id))
        db.commit()
        db.close()
        print(f"✅ Analyse abgeschlossen für #{entry_id}: Relevanz {analysis.get('relevance', '?')}/5")
    else:
        print(f"⚠️  Keine Analyse für #{entry_id} — kein Content verfügbar")
        db = get_db()
        db.execute("UPDATE entries SET status = 'failed' WHERE id = ?", (entry_id,))
        db.commit()
        db.close()
        set_processing_step(entry_id, None)
        return
    
    # Step 3: Trilium path suggestion
    print(f"🗺️  Ermittle Trilium-Pfad für #{entry_id}...")
    try:
        tree = get_resources_structure()
        structure_str = format_structure_for_llm(tree)
        path_suggestion = suggest_trilium_path(title, analysis, structure_str, language=language)
        
        if path_suggestion:
            db = get_db()
            db.execute(
                "UPDATE entries SET trilium_suggested_path = ?, trilium_target_note_id = ? WHERE id = ?",
                (path_suggestion.get("path", ""), path_suggestion.get("noteId", ""), entry_id)
            )
            db.commit()
            db.close()
            print(f"✅ Trilium-Pfad: {path_suggestion.get('path', '📚 Resources')}")
    except Exception as e:
        print(f"⚠️  Trilium-Pfad-Fehler für #{entry_id}: {e}")
    
    # Set status to analyzed
    db = get_db()
    db.execute("UPDATE entries SET status = 'analyzed', processing_step = NULL WHERE id = ?", (entry_id,))
    db.commit()
    db.close()
    print(f"✅ Entry #{entry_id} ist bereit für Trilium-Ablage")


# ─── Telegram Bot Poller ─────────────────────────────

TELEGRAM_API = "https://api.telegram.org/bot"
POLL_INTERVAL = 180  # 3 Minuten

def _load_telegram_token() -> str:
    """Load Telegram bot token from data/telegram-bot-token.txt"""
    token_path = ROOT / "data" / "telegram-bot-token.txt"
    try:
        if token_path.exists():
            return token_path.read_text().strip()
    except Exception as e:
        print(f"⚠️  Telegram-Token Fehler: {e}")
    return ""


def _extract_urls(text: str) -> list[str]:
    """Extract http/https URLs from text."""
    import re
    return re.findall(r'https?://[^\s<>"\']+', text)


def _save_telegram_url(url: str, sender_name: str, message_id: int, chat_id: int):
    """Save a Telegram URL to the inbox table."""
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM telegram_inbox WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id)
        ).fetchone()
        if existing:
            return existing["id"]
        db.execute(
            "INSERT OR IGNORE INTO telegram_inbox (url, sender_name, message_id, chat_id) VALUES (?, ?, ?, ?)",
            (url, sender_name, message_id, chat_id)
        )
        db.commit()
        row = db.execute("SELECT id FROM telegram_inbox WHERE message_id = ? AND chat_id = ?",
                         (message_id, chat_id)).fetchone()
        return row["id"] if row else None
    finally:
        db.close()


def _telegram_poller():
    """Periodically fetch new messages from Telegram bot and save URLs."""
    last_update_id = 0
    token = _load_telegram_token()
    if not token:
        print("⚠️  Kein Telegram-Token — Poller deaktiviert")
        return

    print(f"📨 Telegram-Poller gestartet (Intervall: {POLL_INTERVAL}s)")
    while True:
        try:
            import urllib.request
            import json
            url = f"{TELEGRAM_API}{token}/getUpdates?offset={last_update_id + 1}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())

            if data.get("ok"):
                for update in data.get("result", []):
                    update_id = update.get("update_id", 0)
                    if update_id > last_update_id:
                        last_update_id = update_id

                    msg = update.get("message", {})
                    if not msg:
                        continue

                    text = msg.get("text", "") or msg.get("caption", "")
                    if not text:
                        continue

                    urls = _extract_urls(text)
                    if not urls:
                        continue

                    chat_id = msg.get("chat", {}).get("id", 0)
                    message_id = msg.get("message_id", 0)
                    sender = msg.get("from", {}).get("first_name", "Unbekannt")

                    for found_url in urls:
                        _save_telegram_url(found_url, sender, message_id, chat_id)

                    # Reply to user: acknowledge receipt
                    reply_url = f"{TELEGRAM_API}{token}/sendMessage"
                    reply_data = json.dumps({
                        "chat_id": chat_id,
                        "text": "✅ Erhalten! Wird in Trend Radar verarbeitet.",
                        "reply_to_message_id": message_id,
                    }).encode()
                    reply_req = urllib.request.Request(reply_url, data=reply_data,
                                                       headers={"Content-Type": "application/json"})
                    try:
                        urllib.request.urlopen(reply_req, timeout=10)
                    except Exception:
                        pass

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"⚠️  Telegram-Poller Fehler: {e}")
            time.sleep(60)


HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"
PORT = 9126
if 'PORT' in os.environ:
    PORT = int(os.environ['PORT'])
if len(sys.argv) > 1:
    PORT = int(sys.argv[1])


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

        if path == "/api/config":
            automatik = get_config("automatik_an", "true")
            self._send_json({"automatik_an": automatik == "true"})
            return

        if path == "/api/stats":
            db = get_db()
            total = db.execute("SELECT COUNT(*) as cnt FROM entries").fetchone()["cnt"]
            unread = db.execute("SELECT COUNT(*) as cnt FROM entries WHERE status='new' OR status='pre_analyzed'").fetchone()["cnt"]
            analyzed = db.execute("SELECT COUNT(*) as cnt FROM entries WHERE status='analyzed'").fetchone()["cnt"]
            filed = db.execute("SELECT COUNT(*) as cnt FROM entries WHERE status='filed'").fetchone()["cnt"]
            failed = db.execute("SELECT COUNT(*) as cnt FROM entries WHERE status='failed'").fetchone()["cnt"]
            db.close()
            self._send_json({"total": total, "unread": unread, "analyzed": analyzed, "filed": filed, "failed": failed})
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
                    if entry.get("stage_progress"):
                        try:
                            entry["stage_progress"] = json.loads(entry["stage_progress"])
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
                if e.get("stage_progress"):
                    try:
                        e["stage_progress"] = json.loads(e["stage_progress"])
                    except (json.JSONDecodeError, TypeError):
                        pass
            db.close()
            self._send_json(entries)
            return

        # --- Playlist API (GET) ---
        # /api/playlists/:id/videos (must come before /api/playlists/:id)
        if path.startswith("/api/playlists/") and path.endswith("/videos"):
            parts = path.split("/")
            if len(parts) == 5:
                playlist_id = parts[3]
                status_filter = params.get("status", [None])[0]
                db = get_db()
                query = "SELECT * FROM playlist_videos WHERE playlist_id = ?"
                args = [playlist_id]
                if status_filter:
                    query += " AND status = ?"
                    args.append(status_filter)
                query += " ORDER BY published_at DESC, created_at DESC"
                rows = db.execute(query, args).fetchall()
                db.close()
                self._send_json([dict(r) for r in rows])
                return

        # /api/playlists/:id
        if path.startswith("/api/playlists/"):
            parts = path.split("/")
            if len(parts) == 4:
                playlist_id = parts[3]
                db = get_db()
                row = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not row:
                    db.close()
                    self._send_json({"error": "Not Found"}, 404)
                    return
                playlist = dict(row)
                # Add video counts
                counts = db.execute("""
                    SELECT status, COUNT(*) as cnt FROM playlist_videos
                    WHERE playlist_id = ? GROUP BY status
                """, (playlist_id,)).fetchall()
                playlist["video_counts"] = {r["status"]: r["cnt"] for r in counts}
                db.close()
                self._send_json(playlist)
                return

        if path == "/api/playlists":
            db = get_db()
            rows = db.execute("SELECT * FROM playlists ORDER BY created_at DESC").fetchall()
            playlists = [dict(r) for r in rows]
            # Add video counts per playlist
            for p in playlists:
                counts = db.execute("""
                    SELECT status, COUNT(*) as cnt FROM playlist_videos
                    WHERE playlist_id = ? GROUP BY status
                """, (p["id"],)).fetchall()
                p["video_counts"] = {r["status"]: r["cnt"] for r in counts}
            db.close()
            self._send_json(playlists)
            return

        # --- Telegram Inbox API (GET) ---
        if path.startswith("/api/telegram/inbox/stats"):
            db = get_db()
            new_count = db.execute("SELECT COUNT(*) as cnt FROM telegram_inbox WHERE status='new'").fetchone()["cnt"]
            added_count = db.execute("SELECT COUNT(*) as cnt FROM telegram_inbox WHERE status='added'").fetchone()["cnt"]
            dismissed_count = db.execute("SELECT COUNT(*) as cnt FROM telegram_inbox WHERE status='dismissed'").fetchone()["cnt"]
            total = db.execute("SELECT COUNT(*) as cnt FROM telegram_inbox").fetchone()["cnt"]
            db.close()
            self._send_json({"total": total, "new": new_count, "added": added_count, "dismissed": dismissed_count})
            return

        if path == "/api/telegram/inbox":
            db = get_db()
            status_filter = params.get("status", [None])[0]
            query = "SELECT * FROM telegram_inbox"
            args = []
            if status_filter:
                query += " WHERE status = ?"
                args.append(status_filter)
            query += " ORDER BY created_at DESC"
            rows = db.execute(query, args).fetchall()
            db.close()
            self._send_json([dict(r) for r in rows])
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

            # Auto-detect source_type if not explicitly set
            source_type = body.get("source_type", "").strip()
            if not source_type or source_type == "web":
                source_type = _detect_source_type(url)

            title = body.get("title", "").strip()

            db = get_db()
            try:
                db.execute(
                    "INSERT OR IGNORE INTO entries (source_type, url, title, status, stage_progress) VALUES (?, ?, ?, 'new', ?)",
                    (source_type, url, title, init_stage_progress(source_type))
                )
                db.commit()
                entry_row = db.execute("SELECT id, source_type FROM entries WHERE url = ?", (url,)).fetchone()
                db.close()
                if entry_row:
                    entry_id = entry_row["id"]

                    # Chained auto-processing: preanalyze → kanban → pipeline
                    # Replaces old parallel _preanalyze_entry + _auto_kanban threads
                    automatik = get_config("automatik_an", "true")
                    if automatik == "true":
                        threading.Thread(
                            target=_auto_process_entry,
                            args=(entry_id,),
                            daemon=True
                        ).start()
                        print(f"🤖 Automatik: Auto-Process #{entry_id} gestartet")
                    else:
                        # Automatik AUS: nur Pre-Analyse (Metadaten)
                        threading.Thread(
                            target=_preanalyze_entry,
                            args=(entry_id,),
                            daemon=True
                        ).start()

                    self._send_json({"id": entry_id, "message": "Eintrag erstellt"}, 201)
                else:
                    self._send_json({"message": "Eintrag existiert bereits"}, 200)
            except Exception as e:
                db.close()
                self._send_json({"error": str(e)}, 500)
            return

        # POST /api/entries/:id/kanban — Eintrag ins Kanban schicken + Pipeline starten
        if path.startswith("/api/entries/") and path.endswith("/kanban"):
            parts = path.split("/")
            if len(parts) == 5:
                entry_id = parts[3]
                db = get_db()
                row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                db.close()
                if not row:
                    self._send_json({"error": "Entry not found"}, 404)
                    return
                entry = dict(row)
                if entry["status"] not in ("new", "pre_analyzed", "failed"):
                    self._send_json({"error": f"Kanban nur für new/pre_analyzed/failed möglich, aktuell: {entry['status']}"}, 400)
                    return
                _send_to_kanban(entry_id)

                # Pipeline in Background starten (YouTube → transcribe+analyze, Web → analyze)
                source_type = entry.get("source_type", "web")
                if source_type == "youtube":
                    threading.Thread(target=_run_youtube_pipeline, args=(int(entry_id),), daemon=True).start()
                else:
                    threading.Thread(target=_run_web_pipeline, args=(int(entry_id),), daemon=True).start()

                self._send_json({"message": f"Entry #{entry_id} ins Kanban geschickt, Pipeline gestartet", "status": "kanban"})
                return

        # POST /api/entries/:id/analyze — LLM-Analyse starten (nur pre_analyzed)
        if path.startswith("/api/entries/") and path.endswith("/analyze"):
            parts = path.split("/")
            if len(parts) == 5:
                entry_id = parts[3]
                db = get_db()
                row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                db.close()
                if not row:
                    self._send_json({"error": "Entry not found"}, 404)
                    return
                entry = dict(row)
                if entry["status"] not in ("pre_analyzed", "new"):
                    self._send_json({"error": f"Analyse nur für pre_analyzed/new möglich, aktuell: {entry['status']}"}, 400)
                    return

                def _run_analysis(entry_id: int, entry: dict):
                    _analyze_and_suggest(entry_id, entry)

                threading.Thread(target=_run_analysis, args=(entry_id, entry), daemon=True).start()
                self._send_json({"message": "Analyse gestartet"})
                return

        # POST /api/entries/:id/trilium — In Trilium ablegen
        if path.startswith("/api/entries/") and path.endswith("/trilium"):
            parts = path.split("/")
            if len(parts) == 5:
                entry_id = parts[3]
                db = get_db()
                row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                db.close()
                if not row:
                    self._send_json({"error": "Entry not found"}, 404)
                    return
                entry = dict(row)
                if entry["status"] != "analyzed":
                    self._send_json({"error": f"Ablage nur für analyzed möglich, aktuell: {entry['status']}"}, 400)
                    return

                target_note_id = entry.get("trilium_target_note_id") or RESOURCES_NOTE_ID
                analysis = entry.get("analysis")
                if analysis:
                    try:
                        analysis = json.loads(analysis)
                    except (json.JSONDecodeError, TypeError):
                        analysis = {}

                from trilium import create_trilium_note
                result = create_trilium_note(entry, analysis, target_note_id)

                if result["status"] == "ok":
                    db = get_db()
                    db.execute(
                        "UPDATE entries SET status = 'filed', trilium_note_id = ?, processing_step = NULL WHERE id = ?",
                        (result.get("note_id", ""), entry_id)
                    )
                    db.commit()
                    db.close()
                    self._send_json({
                        "status": "filed",
                        "note_id": result["note_id"],
                        "path": result["path"],
                        "message": f"📂 Abgelegt unter: {result['path']}"
                    })
                else:
                    db = get_db()
                    db.execute("UPDATE entries SET status = 'failed' WHERE id = ?", (entry_id,))
                    db.commit()
                    db.close()
                    self._send_json({
                        "status": "failed",
                        "error": result.get("error", "Unbekannter Fehler"),
                        "message": f"❌ Fehler bei Trilium-Ablage: {result.get('error', 'Unbekannt')}"
                    }, 500)
                return

        # --- Playlist API (POST) ---

        # POST /api/playlists — Neue Playlist anlegen
        if path == "/api/playlists":
            body = self._read_body()
            name = body.get("name", "").strip()
            playlist_url = body.get("playlist_url", "").strip()
            interval = body.get("check_interval_hours", 24)
            if not name or not playlist_url:
                self._send_json({"error": "name and playlist_url are required"}, 400)
                return
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO playlists (name, playlist_url, check_interval_hours) VALUES (?, ?, ?)",
                    (name, playlist_url, interval)
                )
                db.commit()
                row = db.execute("SELECT * FROM playlists WHERE playlist_url = ?", (playlist_url,)).fetchone()
                db.close()
                self._send_json(dict(row), 201)
            except Exception as e:
                db.close()
                self._send_json({"error": str(e)}, 500)
            return

        # POST /api/playlists/:id/check — Playlist sofort prüfen
        if path.startswith("/api/playlists/") and path.endswith("/check"):
            parts = path.split("/")
            if len(parts) == 5:
                playlist_id = parts[3]
                import subprocess
                db = get_db()
                pl = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not pl:
                    db.close()
                    self._send_json({"error": "Not Found"}, 404)
                    return
                playlist = dict(pl)
                url = playlist["playlist_url"]
                print(f"📋 Checke Playlist #{playlist_id}: {playlist['name']} ({url[:60]}...)")
                try:
                    result = subprocess.run(
                        ["yt-dlp", "--flat-playlist", "--dump-json", url],
                        capture_output=True, text=True, timeout=60
                    )
                    if result.returncode != 0:
                        db.close()
                        self._send_json({"error": f"yt-dlp Fehler: {result.stderr[:300]}"}, 500)
                        return

                    new_count = 0
                    for line in result.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        try:
                            video = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        video_id = video.get("id", "")
                        vtitle = video.get("title", "")
                        channel = video.get("channel", "") or video.get("uploader", "")
                        thumb = video.get("thumbnail", "")
                        published = video.get("upload_date", "")
                        if published and len(published) == 8:
                            published = f"{published[:4]}-{published[4:6]}-{published[6:8]}"

                        try:
                            db.execute(
                                """INSERT OR IGNORE INTO playlist_videos
                                   (playlist_id, video_id, title, channel, thumbnail_url, published_at)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (playlist_id, video_id, vtitle, channel, thumb, published)
                            )
                            if db.total_changes > 0:
                                new_count += 1
                        except Exception:
                            pass

                    # Update last_checked_at
                    from datetime import datetime
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    db.execute("UPDATE playlists SET last_checked_at = ? WHERE id = ?",
                               (now, playlist_id))
                    db.commit()
                    db.close()

                    print(f"✅ Playlist #{playlist_id} gecheckt: {new_count} neue Videos")
                    self._send_json({
                        "message": f"Playlist gecheckt: {new_count} neue Videos",
                        "new_count": new_count,
                        "last_checked_at": now
                    })
                except subprocess.TimeoutExpired:
                    db.close()
                    self._send_json({"error": "yt-dlp timeout (60s)"}, 500)
                except Exception as e:
                    db.close()
                    self._send_json({"error": str(e)}, 500)
                return

        # POST /api/playlists/:id/videos/:vid/dismiss — Video ignorieren
        if path.startswith("/api/playlists/") and "/videos/" in path and path.endswith("/dismiss"):
            parts = path.split("/")
            if len(parts) == 7 and parts[4] == "videos" and parts[6] == "dismiss":
                playlist_id, video_id = parts[3], parts[5]
                db = get_db()
                db.execute(
                    "UPDATE playlist_videos SET status = 'dismissed' WHERE playlist_id = ? AND video_id = ?",
                    (playlist_id, video_id)
                )
                db.commit()
                db.close()
                self._send_json({"message": "Video dismissed", "status": "dismissed"})
                return

        # POST /api/playlists/:id/videos/:vid/add — Video als Eintrag übernehmen
        if path.startswith("/api/playlists/") and "/videos/" in path and path.endswith("/add"):
            parts = path.split("/")
            if len(parts) == 7 and parts[4] == "videos" and parts[6] == "add":
                playlist_id, video_id = parts[3], parts[5]
                db = get_db()
                vid_row = db.execute(
                    "SELECT * FROM playlist_videos WHERE playlist_id = ? AND video_id = ?",
                    (playlist_id, video_id)
                ).fetchone()
                if not vid_row:
                    db.close()
                    self._send_json({"error": "Video not found"}, 404)
                    return
                vid = dict(vid_row)
                yt_url = f"https://youtube.com/watch?v={video_id}"
                existing = db.execute("SELECT id FROM entries WHERE url = ?", (yt_url,)).fetchone()
                if existing:
                    entry_id = existing["id"]
                    db.execute(
                        "UPDATE playlist_videos SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, vid["id"])
                    )
                    db.commit()
                    db.close()
                    self._send_json({"id": entry_id, "message": "Eintrag existiert bereits, verlinkt"})
                    return

                title = vid.get("title", "")
                channel = vid.get("channel", "")
                thumb = vid.get("thumbnail_url", "")
                published = vid.get("published_at", "")
                db.execute(
                    """INSERT INTO entries (source_type, url, title, author, thumbnail_url,
                       published_at, status) VALUES ('youtube', ?, ?, ?, ?, ?, 'new')""",
                    (yt_url, title, channel, thumb, published)
                )
                db.commit()
                entry_row = db.execute("SELECT id FROM entries WHERE url = ?", (yt_url,)).fetchone()
                entry_id = entry_row["id"] if entry_row else None

                if entry_id:
                    db.execute(
                        "UPDATE playlist_videos SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, vid["id"])
                    )
                    db.commit()
                    threading.Thread(
                        target=_preanalyze_entry,
                        args=(entry_id,),
                        daemon=True
                    ).start()

                db.close()
                self._send_json({
                    "id": entry_id,
                    "status": "added",
                    "message": f"Video als Eintrag #{entry_id} übernommen, Pre-Analyse gestartet"
                }, 201)
                return

        # POST /api/playlists/bulk/add — Mehrere Videos auf einmal übernehmen
        if path == "/api/playlists/bulk/add":
            body = self._read_body()
            items = body.get("items", [])
            if not items:
                self._send_json({"error": "items array is required"}, 400)
                return
            results = []
            db = get_db()
            for item in items:
                pl_id = item.get("playlist_id")
                v_id = item.get("video_id")
                if not pl_id or not v_id:
                    results.append({"playlist_id": pl_id, "video_id": v_id, "status": "error", "error": "Missing fields"})
                    continue
                vid_row = db.execute(
                    "SELECT * FROM playlist_videos WHERE playlist_id = ? AND video_id = ?",
                    (pl_id, v_id)
                ).fetchone()
                if not vid_row:
                    results.append({"playlist_id": pl_id, "video_id": v_id, "status": "error", "error": "Video not found"})
                    continue
                vid = dict(vid_row)
                yt_url = f"https://youtube.com/watch?v={v_id}"
                existing = db.execute("SELECT id FROM entries WHERE url = ?", (yt_url,)).fetchone()
                if existing:
                    db.execute(
                        "UPDATE playlist_videos SET status = 'added', entry_id = ? WHERE id = ?",
                        (existing["id"], vid["id"])
                    )
                    results.append({"playlist_id": pl_id, "video_id": v_id, "status": "exists", "entry_id": existing["id"]})
                    continue
                db.execute(
                    """INSERT INTO entries (source_type, url, title, author, thumbnail_url,
                       published_at, status) VALUES ('youtube', ?, ?, ?, ?, ?, 'new')""",
                    (yt_url, vid.get("title",""), vid.get("channel",""), vid.get("thumbnail_url",""), vid.get("published_at",""))
                )
                db.commit()
                erow = db.execute("SELECT id FROM entries WHERE url = ?", (yt_url,)).fetchone()
                eid = erow["id"] if erow else None
                if eid:
                    db.execute(
                        "UPDATE playlist_videos SET status = 'added', entry_id = ? WHERE id = ?",
                        (eid, vid["id"])
                    )
                    threading.Thread(target=_preanalyze_entry, args=(eid,), daemon=True).start()
                    results.append({"playlist_id": pl_id, "video_id": v_id, "status": "added", "entry_id": eid})
            db.commit()
            db.close()
            self._send_json({"results": results, "total": len(results)}, 201)
            return

        # --- Telegram Inbox API (POST) ---

        # POST /api/telegram/inbox/check — Sofortigen Poll triggern
        if path == "/api/telegram/inbox/check":
            # Run poll in a background thread so request doesn't hang
            def _manual_poll():
                token = _load_telegram_token()
                if not token:
                    return
                import urllib.request, json
                try:
                    url = f"{TELEGRAM_API}{token}/getUpdates?offset=0&timeout=10"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read())
                    if data.get("ok"):
                        for update in data.get("result", []):
                            msg = update.get("message", {})
                            if not msg:
                                continue
                            text = msg.get("text", "") or msg.get("caption", "")
                            if not text:
                                continue
                            urls = _extract_urls(text)
                            if not urls:
                                continue
                            chat_id = msg.get("chat", {}).get("id", 0)
                            message_id = msg.get("message_id", 0)
                            sender = msg.get("from", {}).get("first_name", "Unbekannt")
                            for found_url in urls:
                                _save_telegram_url(found_url, sender, message_id, chat_id)
                            reply_url = f"{TELEGRAM_API}{token}/sendMessage"
                            reply_data = json.dumps({
                                "chat_id": chat_id,
                                "text": "✅ Erhalten! Wird in Trend Radar verarbeitet.",
                                "reply_to_message_id": message_id,
                            }).encode()
                            reply_req = urllib.request.Request(reply_url, data=reply_data,
                                                               headers={"Content-Type": "application/json"})
                            try:
                                urllib.request.urlopen(reply_req, timeout=10)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"⚠️  Manueller Telegram-Poll Fehler: {e}")

            threading.Thread(target=_manual_poll, daemon=True).start()
            self._send_json({"message": "Telegram-Poll gestartet"})
            return

        # POST /api/telegram/inbox/bulk/add — Mehrere IDs auf einmal übernehmen
        if path == "/api/telegram/inbox/bulk/add":
            body = self._read_body()
            ids = body.get("ids", [])
            if not ids:
                self._send_json({"error": "ids array is required"}, 400)
                return
            results = []
            db = get_db()
            for inbox_id in ids:
                row = db.execute("SELECT * FROM telegram_inbox WHERE id = ?", (inbox_id,)).fetchone()
                if not row:
                    results.append({"id": inbox_id, "status": "error", "error": "Not found"})
                    continue
                inbox_item = dict(row)
                url = inbox_item["url"]

                existing = db.execute("SELECT id FROM entries WHERE url = ?", (url,)).fetchone()
                if existing:
                    entry_id = existing["id"]
                    db.execute(
                        "UPDATE telegram_inbox SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, inbox_id)
                    )
                    results.append({"id": inbox_id, "status": "exists", "entry_id": entry_id})
                    continue

                source_type = _detect_source_type(url)
                title = inbox_item.get("title", "")
                db.execute(
                    "INSERT INTO entries (source_type, url, title, status) VALUES (?, ?, ?, 'new')",
                    (source_type, url, title)
                )
                db.commit()
                entry_row = db.execute("SELECT id FROM entries WHERE url = ?", (url,)).fetchone()
                entry_id = entry_row["id"] if entry_row else None

                if entry_id:
                    db.execute(
                        "UPDATE telegram_inbox SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, inbox_id)
                    )
                    db.commit()
                    threading.Thread(target=_preanalyze_entry, args=(entry_id,), daemon=True).start()
                    results.append({"id": inbox_id, "status": "added", "entry_id": entry_id})
                else:
                    results.append({"id": inbox_id, "status": "error", "error": "Entry creation failed"})
            db.commit()
            db.close()
            self._send_json({"results": results, "total": len(results)}, 201)
            return

        # POST /api/telegram/inbox/:id/dismiss — Eintrag ignorieren
        if path.startswith("/api/telegram/inbox/") and path.endswith("/dismiss"):
            parts = path.split("/")
            if len(parts) == 6:
                inbox_id = parts[4]
                if inbox_id == "bulk":
                    self._send_json({"error": "Not Found"}, 404)
                    return
                db = get_db()
                db.execute("UPDATE telegram_inbox SET status = 'dismissed' WHERE id = ?", (inbox_id,))
                db.commit()
                db.close()
                self._send_json({"message": "Eintrag ignoriert", "status": "dismissed"})
                return

        # POST /api/telegram/inbox/:id/add — Als Trend-Radar-Eintrag übernehmen
        if path.startswith("/api/telegram/inbox/") and path.endswith("/add"):
            parts = path.split("/")
            if len(parts) == 6:
                inbox_id = parts[4]
                db = get_db()
                row = db.execute("SELECT * FROM telegram_inbox WHERE id = ?", (inbox_id,)).fetchone()
                if not row:
                    db.close()
                    self._send_json({"error": "Not Found"}, 404)
                    return
                inbox_item = dict(row)
                url = inbox_item["url"]

                # Check if URL already exists in entries
                existing = db.execute("SELECT id FROM entries WHERE url = ?", (url,)).fetchone()
                if existing:
                    entry_id = existing["id"]
                    db.execute(
                        "UPDATE telegram_inbox SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, inbox_id)
                    )
                    db.commit()
                    db.close()
                    self._send_json({"id": entry_id, "message": "Eintrag existiert bereits, verlinkt"})
                    return

                # Create new entry
                source_type = _detect_source_type(url)
                title = inbox_item.get("title", "")
                db.execute(
                    "INSERT INTO entries (source_type, url, title, status) VALUES (?, ?, ?, 'new')",
                    (source_type, url, title)
                )
                db.commit()
                entry_row = db.execute("SELECT id FROM entries WHERE url = ?", (url,)).fetchone()
                entry_id = entry_row["id"] if entry_row else None

                if entry_id:
                    db.execute(
                        "UPDATE telegram_inbox SET status = 'added', entry_id = ? WHERE id = ?",
                        (entry_id, inbox_id)
                    )
                    db.commit()
                    # Start pre-analysis
                    threading.Thread(target=_preanalyze_entry, args=(entry_id,), daemon=True).start()

                db.close()

                self._send_json({
                    "id": entry_id,
                    "status": "added",
                    "message": f"URL als Eintrag #{entry_id} übernommen, Pre-Analyse gestartet"
                }, 201)
                return

        self._send_json({"error": "Not Found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # PUT /api/playlists/:id — Playlist bearbeiten
        if path.startswith("/api/playlists/"):
            parts = path.split("/")
            if len(parts) == 4:
                playlist_id = parts[3]
                body = self._read_body()
                db = get_db()
                row = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not row:
                    db.close()
                    self._send_json({"error": "Not Found"}, 404)
                    return
                name = body.get("name", "").strip()
                playlist_url = body.get("playlist_url", "").strip()
                interval = body.get("check_interval_hours")
                if name:
                    db.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
                if playlist_url:
                    db.execute("UPDATE playlists SET playlist_url = ? WHERE id = ?", (playlist_url, playlist_id))
                if interval is not None:
                    db.execute("UPDATE playlists SET check_interval_hours = ? WHERE id = ?", (interval, playlist_id))
                db.commit()
                updated = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                db.close()
                self._send_json(dict(updated))
                return

        self._send_json({"error": "Not Found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # PATCH /api/config — Settings ändern
        if path == "/api/config":
            body = self._read_body()
            if "automatik_an" in body:
                val = "true" if body["automatik_an"] else "false"
                set_config("automatik_an", val)
                self._send_json({"automatik_an": body["automatik_an"]})
                return
            self._send_json({"error": "No valid fields"}, 400)
            return

        # PATCH /api/entries/:id — Hermes Agent schreibt Ergebnisse zurück
        if path.startswith("/api/entries/"):
            parts = path.split("/")
            if len(parts) == 4:
                entry_id = parts[3]
                body = self._read_body()
                db = get_db()
                row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                if not row:
                    db.close()
                    self._send_json({"error": "Not Found"}, 404)
                    return

                updates = []
                args = []
                for field in ("status", "content", "analysis", "trilium_suggested_path",
                              "trilium_note_id", "trilium_target_note_id", "stage_progress"):
                    if field in body:
                        val = body[field]
                        if isinstance(val, (dict, list)):
                            val = json.dumps(val)
                        updates.append(f"{field} = ?")
                        args.append(val)
                if updates:
                    updates.append("processing_step = NULL")
                    args.append(entry_id)
                    db.execute(
                        f"UPDATE entries SET {', '.join(updates)} WHERE id = ?",
                        tuple(args)
                    )
                    db.commit()
                db.close()

                # Return updated entry
                db2 = get_db()
                updated = db2.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                db2.close()
                entry = dict(updated)
                if entry.get("analysis"):
                    try:
                        entry["analysis"] = json.loads(entry["analysis"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if entry.get("stage_progress"):
                    try:
                        entry["stage_progress"] = json.loads(entry["stage_progress"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                self._send_json(entry)
                return

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

        # DELETE /api/playlists/:id — Playlist entfernen
        if path.startswith("/api/playlists/"):
            parts = path.split("/")
            if len(parts) == 4:
                playlist_id = parts[3]
                db = get_db()
                # Videos werden per CASCADE gelöscht
                db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
                db.commit()
                deleted = db.total_changes > 0
                db.close()
                if deleted:
                    self._send_json({"message": "Playlist gelöscht"})
                else:
                    self._send_json({"error": "Not Found"}, 404)
                return

        self._send_json({"error": "Not Found"}, 404)

    def log_message(self, format, *args):
        log_str = f"[{time.strftime('%H:%M:%S')}] " + " ".join(str(a) for a in args)
        print(log_str)


def main():
    # Ensure DB exists
    if not DB_PATH.exists():
        print("📦 Initialisiere Datenbank...")
        init_db()
        seed_dummy_data()
    else:
        migrate_db()  # non-destructive upgrades

    # Start Telegram poller in background thread
    polling_thread = threading.Thread(target=_telegram_poller, daemon=True)
    polling_thread.start()

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