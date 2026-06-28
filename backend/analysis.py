#!/usr/bin/env python3
"""LLM-Analyse-Pipeline: Venice Chat API + Trilium-Pfad-Vorschlag."""

import json
import os
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent

VENICE_API = "https://api.venice.ai/api/v1/chat/completions"
VENICE_KEY = os.environ.get("AUXILIARY_VISION_API_KEY", "")

MODEL = "deepseek-v4-flash"


def analyze_with_llm(title: str, content: str, source_type: str, language: str = "de") -> dict:
    """Send title + content to Venice Chat API for structured analysis.

    Returns: {relevance, summary, topics: [{name, what_is, homelab_value, hermi_value}]}
    """
    is_german = language == "de"
    prompt_lang = "DE" if is_german else "EN"
    analysis_instruction = (
        "Analysiere den Inhalt und antworte NUR mit einem JSON-Objekt, keinem anderen Text:"
        if is_german else
        "Analyze the content and respond ONLY with a JSON object, no other text:"
    )
    summary_field = (
        "\"summary\": \"<1-3 Sätze Zusammenfassung auf Deutsch>\""
        if is_german else
        "\"summary\": \"<1-3 sentence summary in English>\""
    )
    topic_field_names = (
        "\"what_is\": \"<Erklärung: Was ist das? Wofür wird es genutzt?>\","
        "\"homelab_value\": \"<Mehrwert für das Homelab auf Butler — warum relevant, worauf achten>\","
        "\"hermi_value\": \"<Mehrwert für Hermi/KI-Systeme — was können wir damit machen?>\""
        if is_german else
        "\"what_is\": \"<Explanation: What is it? What is it used for?>\","
        "\"homelab_value\": \"<Value for the homelab on Butler — why relevant, things to watch out for>\","
        "\"hermi_value\": \"<Value for Hermi/AI systems — what can we do with it?>\""
    )
    relevance_rules = (
        "- relevance: 1=nicht relevant, 2=Randnotiz, 3=interessant, 4=sollte man anschauen, 5=sofort testen"
        if is_german else
        "- relevance: 1=not relevant, 2=side note, 3=interesting, 4=worth checking out, 5=test immediately"
    )
    other_rules = (
        "- topics: max 3 Themen aus dem Inhalt\n"
        "- Jedes Thema hat eine Erklärung WAS es ist, WOFÜR es im Homelab gut ist und WAS Hermi/KI damit anfangen kann\n"
        "- Keine ToDo-Listen, keine Checkboxen, keine Handlungsaufforderungen\n"
        "- Nur Erklärung und Bewertung"
        if is_german else
        "- topics: max 3 topics from the content\n"
        "- Each topic explains WHAT it is, WHY it's useful in the homelab, and WHAT Hermi/AI can do with it\n"
        "- No todo lists, no checkboxes, no action prompts\n"
        "- Explanations and evaluation only"
    )

    prompt = f"""Du analysierst Tech-Inhalte für ein Homelab-Setup (Linux-Server, Docker, Self-Hosting, KI-Tools).

**Quelle/Source:** {source_type}
**Sprache/Language:** {prompt_lang}
**Titel/Title:** {title}
**Inhalt/Content:**
{content[:4000]}

{analysis_instruction}

{{
  "relevance": <1-5>,
  {summary_field},
  "topics": [
    {{
      "name": "<Name der Technologie / des Themas oder Technology/Topic name>",
      {topic_field_names}
    }}
  ]
}}

Regeln/Rules:
{relevance_rules}
{other_rules}"""

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
        "max_tokens": 8192,
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


def suggest_trilium_path(title: str, analysis: dict, resources_tree: str, language: str = "de") -> dict:
    """Ask the LLM to suggest a target Trilium path (1-3 levels, existing folders only).
    
    Resources tree is passed as context so the LLM only picks from existing folders.
    Returns: {"path": "📚 Resources > ...", "noteId": "dK4MRXl5jhaC"} 
             or {"path": "📚 Resources", "noteId": "dK4MRXl5jhaC"} (level 1 only)
    """
    from trilium import RESOURCES_NOTE_ID
    
    is_german = language == "de"
    
    if is_german:
        prompt = f"""Du wählst einen existierenden Ordner-Pfad in Trilium für einen Trend-Radar-Eintrag.

**Titel:** {title}
**Relevanz:** {analysis.get('relevance', 1)}/5
**Zusammenfassung:** {analysis.get('summary', '')}
**Topics:**
{json.dumps(analysis.get('topics', []), indent=2, ensure_ascii=False)}

**Existierende Ordner-Struktur (nur diese Ordner sind verfügbar):**
{resources_tree}

**Regeln:**
- Wähle AUSSCHLIESSLICH aus den existierenden Ordnern — schlage KEINE neuen Ordner vor
- Maximal 3 Ebenen tief: Ebene 1 = immer 📚 Resources, Ebene 2-3 = existierende Child-Ordner
- Wenn kein passender Theme-Ordner existiert → bleib bei Ebene 1 (📚 Resources)
- Der Pfad muss einem der existierenden noteIds entsprechen

Antworte NUR mit einem JSON-Objekt:
{{
  "path": "📚 Resources > Software & digitales > Homelab",
  "noteId": "bci6Tmwj7iMh"
}}
Oder bei nur Ebene 1:
{{
  "path": "📚 Resources",
  "noteId": "dK4MRXl5jhaC"
}}"""
    else:
        prompt = f"""You select an existing folder path in Trilium for a Trend Radar entry.

**Title:** {title}
**Relevance:** {analysis.get('relevance', 1)}/5
**Summary:** {analysis.get('summary', '')}
**Topics:**
{json.dumps(analysis.get('topics', []), indent=2, ensure_ascii=False)}

**Available folder structure (pick from these only):**
{resources_tree}

**Rules:**
- Pick ONLY from existing folders — do NOT suggest new folders
- Max 3 levels deep: Level 1 = always 📚 Resources, Level 2-3 = existing children
- If no matching theme folder exists → stay at Level 1 (📚 Resources)
- The path must match an existing noteId

Respond ONLY with a JSON object:
{{
  "path": "📚 Resources > Software & digitales > Homelab",
  "noteId": "bci6Tmwj7iMh"
}}
Or for level 1 only:
{{
  "path": "📚 Resources",
  "noteId": "dK4MRXl5jhaC"
}}"""

    if not VENICE_KEY:
        print("⚠️  Kein Venice API-Key — Trilium-Pfad-Vorschlag deaktiviert")
        return {"path": "📚 Resources", "noteId": RESOURCES_NOTE_ID}

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Du wählst Ordner-Pfade und antwortest NUR mit JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
    }

    try:
        import httpx
        with httpx.Client(timeout=30) as client:
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
            return {"path": "📚 Resources", "noteId": RESOURCES_NOTE_ID}

        result = resp.json()
        raw = result["choices"][0]["message"]["content"]

        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        suggestion = json.loads(raw)
        path = suggestion.get("path", "📚 Resources")
        note_id = suggestion.get("noteId", RESOURCES_NOTE_ID)
        print(f"✅ Trilium-Pfad-Vorschlag: {path} ({note_id})")
        return {"path": path, "noteId": note_id}

    except json.JSONDecodeError as e:
        print(f"❌ LLM-Pfad-Antwort kein gültiges JSON: {e}")
        print(f"   Roh: {raw[:300]}")
        return {"path": "📚 Resources", "noteId": RESOURCES_NOTE_ID}
    except Exception as e:
        print(f"❌ LLM-Pfad-Fehler: {e}")
        return {"path": "📚 Resources", "noteId": RESOURCES_NOTE_ID}


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