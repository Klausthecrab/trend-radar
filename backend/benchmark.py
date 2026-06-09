#!/usr/bin/env python3
"""Benchmark: Zeit pro Schritt der YouTube-Pipeline."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
TEMP_DIR = ROOT / "data" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(HERE))
from analysis import analyze_with_llm

VENICE_KEY = os.environ.get("AUXILIARY_VISION_API_KEY", "")
if not VENICE_KEY:
    print("❌ AUXILIARY_VISION_API_KEY nicht gesetzt")
    sys.exit(1)

URL = "https://www.youtube.com/watch?v=Zsz0QnNyAjk"
TITLE = "What I Actually Run in My Homelab – 2026 Self-Hosted Stack"
times = {}


def step(name, func):
    print(f"\n{'='*50}")
    print(f"⏱  Schritt: {name}")
    print(f"{'='*50}")
    start = time.time()
    result = func()
    elapsed = time.time() - start
    times[name] = elapsed
    m, s = divmod(int(elapsed), 60)
    print(f"✅ {name}: {m}:{s:02d} min ({elapsed:.1f}s)")
    return result


# Step 1: yt-dlp download
def download():
    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3",
         "-o", str(TEMP_DIR / "%(id)s.%(ext)s"),
         URL],
        capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        print(f"❌ yt-dlp Fehler: {result.stderr[:500]}")
        return None
    mp3_files = list(TEMP_DIR.glob("*.mp3"))
    if not mp3_files:
        print("❌ Keine MP3-Datei gefunden")
        return None
    return max(mp3_files, key=lambda p: p.stat().st_mtime)


mp3_path = step("1️⃣ yt-dlp Download", download)
if not mp3_path:
    sys.exit(1)

print(f"   Datei: {mp3_path.name} ({mp3_path.stat().st_size / 1024:.0f} KB)")


# Step 2: ffmpeg normalization
def normalize():
    normalized = TEMP_DIR / f"{mp3_path.stem}_16k.mp3"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path),
         "-ar", "16000", "-ac", "1",
         str(normalized)],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"❌ ffmpeg Fehler: {result.stderr[:300]}")
        return None
    mp3_path.unlink(missing_ok=True)
    return normalized


norm_path = step("2️⃣ ffmpeg Normalisierung", normalize)
if not norm_path:
    sys.exit(1)
print(f"   Normalisiert: {norm_path.stat().st_size / 1024:.0f} KB")


# Step 3: Venice STT
def transcribe():
    import httpx
    with open(norm_path, "rb") as f:
        files = {"file": (norm_path.name, f, "audio/mpeg")}
        data = {
            "model": "nvidia/parakeet-tdt-0.6b-v3",
            "response_format": "json",
        }
        resp = httpx.post(
            "https://api.venice.ai/api/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {VENICE_KEY}"},
            files=files,
            data=data,
            timeout=180,
        )
    if resp.status_code != 200:
        print(f"❌ Venice STT Fehler {resp.status_code}: {resp.text[:300]}")
        return None
    result = resp.json()
    transcript = result.get("text", "").strip()
    print(f"   Transkript: {len(transcript)} Zeichen")
    return transcript


transcript = step("3️⃣ Venice STT Transkription", transcribe)
if not transcript:
    sys.exit(1)


# Step 4: LLM Analysis
def analyze():
    return analyze_with_llm(TITLE, transcript[:4000], "youtube")


analysis = step("4️⃣ LLM Analyse", analyze)
if analysis:
    print(f"   Relevanz: {analysis.get('relevance')}/5")

# Cleanup
try:
    os.unlink(norm_path)
except OSError:
    pass

# Summary
print(f"\n{'='*50}")
print(f"📊 BENCHMARK-ERGEBNISSE")
print(f"{'='*50}")
print(f"Video: {TITLE}")
print(f"Dauer: 9:27 min (567s)")
print(f"Total Pipeline: {sum(times.values()):.1f}s = {sum(times.values())/60:.1f} min")
print()
for name, elapsed in times.items():
    pct = (elapsed / sum(times.values())) * 100
    m, s = divmod(int(elapsed), 60)
    print(f"  {name}: {m}:{s:02d} min ({elapsed:.1f}s) — {pct:.0f}%")
print()

# Save to file
results = {
    "video_url": URL,
    "video_title": TITLE,
    "video_duration_sec": 567,
    "total_pipeline_sec": round(sum(times.values()), 1),
    "steps": times,
    "transcript_length": len(transcript),
}
out = ROOT / "data" / "benchmark.json"
out.write_text(json.dumps(results, indent=2))
print(f"📁 Ergebnis gespeichert: {out}")