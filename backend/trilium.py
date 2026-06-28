#!/usr/bin/env python3
"""Trilium ETAPI-Client für Trend-Radar.

Functions:
- get_resources_structure() — Liste aller existierenden Ordner unter Resources
- suggest_trilium_path(entry, analysis, structure) — LLM schlägt Zielpfad vor
- create_trilium_note(entry, analysis, parent_note_id) — Note via ETAPI anlegen
"""

import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent

# Trilium-ETAPI (localhost-only via Docker)
TRILIUM_BASE = "http://localhost:8080"
RESOURCES_NOTE_ID = "dK4MRXl5jhaC"
TRILIUM_TOKEN_CMD = ["pass", "butler/services/trilium-etapi-token"]
TRILIUM_TOKEN_FILE = Path(__file__).parent.parent / "data" / "trilium-token.txt"

# --- Auth (cached) ---

_TOKEN_CACHE = None


def _get_token() -> str:
    """Read Trilium ETAPI token from pass (cached for the session).
    Falls back to data/trilium-token.txt if pass is unavailable."""
    global _TOKEN_CACHE
    if _TOKEN_CACHE:
        return _TOKEN_CACHE
    try:
        result = subprocess.run(TRILIUM_TOKEN_CMD, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            _TOKEN_CACHE = result.stdout.strip()
            return _TOKEN_CACHE
        print(f"⚠️  pass Fehler: {result.stderr[:200]}")
    except Exception as e:
        print(f"⚠️  pass Exception: {e}")
    # Fallback: try file
    try:
        if TRILIUM_TOKEN_FILE.exists():
            _TOKEN_CACHE = TRILIUM_TOKEN_FILE.read_text().strip()
            print("📄 Token aus Datei gelesen")
            return _TOKEN_CACHE
    except Exception as e:
        print(f"⚠️  Token-Datei Fehler: {e}")
    return ""


def _api_call(method: str, endpoint: str, data: dict | None = None) -> tuple[int, dict | None]:
    """Make an ETAPI call. Returns (status_code, response_dict)."""
    token = _get_token()
    if not token:
        return 0, {"error": "Kein Trilium-Token"}
    
    cmd = [
        "curl", "-s", "--max-time", "8", "-w", "\n%{http_code}",
        "-X", method,
        f"{TRILIUM_BASE}{endpoint}",
        "-H", f"Authorization: Bearer {token}",
    ]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 0, {"error": f"curl error: {result.stderr[:200]}"}
        
        out = result.stdout.strip()
        if not out:
            return 0, {"error": "Leere Antwort"}
        
        # Extract HTTP code (last line after \n)
        parts = out.rsplit("\n", 1)
        status_code = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 200
        body = parts[0].strip() if len(parts) == 2 else ""
        
        if body:
            try:
                return status_code, json.loads(body)
            except json.JSONDecodeError:
                return status_code, {"raw": body}
        return status_code, None
    except Exception as e:
        return 0, {"error": str(e)}


# --- Structure Discovery ---

def _fetch_note(note_id: str) -> dict | None:
    """Fetch a single note's metadata from ETAPI."""
    status, data = _api_call("GET", f"/etapi/notes/{note_id}")
    if status != 200 or not data:
        return None
    return data


def _build_tree(note_id: str, depth: int = 0, max_depth: int = 4) -> dict | None:
    """Build a nested tree dict from ETAPI. Max depth 4 (Resources → child → sub → sub-sub)."""
    if depth > max_depth:
        return None
    note = _fetch_note(note_id)
    if not note:
        return None
    
    children = []
    for child_id in note.get("childNoteIds", []):
        child = _build_tree(child_id, depth + 1, max_depth)
        if child:
            children.append(child)
    
    return {
        "noteId": note_id,
        "title": note.get("title", "?"),
        "children": children,
    }


def get_resources_structure() -> dict:
    """Fetch the full Resources tree (4 levels deep) from Trilium."""
    tree = _build_tree(RESOURCES_NOTE_ID, max_depth=4)
    return tree or {"noteId": RESOURCES_NOTE_ID, "title": "📚 Resources", "children": []}


def _tree_to_flat_paths(tree: dict, prefix: str = "") -> list[dict]:
    """Convert tree to flat list of {path, noteId, depth} entries."""
    result = []
    name = tree.get("title", "?")
    full_path = f"{prefix} > {name}" if prefix else name
    result.append({
        "path": full_path,
        "noteId": tree["noteId"],
        "depth": full_path.count(" > "),
    })
    for child in tree.get("children", []):
        result.extend(_tree_to_flat_paths(child, full_path))
    return result


def format_structure_for_llm(tree: dict) -> str:
    """Format the Resources tree as a readable string for LLM prompt."""
    lines = []
    lines.append(f"📚 Resources (noteId: {RESOURCES_NOTE_ID})")
    
    def _fmt(node, indent=0, is_last=False, prefix=""):
        if indent == 0:
            connector = "├── "
        else:
            connector = "├── "
        lines.append(f"{prefix}{connector}{node.get('title', '?')} (noteId: {node['noteId']})")
        children = node.get("children", [])
        for i, child in enumerate(children):
            child_is_last = i == len(children) - 1
            child_prefix = prefix + ("│   " if not is_last else "    ")
            _fmt(child, indent + 1, child_is_last, child_prefix)
    
    children = tree.get("children", [])
    for i, child in enumerate(children):
        _fmt(child, 0, i == len(children) - 1, "")
    
    return "\n".join(lines)


# --- Note Creation ---

def create_trilium_note(
    entry: dict,
    analysis: dict,
    parent_note_id: str,
) -> dict:
    """Create a note in Trilium via ETAPI.
    
    Note content: compact summary with title, URL, summary, topics, relevance.
    
    Returns: {"status": "ok", "note_id": "...", "path": "..."} or {"status": "error", "error": "..."}
    """
    title = entry.get("title", "Trend-Radar Eintrag")
    url = entry.get("url", "")
    
    # Build note content (HTML)
    relevance = analysis.get("relevance", 1)
    relevance_stars = "⭐" * relevance
    summary = analysis.get("summary", "Keine Zusammenfassung")
    topics = analysis.get("topics", [])
    
    content_parts = [f"<p><strong>Quelle:</strong> <a href=\"{url}\">{url}</a></p>"]
    content_parts.append(f"<p><strong>Relevanz:</strong> {relevance_stars} ({relevance}/5)</p>")
    content_parts.append(f"<hr><h2>Zusammenfassung</h2><p>{summary}</p>")
    
    if topics:
        content_parts.append("<h2>Topics</h2><ul>")
        for t in topics:
            name = t.get("name", "Thema")
            what_is = t.get("what_is", "")
            homelab = t.get("homelab_value", "")
            hermi = t.get("hermi_value", "")
            content_parts.append(f"<li><strong>{name}</strong>: {what_is}<br>")
            if homelab:
                content_parts.append(f"🏠 Homelab: {homelab}<br>")
            if hermi:
                content_parts.append(f"🤖 Hermi/KI: {hermi}")
            content_parts.append("</li>")
        content_parts.append("</ul>")
    
    content_parts.append("<hr><p><em>Automatisch erstellt von Trend-Radar</em></p>")
    content_html = "\n".join(content_parts)
    
    payload = {
        "parentNoteId": parent_note_id,
        "title": title,
        "type": "text",
        "content": content_html,
    }
    
    status, data = _api_call("POST", "/etapi/create-note", payload)
    
    if status == 201 and data and "note" in data:
        note_id = data["note"].get("noteId", "?")
        print(f"✅ Trilium-Note erstellt: {title[:50]}... (noteId: {note_id})")
        
        # Build path for response
        parent_title = _fetch_note(parent_note_id)
        parent_name = parent_title.get("title", "?") if parent_title else "?"
        
        return {
            "status": "ok",
            "note_id": note_id,
            "path": f"📚 Resources > {parent_name}",
        }
    
    error_msg = data.get("error", data.get("message", "Unbekannter Fehler")) if data else "Keine Antwort"
    print(f"❌ Trilium-Note fehlgeschlagen: {error_msg}")
    return {"status": "error", "error": str(error_msg)}


# --- Verification ---

def verify_connection() -> dict:
    """Test the ETAPI connection. Returns status dict."""
    token = _get_token()
    if not token:
        return {"status": "error", "error": "Kein Token in pass"}
    
    status, data = _api_call("GET", f"/etapi/notes/{RESOURCES_NOTE_ID}")
    if status == 200 and data:
        return {"status": "ok", "title": data.get("title", "?")}
    if status == 401:
        return {"status": "error", "error": "ETAPI: 401 Unauthorized — Token ungültig"}
    return {"status": "error", "error": f"ETAPI Status {status}: {data}"}


if __name__ == "__main__":
    # Test
    print("=== Trilium-Verbindung ===")
    conn = verify_connection()
    print(json.dumps(conn, indent=2, ensure_ascii=False))
    
    if conn["status"] == "ok":
        print("\n=== Resources-Struktur ===")
        tree = get_resources_structure()
        print(format_structure_for_llm(tree))
        
        print("\n=== Flat Paths ===")
        flat = _tree_to_flat_paths(tree)
        for p in flat[:10]:
            print(f"  depth={p['depth']} | {p['path']} ({p['noteId']})")
        print(f"  ... ({len(flat)} total)")
