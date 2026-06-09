#!/usr/bin/env python3
"""LLM-Analyse-Pipeline: Venice Chat API + Obsidian-Notiz-Generierung."""

import json
import os
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent

VENICE_API = "https://api.venice.ai/api/v1/chat/completions"
VENICE_KEY = os.environ.get("AUXILIARY_VISION_API_KEY", "")
OBSIDIAN_VAULT = Path.home() / "obsidian-vaults" / "osb"
RES_DIR = OBSIDIAN_VAULT / "07 - _ressources" / "trend-radar"

MODEL = "deepseek-v4-flash"

SOURCE_PREFIXES = {
    "youtube": "yt",
    "reddit": "rd",
    "rss": "rss",
    "web": "web",
}


def analyze_with_llm(title: str, content: str, source_type: str) -> dict:
    """Send title + content to Venice Chat API for structured analysis.

    Returns: {relevance, summary, topics: [{name, what_is, homelab_value, hermi_value}]}
    """
    prompt = f"""Du analysierst Tech-Inhalte für ein Homelab-Setup (Linux-Server, Docker, Self-Hosting, KI-Tools).

**Quelle:** {source_type}
**Titel:** {title}
**Inhalt:**
{content[:4000]}

Analysiere den Inhalt und antworte NUR mit einem JSON-Objekt, keinem anderen Text:

{{
  "relevance": <1-5>,
  "summary": "<1-3 Sätze Zusammenfassung auf Deutsch>",
  "topics": [
    {{
      "name": "<Name der Technologie / des Themas>",
      "what_is": "<Erklärung: Was ist das? Wofür wird es genutzt?>",
      "homelab_value": "<Mehrwert für das Homelab auf Butler — warum relevant, worauf achten>",
      "hermi_value": "<Mehrwert für Hermi/KI-Systeme — was können wir damit machen?>"
    }}
  ]
}}

Regeln:
- relevance: 1=nicht relevant, 2=Randnotiz, 3=interessant, 4=sollte man anschauen, 5=sofort testen
- topics: max 3 Themen aus dem Inhalt
- Jedes Thema hat eine Erklärung WAS es ist, WOFÜR es im Homelab gut ist und WAS Hermi/KI damit anfangen kann
- Keine ToDo-Listen, keine Checkboxen, keine Handlungsaufforderungen
- Nur Erklärung und Bewertung"""

    if not VENICE_KEY:
        print("⚠️  Kein Venice API-Key — LLM-Analyse deaktiviert")
        return None

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Du analysierst Tech-Inhalte und antwortest NUR mit JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    try:
        import httpx
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                VENICE_API,
                headers={
                    "Authorization": f"Bearer {VENICE_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code != 200:
            print(f"❌ LLM-API Fehler {resp.status_code}: {resp.text[:300]}")
            return None

        result = resp.json()
        raw = result["choices"][0]["message"]["content"]

        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        analysis = json.loads(raw)
        print(f"✅ LLM-Analyse: Relevanz {analysis.get('relevance', '?')}/5, {len(analysis.get('topics', []))} Themen")
        return analysis

    except json.JSONDecodeError as e:
        print(f"❌ LLM-Antwort kein gültiges JSON: {e}")
        print(f"   Roh-Antwort: {raw[:300]}")
        return None
    except Exception as e:
        print(f"❌ LLM-API Fehler: {e}")
        return None


def generate_obsidian_note(entry: dict, analysis: dict) -> str | None:
    """Generate an Obsidian note with neutral explanations (no checkboxes).

    Filename format: yt: Titel / rd: Titel / rss: Titel
    Content: topics with what_is, homelab_value, hermi_value.

    Returns the relative path within the vault.
    """
    title = entry.get("title", "Unbekannter Eintrag")
    url = entry.get("url", "")
    source_type = entry.get("source_type", "web")
    today = datetime.now().strftime("%Y-%m-%d")

    # Build prefixed title
    prefix = SOURCE_PREFIXES.get(source_type, "web")
    display_title = f"{prefix}: {title}"
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in display_title)[:70]

    # Clean unsafe chars from filename
    safe_title = safe_title.replace(":", "_").replace("/", "_")
    filename = f"{today} - {safe_title}.md"
    filepath = RES_DIR / filename

    relevance = analysis.get("relevance", 1)
    relevance_stars = "⭐" * relevance
    summary = analysis.get("summary", "")
    topics = analysis.get("topics", [])

    # Build content
    content = f"""---
title: {display_title}
source: {source_type}
url: {url}
relevance: {relevance}/5
created: {today}
tags: [trend-radar, {source_type}]
---

# {display_title}

**Quelle:** [{url}]({url})
**Relevanz:** {relevance_stars} ({relevance}/5)
**Datum:** {today}

## Zusammenfassung

{summary}

## Analyse

"""

    for t in topics:
        content += f"""### {t.get('name', 'Thema')}

**Was ist das?** {t.get('what_is', '—')}

**Mehrwert für Homelab:** {t.get('homelab_value', '—')}

**Mehrwert für Hermi/KI:** {t.get('hermi_value', '—')}

"""

    content += "---\n*Automatisch erstellt von Trend-Radar*\n"

    try:
        RES_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        rel_path = str(filepath.relative_to(OBSIDIAN_VAULT))
        print(f"✅ Obsidian-Note: {rel_path}")
        return rel_path
    except Exception as e:
        print(f"❌ Obsidian-Note fehlgeschlagen: {e}")
        return None


if __name__ == "__main__":
    # Test
    test = {
        "title": "Immich v2.0 veröffentlicht – Self-Hosted Google Photos Alternative",
        "content": "Immich 2.0 ist da! Neue KI-gestützte Gesichtserkennung, verbesserte Suche und ein komplett überarbeitetes Mobile-UI. Läuft auf Docker, empfiehlt 4GB RAM + GPU für ML-Features.",
        "source_type": "reddit",
        "id": 42,
        "url": "https://reddit.com/r/selfhosted/test"
    }
    a = analyze_with_llm(test["title"], test["content"], test["source_type"])
    if a:
        print(json.dumps(a, indent=2, ensure_ascii=False))
        note = generate_obsidian_note(test, a)
        print(f"Note: {note}")