#!/usr/bin/env python3
"""Pipeline: YouTube-Download + Venice STT Transkription + Analyse."""

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from analysis import analyze_with_llm, generate_obsidian_note

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
TEMP_DIR = ROOT / "data" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

VENICE_API = "https://api.venice.ai/api/v1/audio/transcriptions"
VENICE_KEY = os.environ.get("AUXILIARY_VISION_API_KEY", "")

# Lazy import to avoid circular dependency
_processing_step_fn = None

def _update_step(entry_id, step):
    global _processing_step_fn
    if _processing_step_fn is None:
        from database import set_processing_step as _fn
        _processing_step_fn = _fn
    if entry_id is not None:
        _processing_step_fn(entry_id, step)

if not VENICE_KEY:
    print("⚠️  AUXILIARY_VISION_API_KEY nicht gesetzt — Venice STT nicht verfügbar")


def get_youtube_metadata(url: str) -> dict:
    """Extrahiere Thumbnail-URL + Channel via yt-dlp --print (schnell, kein Download)."""
    print(f"📋 Hole YouTube-Metadaten: {url}")
    meta = {"thumbnail_url": None, "channel": None}
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "thumbnail", "--print", "channel", "--print", "channel_url",
             "--print", "duration_string", url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 1 and lines[0]:
                meta["thumbnail_url"] = lines[0]
            if len(lines) >= 2 and lines[1]:
                meta["channel"] = lines[1]
            print(f"   🖼️ Thumbnail: {meta['thumbnail_url']}")
            print(f"   📺 Channel: {meta['channel']}")
        else:
            print(f"⚠️  yt-dlp metadata Fehler: {result.stderr[:200]}")
    except Exception as e:
        print(f"⚠️  yt-dlp metadata Exception: {e}")
    # Fallback: Thumbnail-URL aus Video-ID konstruieren
    if not meta["thumbnail_url"]:
        import re
        match = re.search(r"(?:v=|youtu\.be/|/shorts/)([a-zA-Z0-9_-]{11})", url)
        if match:
            meta["thumbnail_url"] = f"https://img.youtube.com/vi/{match.group(1)}/hqdefault.jpg"
            print(f"   🖼️ Thumbnail (Fallback): {meta['thumbnail_url']}")
    return meta


def download_youtube_audio(url: str) -> str | None:
    """Download YouTube audio, return path to normalized MP3."""
    print(f"📥 Lade YouTube-Audio: {url}")
    try:
        # Download als MP3
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3",
             "-o", str(TEMP_DIR / "%(id)s.%(ext)s"),
             url],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"❌ yt-dlp fehlgeschlagen: {result.stderr[:500]}")
            return None

        # Finde die heruntergeladene Datei
        mp3_files = list(TEMP_DIR.glob("*.mp3"))
        if not mp3_files:
            print("❌ Keine MP3-Datei gefunden")
            return None

        # Nimm die neueste
        mp3_path = max(mp3_files, key=lambda p: p.stat().st_mtime)

        # Normalisieren auf 16kHz Mono
        normalized = TEMP_DIR / f"{mp3_path.stem}_16k.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path),
             "-ar", "16000", "-ac", "1",
             str(normalized)],
            capture_output=True, text=True, timeout=60
        )

        # Original löschen
        mp3_path.unlink(missing_ok=True)

        print(f"✅ Audio normalisiert: {normalized} ({normalized.stat().st_size / 1024:.0f} KB)")
        return str(normalized)

    except subprocess.TimeoutExpired:
        print("❌ yt-dlp timeout (120s)")
        return None
    except Exception as e:
        print(f"❌ Fehler bei YouTube-Download: {e}")
        return None


def transcribe_audio(audio_path: str) -> str | None:
    """Send audio to Venice STT, return transcript text."""
    print(f"🎤 Transkribiere: {audio_path}")
    try:
        import httpx
    except ImportError:
        print("⚠️  httpx nicht installiert — verwende urllib")
        return _transcribe_urllib(audio_path)

    try:
        with httpx.Client(timeout=120) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
                data = {
                    "model": "nvidia/parakeet-tdt-0.6b-v3",
                    "response_format": "json",
                }
                resp = client.post(
                    VENICE_API,
                    headers={"Authorization": f"Bearer {VENICE_KEY}"},
                    files=files,
                    data=data,
                )
            if resp.status_code != 200:
                print(f"❌ Venice STT Fehler {resp.status_code}: {resp.text[:300]}")
                return None
            result = resp.json()
            transcript = result.get("text", "").strip()
            duration = result.get("duration", "?")
            print(f"✅ Transkript erhalten ({len(transcript)} Zeichen, {duration}s)")
            return transcript
    except Exception as e:
        print(f"❌ Venice STT Fehler: {e}")
        return None


def _transcribe_urllib(audio_path: str) -> str | None:
    """Fallback: Venice STT via urllib (ohne httpx)."""
    import io
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase

    try:
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = b""
        # file part
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(audio_path)}"\r\n'.encode()
        body += b"Content-Type: audio/mpeg\r\n\r\n"
        body += audio_data + b"\r\n"
        # model part
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="model"\r\n\r\n'
        body += b"nvidia/parakeet-tdt-0.6b-v3\r\n"
        # response_format part
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        body += b"json\r\n"
        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            VENICE_API,
            data=body,
            headers={
                "Authorization": f"Bearer {VENICE_KEY}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            transcript = result.get("text", "").strip()
            duration = result.get("duration", "?")
            print(f"✅ Transkript erhalten ({len(transcript)} Zeichen, {duration}s)")
            return transcript
    except Exception as e:
        print(f"❌ Venice STT urllib Fehler: {e}")
        return None


def analyze_entry(title: str, content: str, source_type: str, entry_id: int = None) -> dict | None:
    """Analyze an entry using Venice LLM. Returns {relevance, summary, takeaways, action_items, obsidian_note}."""
    print(f"🔍 LLM-Analyse für: {title[:60]}...")
    analysis = analyze_with_llm(title, content, source_type)
    if not analysis:
        print("⚠️  LLM-Analyse fehlgeschlagen, verwende Platzhalter")
        return {
            "relevance": 1,
            "summary": "Keine Analyse verfügbar (LLM-Fehler)",
            "takeaways": ["Inhalt manuell prüfen"],
            "action_items": ["Auf Butler testen"],
        }
    return analysis


def process_youtube(url: str, title: str = "", entry_id: int | None = None) -> dict:
    """Full pipeline: get metadata → download YouTube audio → transcribe → analyze."""
    result = {"status": "pending", "url": url, "title": title,
              "transcript": None, "analysis": None, "thumbnail_url": None, "channel": None}

    print(f"\n{'='*50}")
    print(f"🎬 Verarbeite YouTube: {url}")
    _update_step(entry_id, "extracting")

    # Step 0: Get metadata (thumbnail, channel)
    meta = get_youtube_metadata(url)
    result["thumbnail_url"] = meta["thumbnail_url"]
    result["channel"] = meta["channel"]
    audio_path = download_youtube_audio(url)
    if not audio_path:
        result["status"] = "error"
        result["error"] = "Download fehlgeschlagen"
        return result

    # Step 2: Transcribe
    _update_step(entry_id, "transcribing")
    transcript = transcribe_audio(audio_path)
    try:
        os.unlink(audio_path)
    except OSError:
        pass

    if not transcript:
        result["status"] = "error"
        result["error"] = "Transkription fehlgeschlagen"
        return result

    result["transcript"] = transcript
    result["status"] = "transcribed"

    # Step 3: Analyze
    _update_step(entry_id, "analyzing")
    analysis = analyze_entry(title or url, transcript, "youtube", entry_id=None)
    result["analysis"] = analysis
    result["status"] = "analyzed"

    # Step 4: Generate Obsidian note
    if analysis:
        _update_step(entry_id, "note")
        note_path = generate_obsidian_note(
            {"id": "?", "title": title or url, "url": url, "source_type": "youtube"},
            analysis
        )
        if note_path:
            result["obsidian_note"] = note_path

    print(f"✅ YouTube-Pipeline abgeschlossen (Relevanz: {analysis.get('relevance', '?')}/5)")
    print(f"{'='*50}")
    return result


def process_url(url: str, source_type: str, title: str = "",
                entry_id: int | None = None,
                preloaded_content: str | None = None) -> dict:
    """Process any URL: YouTube gets full pipeline, others get LLM analysis.

    Args:
        url: The source URL.
        source_type: 'youtube', 'reddit', 'rss', or 'web'.
        title: Optional title.
        entry_id: DB entry ID for status updates.
        preloaded_content: Pre-extracted content (from scanner). If provided
            and not a YouTube URL, runs analysis directly.
    """
    if source_type == "youtube" or "youtube.com" in url or "youtu.be" in url:
        return process_youtube(url, title, entry_id)

    # Non-YouTube: use preloaded content or run basic analysis on title only
    if preloaded_content:
        from database import get_db
        from database import set_processing_step

        print(f"🔍 Analysiere Entry #{entry_id}: {title[:60]}...")

        _update_step(entry_id, "analyzing")
        analysis = analyze_entry(title, preloaded_content, source_type, entry_id=None)

        _update_step(entry_id, "note")
        note_path = None
        if analysis:
            note_path = generate_obsidian_note(
                {"id": entry_id, "title": title, "url": url, "source_type": source_type},
                analysis
            )
        _update_step(entry_id, "saving")

        # Store in DB
        if analysis or note_path:
            db = get_db()
            try:
                if analysis:
                    db.execute("UPDATE entries SET analysis = ? WHERE id = ?",
                               (json.dumps(analysis), entry_id))
                if note_path:
                    db.execute("UPDATE entries SET obsidian_note_path = ? WHERE id = ?",
                               (note_path, entry_id))
                if not db.execute("SELECT content FROM entries WHERE id = ?", (entry_id,)).fetchone()["content"]:
                    db.execute("UPDATE entries SET content = ? WHERE id = ?",
                               (preloaded_content[:5000], entry_id))
                db.commit()
            except Exception as e:
                print(f"⚠️  DB-Update fehlgeschlagen für #{entry_id}: {e}")
            finally:
                db.close()

        return {
            "status": "analyzed" if analysis else "error",
            "url": url,
            "title": title,
            "analysis": analysis,
            "obsidian_note": note_path,
        }

    # Fallback: no content, just acknowledge
    print(f"ℹ️  Kein Content für {source_type}-Entry #{entry_id}: {title[:50]}...")
    return {
        "status": "pending",
        "url": url,
        "title": title,
        "message": "Kein Inhalt zum Analysieren (Scraping nicht implementiert)",
    }


if __name__ == "__main__":
    # Test
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    result = process_url(test_url, "youtube")
    print(json.dumps(result, indent=2, ensure_ascii=False))