# Trend-Radar 📡

Entdeckt Neues, analysiert Relevanz, hält Daten bereit — das Gegenstück zum Release Radar (Changes-Tracking, rückwärtsgerichtet).

**Keine Dashboard-Abhängigkeit** — komplett eigenständig, privates Repo, Port 9126.

## Kurz & Knapp

- **Cron-Scans:** Subreddits, RSS-Feeds, Tech-Blogs — laufen automatisch auf Zeitplan
- **Ad-hoc Dump:** Max wirft Links rein (YouTube, Reddit, URLs) → Analyse-Pipeline
- **Analyse:** Relevanz-Bewertung + Obsidian-Notiz pro Fund
- **CIO-Integration:** Hermi-CIO konsumiert die Analysen für Diskussion mit Max

## Port & Stack

| | |
|---|---|
| **Port** | 9126 |
| **Backend** | Python http.server |
| **Frontend** | Single-Page HTML/CSS/JS (Dark, Glas-Optik) |
| **DB** | SQLite (WAL) in `data/` |
| **Repo** | `~/repos/trend-radar/` (private) |

## Status

In Entwicklung — VISION steht, Grundstruktur ist da.

## Weiterführend

- **[VISION.md](VISION.md)** — Vollständiges Zielbild mit Architektur, Datenmodell, API-Endpunkten
- **GitHub Issues** — Offene Aufgaben im Repo