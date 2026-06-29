# Trend-Radar — Vision (v3 — Befehlsstand)

> **Sammelstelle für Quellen. Befehlsstand für Analyse.**
> Trend Radar ist das Dashboard. Hermes Agent ist der Arbeiter. Der Kanban ist die To-Do-Liste.
>
> *Stand: Juni 2026 — v3-Planung, Ablösung der v2-Eigenanalyse.*

---

## Was hat sich geändert (v2.1 → v3)

| Aspekt | v2.1 (aktuell) | v3 (Ziel) |
|--------|----------------|-----------|
| **Analyse** | LLM läuft in Trend Radar (server.py-Threads) | LLM läuft in Hermes Agent via Kanban |
| **Transkription** | yt-dlp + Venice STT in Trend Radar | Hermes Agent via Kanban |
| **Trilium-Ablage** | ETAPI in Trend Radar | Hermes Agent via Kanban |
| **Trend Radar** | Macht alles | Nur Dashboard + Speicher |
| **Automatisierung** | Immer sofort (keine Wahl) | Automatik-AN/AUS-Schalter |
| **YouTube-Route** | Gleicher Ablauf wie Web | Eigener Stufen-Pfad mit Transkript |

---

## Stufen-System — Zwei Routen

Jeder Eintrag durchläuft Phasen. Welche Route hängt vom `source_type` ab.

### Route A: Web / Reddit / Blog

```
📥 Neu eingetroffen
    ↓
🔎 LLM-Analyse (Hermes Agent)
    ↓
🏷️ Trilium-Pfad vorschlagen (Hermes Agent)
    ↓
✅ In Trilium abgelegt
```

### Route B: YouTube

```
📥 Neu eingetroffen
    ↓
🎤 Transkribieren (yt-dlp + Venice STT, Hermes Agent)
    ↓
🔎 LLM-Analyse (Hermes Agent)
    ↓
🏷️ Trilium-Pfad vorschlagen (Hermes Agent)
    ↓
✅ In Trilium abgelegt
```

---

## Zwei Modi

### 🔘 Automatik AN (default)

Neue Einträge landen automatisch als Kanban-Karte im Hermes-Agent-Kanban.
Hermes Agent arbeitet jede Karte Schritt für Schritt ab:
1. Holt Daten aus Trend Radar
2. Führt Analyse / Transkription durch
3. Schreibt Ergebnis zurück in Trend Radar (`PATCH /api/entries/:id`)
4. Markiert nächsten Schritt im Kanban
5. Wiederholt bis Status = `filed`

### 🔘 Automatik AUS

Neue Einträge bleiben liegen. Max klickt pro Karte auf **"📤 Ins Kanban"**.
Erst dann landet ein Eintrag im Kanban und wird verarbeitet.
Ermöglicht bewusste Auswahl: "Das will ich analysieren, das nicht."

---

## Datenmodell — Status-Werte

| Status | Bedeutung | Rahmenfarbe |
|--------|-----------|-------------|
| `new` | Gerade eingegangen, unbearbeitet | 🟡 Gelb |
| `transcribing` | YouTube: Transkription läuft | 🟠 Orange |
| `transcribed` | YouTube: Transkript fertig | 🟠 Orange |
| `analyzing` | LLM-Analyse läuft | 🔵 Blau (pulsierend) |
| `analyzed` | LLM-Analyse abgeschlossen | 🔵 Blau |
| `path_suggested` | Trilium-Pfad vorgeschlagen | 🟣 Lila |
| `filed` | In Trilium abgelegt | 🟢 Grün |
| `failed` | Fehlgeschlagen | 🔴 Rot |

Zusätzlich: `stage_progress`-Feld (JSON) speichert welche Stufen erreicht sind:
```json
{"received": true, "transcribed": true, "analyzed": true, "path_found": false, "filed": false}
```

---

## UI — Eintrags-Karte mit Fortschritt

Jeder Eintrag wird als Karte dargestellt:

```
┌─────────────────────────────────────────────────────┐
│  #42  🔗 How to deploy your own Docker…             │
│       URL: https://blog.example.com/...              │
│       ───────── Stufen ─────────                     │
│       📥 ✅  🔎 ✅  🏷️ ⏳  ✅ ❌                    │
│       [📤 Manuell ins Kanban]  [🔍 Details]         │
└─────────────────────────────────────────────────────┘
```

YouTube-Karten haben einen extra Punkt:
```
┌─────────────────────────────────────────────────────┐
│  #43  ▶️ Raspberry Pi 5 NAS Build                   │
│       URL: https://youtube.com/...                   │
│       ───────── Stufen ─────────                     │
│       📥 ✅  🎤 ✅  🔎 ✅  🏷️ ⏳  ✅ ❌              │
│       [📤 Manuell ins Kanban]  [🔍 Details]         │
└─────────────────────────────────────────────────────┘
```

Legende: ✅ erledigt | ⏳ läuft | ❌ offen/ausstehend

---

## Architektur

```
                    ┌──────────────────┐
                    │   Max' Browser   │
                    │ (Brave, Extension)│
                    └────────┬─────────┘
                             │ POST /api/entries (URLs)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│               Trend Radar — Befehlsstand (Port 9126)          │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Dump     │  │ Einträge │  │ Playlists│  │ Telegram     │ │
│  │ (Input)  │  │ (Dashboard)│ │          │  │ (Inbox)      │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  SQLite — entries mit Status + stage_progress         │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  🔘 Automatik AN/AUS  │  [📤 Ins Kanban] (pro Eintrag)     │
└──────────────────────────┬───────────────────────────────────┘
                           │ Kanban-Karte erzeugen
                           ▼
┌──────────────────────────────────────────────────────────────┐
│               Hermes Agent Kanban                              │
│                                                               │
│  🔲 #42 — Analyse: blog.example.com (URL)                    │
│  🔲 #43 — Transkribieren: YouTube-Video                      │
│  🔲 #44 — Trilium-Pfad: #43                                  │
│                                                               │
│  Hermes Agent arbeitet ab:                                    │
│  → Holt Daten aus Trend Radar API                             │
│  → Führt LLM / yt-dlp / STT aus                              │
│  → Schreibt Ergebnis zurück (PATCH /api/entries/:id)         │
│  → Markiert Stufe als erledigt                                │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼ (wenn filed)
                    ┌──────────────────┐
                    │     Trilium      │
                    │ (Ressourcen)     │
                    └──────────────────┘
```

## API-Endpunkte (neu/geändert gegenüber v2.1)

| Endpunkt | Methode | Beschreibung |
|----------|---------|-------------|
| `GET /api/entries` | GET | Alle Einträge (filterbar nach status, source) |
| `POST /api/entries` | POST | Neuen URL-Eintrag einreichen |
| `GET /api/entries/:id` | GET | Detailansicht mit Analyse + stage_progress |
| `PATCH /api/entries/:id` | PATCH | **NEU:** Hermes Agent schreibt Ergebnis zurück (Transkript, Analyse, Pfad, Status) |
| `POST /api/entries/:id/kanban` | POST | **NEU:** Eintrag ins Kanban schicken (manuell) |
| `GET /api/config` | GET | **NEU:** Aktuelle Einstellungen (automatik_an/aus) |
| `PATCH /api/config` | PATCH | **NEU:** Einstellungen ändern |
| *(entfällt)* | — | `POST /api/entries/:id/analyze` — entfällt (macht jetzt Kanban) |
| *(entfällt)* | — | `POST /api/entries/:id/trilium` — entfällt (macht jetzt Kanban) |

---

## Tabs (unverändert zu v2.1)

| Tab | Zweck |
|-----|-------|
| 📊 Status | Server-Health + Entry-Statistiken |
| 📋 Einträge | Alle Einträge browsen / filtern — hier sieht Max die Karten mit Fortschritt |
| 📋 Playlists | YouTube-Playlists verwalten |
| 📨 Telegram | Telegram-Inbox verwalten |
| 📥 Dump | URLs einwerfen (Haupt-Eingabe) |
| 🎬 YouTube-Log | YouTube-Galerie mit Thumbnails |

---

## Input-Quellen (alle unverändert)

- **Browser-Extension** (Brave) — alle Tabs schließen + senden (#28-#31)
- **Dump-Tab** (Web-UI) — URLs per Textarea einwerfen
- **Telegram-Bot** — URL an Bot schicken → Telegram-Inbox
- **YouTube-Playlists** — Playlist scannen → Videos browsen → übernehmen

Alle münden in `POST /api/entries` → landen in der Einträge-Tabelle. Kein Unterschied im Backend, nur unterschiedliche Frontend-Wege.

---

## Nächste Schritte (Umsetzung)

1. ⬜ **Kanban-API** — `POST /api/entries/:id/kanban` + `PATCH /api/entries/:id` (#33)
2. ⬜ **Automatik-Schalter im UI** — Toggle + Config-Endpoint (#34)
3. ⬜ **Manuell-Button pro Eintrag** — "📤 Ins Kanban" (#35)
4. ⬜ **Phasen-Fortschritt im UI** — Balken + Icons pro Karte (#36)
|5. ✅ **YouTube-Route** — source_type=youtube → eigener Stufen-Pfad (#37)
6. ✅ **Hermes Skill: Trend-Radar-Analyse** — Der Kanban-Skill (#38)
7. ✅ **Alte Analyse-Threads entfernen** — pre_analyze + analyze aus server.py raus (#47)
8. ✅ **Kanban-Worker-Cron** — Hermes Agent Cron-Job alle 5 Min (#43)

---

## Kanban-Worker-Cron (Issue #43)

Ein Hermes-Cron-Job pollt alle 5 Minuten das `trend-radar`-Kanban-Board und verarbeitet `ready`-Karten.

### Konfiguration

| Feld | Wert |
|------|------|
| **Job-ID** | `f6c6076d40b2` |
| **Name** | `trend-radar-kanban-worker` |
| **Schedule** | Alle 5 Minuten (`every 5m`) |
| **Skills** | `trend-radar-analysis`, `butler-homelab-profile`, `knowledgeskill-secrets-v1` |
| **Model** | `deepseek-v4-flash` (Provider: `custom`) |
| **Deliver** | `local` (keine Benachrichtigung, nur Log) |
| **Toolsets** | `terminal`, `web`, `file` |

### Worker-Workflow (pro Tick)

1. **Pre-Flight:** Server erreichbar? Automatik AN?
2. **Board poller:** `hermes kanban --board trend-radar list --status ready --json` → erste ready-Karte
3. **Entry-ID + Schritt extrahieren** aus Kartentitel (Regex: `Eintrag #(\d+)` + `Schritt: (\w+)`)
4. **Schritt ausführen** laut `trend-radar-analysis`-Skill:
   - `extract` (Web): HTML→Text
   - `extract` (YouTube): yt-dlp Metadaten
   - `transcribe`: yt-dlp + Venice STT
   - `analyze`: LLM-Analyse
   - `suggest_path`: Trilium-Struktur + LLM-Pfad-Vorschlag
   - `file`: Trilium-Note via ETAPI
5. **PATCH /api/entries/:id** → Ergebnis + stage_progress
6. **Karte abschließen** + nächste Schritt-Karte anlegen
7. **Bei Fehler:** Status `failed`, Karte in `review`