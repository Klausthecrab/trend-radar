#!/usr/bin/env python3
"""Scanner: crawl subreddits and RSS feeds for new content."""

import json
import time
import html
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from database import get_db
from pipeline import process_url

HERE = Path(__file__).parent.resolve()

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
SCAN_LIMIT = 25


def fetch_json(url: str) -> dict | None:
    """Fetch JSON from a URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"⚠️  HTTP {e.code} für {url[:60]}...")
        return None
    except Exception as e:
        print(f"⚠️  Fehler bei {url[:60]}...: {e}")
        return None


def fetch_text(url: str) -> str | None:
    """Fetch raw text from a URL (for RSS/Atom feeds)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"⚠️  Fehler bei Feed {url[:60]}...: {e}")
        return None


def _insert_entry(db, source_id: int | None, title: str, url: str,
                  content: str, author: str, published_at: str | None = None) -> int | None:
    """Insert a new entry if it doesn't exist. Returns entry id or None if duplicate."""
    try:
        db.execute(
            """INSERT OR IGNORE INTO entries
               (source_id, source_type, url, title, content, author, published_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'unread')""",
            (source_id, _detect_source_type(url), url, title, content, author, published_at)
        )
        db.commit()
        row = db.execute("SELECT id FROM entries WHERE url = ?", (url,)).fetchone()
        if row:
            return row["id"]
        return None
    except Exception as e:
        print(f"⚠️  Insert-Fehler für {url[:60]}...: {e}")
        return None


def _detect_source_type(url: str) -> str:
    """Detect source_type from URL."""
    if "reddit.com" in url:
        return "reddit"
    return "rss"


def scan_subreddit(name: str, source_id: int | None = None) -> int:
    """Scan a subreddit for new posts. Returns count of new entries."""
    print(f"📡 Scanne r/{name}...")
    url = f"https://www.reddit.com/r/{name}/new.json?limit={SCAN_LIMIT}"
    data = fetch_json(url)
    if not data or "data" not in data or "children" not in data["data"]:
        print(f"⚠️  Keine Posts für r/{name} gefunden")
        return 0

    posts = data["data"]["children"]
    new_count = 0
    db = get_db()

    for post in posts:
        p = post.get("data", {})
        title = p.get("title", "")
        post_url = p.get("url", "")
        permalink = p.get("permalink", "")
        full_url = f"https://reddit.com{permalink}"
        content = p.get("selftext", "") or ""
        author = p.get("author", "u/unknown")
        created = datetime.utcfromtimestamp(p.get("created_utc", 0)).isoformat() if p.get("created_utc") else None

        entry_id = _insert_entry(
            db, source_id, title,
            full_url if content else post_url,
            content[:5000] if content else "",  # cap content length
            author, created
        )
        if entry_id:
            new_count += 1
            # Trigger analysis pipeline in background
            try:
                import threading
                threading.Thread(
                    target=_analyze_entry_background,
                    args=(entry_id, title, content, "reddit"),
                    daemon=True
                ).start()
            except Exception:
                pass

    db.close()
    print(f"  → {new_count} neue Einträge in r/{name}")
    return new_count


def scan_rss(feed_url: str, source_id: int | None = None) -> int:
    """Scan an RSS/Atom feed for new items. Returns count of new entries."""
    print(f"📡 Scanne RSS: {feed_url[:60]}...")
    raw = fetch_text(feed_url)
    if not raw:
        return 0

    new_count = 0
    db = get_db()

    try:
        # Try RSS (channel → item) and Atom (entry) formats
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # RSS format
        items = root.findall(".//channel/item")
        if not items:
            # Atom format
            items = root.findall(".//atom:entry", ns)
            if not items:
                items = root.findall(".//entry")

        for item in items:
            # RSS fields — use iteration to find title reliably
            title_text = "(Kein Titel)"
            for child in item:
                if child.tag == "title" and child.text:
                    title_text = child.text.strip()
                    break
            link_el = item.find("link")
            desc_el = item.find("description")
            pub_el = item.find("pubDate") or item.find("published") or item.find("atom:published", ns)
            author_el = item.find("author") or item.find("atom:author", ns) or item.find("dc:creator")

            # Atom link has href attribute
            if link_el is not None and link_el.get("href"):
                link = link_el.get("href")
            elif link_el is not None and link_el.text:
                link = link_el.text.strip()
            else:
                link = feed_url

            title_text = title_text or "(Kein Titel)"
            link = link or feed_url
            desc_text = desc_el.text.strip()[:5000] if desc_el is not None and desc_el.text else ""
            # Strip HTML from description
            desc_text = html.unescape(desc_text)

            pub_text = pub_el.text.strip() if pub_el is not None and pub_el.text else None
            author_text = author_el.text.strip() if author_el is not None and author_el.text else "Unbekannt"
            # Handle atom:author with sub-elements
            if author_el is not None and not author_el.text:
                name_el = author_el.find("name") or author_el.find("atom:name", ns)
                if name_el is not None and name_el.text:
                    author_text = name_el.text.strip()

            entry_id = _insert_entry(
                db, source_id, title_text, link,
                desc_text, author_text, pub_text
            )
            if entry_id:
                new_count += 1
                # Trigger analysis in background
                try:
                    import threading
                    threading.Thread(
                        target=_analyze_entry_background,
                        args=(entry_id, title_text, desc_text, "rss"),
                        daemon=True
                    ).start()
                except Exception:
                    pass

    except ET.ParseError as e:
        print(f"⚠️  RSS-Parse-Fehler: {e}")
    except Exception as e:
        print(f"⚠️  RSS-Fehler: {e}")
    finally:
        db.close()

    print(f"  → {new_count} neue Einträge aus Feed")
    return new_count


def _analyze_entry_background(entry_id: int, title: str, content: str, source_type: str):
    """Run analysis pipeline on a scanned entry (background thread)."""
    from database import set_processing_step

    if not content and not title:
        return

    print(f"🧵 Analysiere Scanned-Entry #{entry_id}: {title[:50]}...")
    set_processing_step(entry_id, "analyzing")

    # Pre-Analyse (Metadaten + kanonische URL) — korrigiert URL vor Analyse (Issue #17)
    try:
        from server import _preanalyze_entry
        _preanalyze_entry(entry_id)
    except Exception:
        pass

    try:
        result = process_url("", source_type, title, entry_id=entry_id, preloaded_content=content)
        # process_url stores analysis + Obsidian note in DB
        if result.get("status") == "error":
            print(f"⚠️  Analyse fehlgeschlagen für #{entry_id}: {result.get('error')}")
    except Exception as e:
        print(f"❌ Analyse-Exception für #{entry_id}: {e}")
    finally:
        set_processing_step(entry_id, None)


def scan_source(source: dict) -> int:
    """Scan a single source by its type. Returns count of new entries."""
    stype = source["type"]
    url = source["url"]
    sid = source["id"]

    print(f"\n--- Scanne: {source.get('name', url)} ({stype}) ---")

    if stype == "subreddit":
        # url is the subreddit name or full URL
        name = url.replace("https://reddit.com/r/", "").replace("http://reddit.com/r/", "").replace("/", "")
        count = scan_subreddit(name, sid)
    elif stype == "rss":
        count = scan_rss(url, sid)
    else:
        print(f"⚠️  Unbekannter Quellentyp: {stype}")
        return 0

    # Update last_scanned_at
    try:
        db = get_db()
        db.execute(
            "UPDATE sources SET last_scanned_at = datetime('now') WHERE id = ?",
            (sid,)
        )
        db.commit()
        db.close()
    except Exception:
        pass

    return count


def scan_all_sources() -> dict:
    """Scan all enabled sources. Returns summary dict."""
    print(f"\n{'='*50}")
    print(f"🔄 SCAN-ALL gestartet: {datetime.now().isoformat()}")
    print(f"{'='*50}")

    db = get_db()
    rows = db.execute(
        "SELECT * FROM sources WHERE enabled = 1 ORDER BY name"
    ).fetchall()
    db.close()

    results = {}
    total_new = 0

    for row in rows:
        source = dict(row)
        count = scan_source(source)
        results[source.get("name", source["url"])] = count
        total_new += count
        time.sleep(1)  # Rate limiting between sources

    print(f"\n{'='*50}")
    print(f"✅ SCAN-ALL beendet: {total_new} neue Einträge insgesamt")
    print(f"{'='*50}")

    return {
        "status": "done",
        "total_new": total_new,
        "sources": results,
        "scanned_at": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    # Test: scan all sources
    result = scan_all_sources()
    print(json.dumps(result, indent=2, ensure_ascii=False))