# Issue #10 — Target Expectation

## Ziel
YouTube-Einträge bekommen Thumbnails + eine dedizierte Galerie-Ansicht ("YouTube-Log") im Frontend.

## Akzeptanzkriterien

### Backend (`database.py`)
- [x] Migration: `ALTER TABLE entries ADD COLUMN thumbnail_url TEXT` via `migrate_db()`

### Pipeline (`pipeline.py`)
- [x] Vor YouTube-Download: `yt-dlp --print thumbnail <url>` ausführen, URL extrahieren
- [x] `process_youtube()` gibt `thumbnail_url` im Ergebnis-Dict zurück
- [x] `_process_entry_background()` speichert `thumbnail_url` in der DB (UPDATE entries SET thumbnail_url = ?)

### API (`server.py`)
- [x] Keine expliziten Änderungen nötig — `SELECT *` liefert `thumbnail_url` automatisch

### Frontend (`index.html`)
- [x] Neuer Tab "YouTube-Log" mit Galerie-Ansicht (neben Status, Einträge, Dump)
- [x] Galerie-Layout (CSS Grid): Thumbnail (16:9), Titel, Kanal, Datum
- [x] Klick auf einen YouTube-Eintrag in der Galerie → Detail-Modal (wie #12)
- [x] Bestehende Listen-Ansicht bleibt als Standard erhalten
- [x] Fallback: Wenn kein Thumbnail vorhanden → Platzhalter (🎬)

### Verifikation
- [x] Server starten — DB-Migration läuft automatisch (thumbnail_url Spalte vorhanden)
- [x] `GET /api/entries?source=youtube` zeigt `thumbnail_url` im Response
- [x] YouTube-Log-Tab zeigt Thumbnails + Titel + Kanal + Datum
- [x] Klick auf Galerie-Eintrag → Modal mit Detailansicht
- [x] Bestehende Listen-Ansicht funktioniert weiterhin