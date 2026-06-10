# Issue #6 — Target Expectation

## Ziel
Regelmäßig externe Quellen (Subreddits, RSS-Feeds) scannen, neue Inhalte erkennen, in die DB speichern und durch die Analyse-Pipeline schicken.

## Akzeptanzkriterien

### Backend — Neue Datei `backend/scanner.py`
- [ ] `scan_subreddit(name)` → Holt neueste Posts via `https://www.reddit.com/r/{name}/new.json`, parst Titel/Content/URL/Autor/Datum
- [ ] `scan_rss(url)` → Holt Feed-Items via `feedparser`, parst Titel/Content/URL/Datum
- [ ] `scan_source(source)` → Dispatch zu scan_subreddit/scan_rss je nach source.type
- [ ] `scan_all_sources()` → Loopt über alle enabled sources, ruft scan_source auf
- [ ] Dedup via URL (UNIQUE-Constraint in DB fängt Duplikate ab)
- [ ] Neue Einträge werden mit status='unread' in entries-Tabelle gespeichert
- [ ] Manueller Scan: `POST /api/scan/now` ruft scan_all_sources() auf

### Backend — `backend/pipeline.py`
- [ ] Nicht-YouTube-URLs auch analysieren (Reddit/RSS-Content via LLM)
- [ ] `process_url()` erkennt ob Content vorhanden ist und analysiert direkt

### Backend — `backend/server.py`
- [ ] `POST /api/scan/now` ersetzt Placeholder durch echten scan_all_sources()-Aufruf
- [ ] `scanner` importieren

### Frontend — `frontend/index.html`
- [ ] Neuer Tab "Quellen" (als 5. Tab)
- [ ] Liste aller Quellen (type, url, name, interval, enabled, last_scanned_at)
- [ ] Button pro Quelle: Löschen
- [ ] Formular zum Hinzufügen einer neuen Quelle (type, url, name, interval)
- [ ] Button "Jetzt scannen" zum Triggern von POST /api/scan/now

### Verifikation
- [ ] Server starten → Quellen-Tab sichtbar
- [ ] Quelle hinzufügen (z.B. RSS-Feed) → in sources-Tabelle sichtbar
- [ ] "Jetzt scannen" → NEue Einträge in entries-Tabelle
- [ ] Analysierte Einträge im Einträge-Tab sichtbar (mit Analyse-Daten)