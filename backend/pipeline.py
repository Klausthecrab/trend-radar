#!/usr/bin/env python3
"""Pipeline: YouTube-Download + Venice STT Transkription + Analyse."""

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
TEMP_DIR = ROOT / "data" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

VENICE_API = "https://api.venice.ai/api/v1/audio/transcriptions"
VENICE_KEY = os.environ.get("AUXILIARY_VISION_API_KEY", "")

if not VENICE_KEY:
    print("⚠️  AUXILIARY_VISION_API_KEY nicht gesetzt — Venice STT nicht verfügbar")


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


def analyze_entry(title: str, content: str, source_type: str) -> dict | None:
    """Analyze an entry: relevance + takeaways + action items.
    Uses Hermes to generate analysis. This is a placeholder for now
    — real LLM-based analysis comes in Issue #5."""
    print(f"🔍 Analysiere Eintrag: {title[:50]}...")

    # Placeholder: simple heuristic based on keywords
    relevance = 1
    takeaways = []
    action_items = []

    text = (title + " " + content).lower()

    # Keyword-based relevance scoring
    high_value = ["docker", "self-hosted", "selfhosted", "homelab", "opensource",
                  "alternative", "linux", "server", "kubernetes", "k8s"]
    medium_value = ["python", "api", "monitoring", "backup", "automation",
                    "voice", "assistant", "ai", "llm", "ci/cd"]

    for kw in high_value:
        if kw in text:
            relevance += 1
    for kw in medium_value:
        if kw in text:
            relevance += 0.5

    relevance = min(int(relevance), 5)

    # Simple takeaways
    if "docker" in text:
        takeaways.append("Läuft in Docker → einfach auf Butler testbar")
    if "self-hosted" in text or "selfhosted" in text:
        takeaways.append("Self-Hosted → volle Kontrolle, kein Vendor-Lock-in")
    if "alternative" in text:
        takeaways.append("Mögliche Alternative zu bestehenden Tools prüfen")
    if "voice" in text or "assistant" in text:
        takeaways.append("Smart-Home-Integration für Butler denkbar")
    if "ai" in text or "llm" in text:
        takeaways.append("KI/LLM-Komponente — Hermes-Integration prüfen")

    if not takeaways:
        takeaways.append("Allgemeiner Tech-Trend — Relevanz für Butler prüfen")

    action_items.append("Auf Butler testen")
    if source_type == "youtube":
        action_items.append("Video anschauen für Details")

    return {
        "relevance": relevance,
        "takeaways": takeaways,
        "action_items": action_items,
    }


def process_youtube(url: str, title: str = "") -> dict:
    """Full pipeline: download YouTube audio → transcribe → analyze."""
    result = {"status": "pending", "url": url, "title": title,
              "transcript": None, "analysis": None}

    # Step 1: Download
    print(f"\n{'='*50}")
    print(f"🎬 Verarbeite YouTube: {url}")
    audio_path = download_youtube_audio(url)
    if not audio_path:
        result["status"] = "error"
        result["error"] = "Download fehlgeschlagen"
        return result

    # Step 2: Transcribe
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

    # Step 3: Analyze (heuristic for now)
    analysis = analyze_entry(title or url, transcript, "youtube")
    result["analysis"] = analysis
    result["status"] = "analyzed"

    print(f"✅ YouTube-Pipeline abgeschlossen (Relevanz: {analysis['relevance']}/5)")
    print(f"{'='*50}")
    return result


def process_url(url: str, source_type: str, title: str = "") -> dict:
    """Process any URL: YouTube gets full pipeline, others get basic analysis."""
    if source_type == "youtube" or "youtube.com" in url or "youtu.be" in url:
        return process_youtube(url, title)

    # For Reddit/Web/RSS — basic analysis only (no content extraction yet)
    return {
        "status": "pending",
        "url": url,
        "title": title,
        "message": "Nicht-YouTube-URLs werden in Issue #5 mit Content-Extraktion versorgt",
    }


if __name__ == "__main__":
    # Test
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    result = process_url(test_url, "youtube")
    print(json.dumps(result, indent=2, ensure_ascii=False))