# Trend-Radar — Vision

> **Nach vorne statt nach hinten.**
> Gegenstück zum Release Radar (Changes-Tracking, rückwärtsgerichtet).
> Trend-Radar entdeckt Neues, analysiert Relevanz, hält Daten bereit.

## Warum?

Release Radar und Hermi-CIO decken ab: "Was hat sich geändert, was muss ich wissen?"
Trend-Radar deckt ab: "Was kommt auf uns zu, was ist spannend, was sollten wir testen?"

Zwei Richtungen, eine Pipeline: **Trend-Radar → Daten → Hermi-CIO → Diskussion mit Max**

## Drei Säulen

### 1. Regelmäßig (Cron-Scans)
- Bestimmte Subreddits (z.B. r/selfhosted, r/homelab, r/technologie)
- RSS-Feeds von relevanten KI-News / Tech-Blogs
- Laufen automatisch auf Zeitplan (täglich / stündlich)
- Neue Inhalte werden erkannt und durch die Analyse-Pipeline geschickt

### 2. Dump (Ad-hoc)
- Max schmeißt YouTube-Links, Reddit-Threads oder beliebige URLs rein
- Pipeline: Link → Inhalt extrahieren → analysieren
- YouTube: Transkript via Whisper oder API
- Reddit/Web: Content extrahieren

### 3. Analyse-Pipeline (für beide Wege gleich)
- **A) Relevanz-Bewertung:** Was können wir daraus fürs Homelab nutzen?
  - Ist das umsetzbar? Aufwand? Nutzen?
  - Können wir das auf Butler testen?
- **B) Obsidian-Ressource:** Strukturierte Note anlegen
  - Autor, Link, Quelle, Datum
  - Takeaways / Kernaussagen
  - Konkrete Handlungsoptionen

## Architektur

```
┌─────────────────────────────────────────────────────┐
│                  Trend-Radar (9126)                   │
│                                                      │
│  ┌──────────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Cron-Scanner  │  │  Dump-   │  │  Analyse-      │  │
│  │ (Subreddits,  │  │ Endpoint │  │  Pipeline      │  │
│  │  RSS, Blogs)  │  │ (Ad-hoc) │  │  (Relevanz +   │  │
│  │               │  │          │  │   Obsidian)    │  │
│  └──────┬───────┘  └────┬─────┘  └───────┬───────┘  │
│         │               │                │          │
│         └───────┬───────┘                │          │
│                 │                        │          │
│         ┌───────▼────────┐               │          │
│         │  Daten-Halde   │◄──────────────┘          │
│         │  (SQLite)      │                          │
│         └───────┬────────┘                          │
└─────────────────┼───────────────────────────────────┘
                  │
                  │ REST API
                  ▼
┌─────────────────────────────────────────────────────┐
│                 Hermi-CIO (9122)                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Neuer Tab: "Weiterentwicklung"                  │  │
│  │ Listet analysierte Trend-Radar-Einträge        │  │
│  │ Max kann darüber diskutieren:                  │  │
│  │ "Was denkst du dazu?"                          │  │
│  │ "Sollen wir das testen?"                       │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Klare Trennung:**
- **Trend-Radar** sammelt, analysiert, hält Daten vor. Keine Diskussion.
- **Hermi-CIO** konsumiert die fertigen Analysen. Keine Jagd/Sammlung.

## Web-UI (Trend-Radar selbst, Port 9126)

Eigenes Web-UI (Standalone, kein Dashboard-Portal) für Monitoring:

- **Status-Tab:** Läuft der Cron? Wann wurde zuletzt gescannt? Fehlerlogs?
- **Einträge-Tab:** Alle gesammelten + analysierten Einträge (sortierbar, filterbar)
- **Dump-Tab:** Eingabe für Ad-hoc-Links (YouTube, Reddit, URL)
- **Quellen-Tab:** Verwaltung der Cron-Quellen (Subreddits, RSS-Feeds)

## Datenmodell (SQLite)

```sql
CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,          -- 'subreddit', 'rss', 'youtube_channel'
  url TEXT NOT NULL,           -- die Quelle
  name TEXT,                   -- lesbarer Name
  interval TEXT DEFAULT 'daily', -- Scan-Intervall
  enabled INTEGER DEFAULT 1,
  last_scanned_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER,           -- NULL bei Ad-hoc
  source_type TEXT NOT NULL,   -- 'reddit', 'youtube', 'web', 'rss'
  url TEXT NOT NULL UNIQUE,
  title TEXT,
  content TEXT,                -- extrahierter Inhalt / Transkript
  author TEXT,
  published_at TEXT,
  analysis TEXT,               -- JSON: Relevanz, Takeaways, Handlungsoptionen
  obsidian_note_path TEXT,     -- Pfad zur erstellten Obsidian-Note
  status TEXT DEFAULT 'unread',-- 'unread', 'read', 'discussed'
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE analysis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL,
  relevance_score INTEGER,     -- 1-5
  summary TEXT,                -- Kurzfassung (1-3 Sätze)
  takeaways TEXT,              -- JSON-Array
  action_items TEXT,           -- JSON-Array
  obsidian_note TEXT,          -- Volltext der Obsidian-Note
  created_at TEXT DEFAULT (datetime('now'))
);
```

## API-Endpunkte

| Endpunkt | Methode | Beschreibung |
|----------|---------|-------------|
| `/api/health` | GET | Health-Check |
| `/api/entries` | GET | Alle Einträge (filterbar nach status, source) |
| `/api/entries` | POST | Neuen Ad-hoc-Eintrag einreichen (URL + optional source_type) |
| `/api/entries/:id` | GET | Detailansicht mit Analyse |
| `/api/sources` | GET | Alle Cron-Quellen |
| `/api/sources` | POST | Neue Quelle hinzufügen |
| `/api/sources/:id` | DELETE | Quelle entfernen |
| `/api/scan/now` | POST | Manuellen Scan anstoßen |
| `/api/scan/status` | GET | Letzter Scan-Status + Timestamp |
| `/api/cio/entries` | GET | Für Hermi-CIO: unread/analysierte Einträge |
| `/api/cio/entries/:id` | PATCH | Status ändern (read, discussed) |

## Integration Hermi-CIO

Hermi-CIO bekommt einen neuen Tab "Weiterentwicklung":

1. Ruft `GET /api/cio/entries` von Trend-Radar auf
2. Zeigt Liste der analysierten Einträge (Titel, Relevanz, Kurzfassung)
3. Max klickt einen an → Detailansicht → kann mit CIO diskutieren
4. CIO kann Status auf "discussed" setzen

## Port & Stack

| | |
|---|---|
| **Port** | 9126 |
| **Backend** | Python http.server (wie Hermi Hub, kein Flask) |
| **Frontend** | Single-Page HTML/CSS/JS (Dark-Theme, Glas-Optik) |
| **DB** | SQLite (WAL) in `data/trend-radar.db` |
| **Repo** | `~/repos/trend-radar/` (GitHub: Klausthecrab/trend-radar) |
| **Registry-Typ** | `service` |
| **Start** | `python3 backend/server.py` |

## Nächste Schritte (Phase 1)

1. Backend-Server mit Health-Endpoint + SQLite-Schema
2. Cron-Scanner für RSS-Feeds (via feedparser oder requests + HTML-Parsing)
3. Dump-Endpoint (POST URL → Analyse-Pipeline)
4. Frontend: Status + Einträge-Tab
5. GitHub Repo + Obsidian-Doku
6. Hermi-CIO Integration (Tab "Weiterentwicklung")