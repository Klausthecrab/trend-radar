# Issue #9 — Target Expectation

## Ziel
Während ein YouTube-Video verarbeitet wird, zeigt das UI den aktuellen Pipeline-Schritt an. Kein Fortschrittsbalken (zu schnell: ~25s), sondern **Status-Schritt-Anzeige**.

## Akzeptanzkriterien

### Backend (`database.py` + `server.py` + `pipeline.py`)
- [x] `entries`-Tabelle hat neue Spalte `processing_step TEXT` (nullable)
- [x] Pipeline setzt Schritt-Updates: `downloading` → `transcribing` → `analyzing` → `done`
- [x] Während Pipeline läuft: `entries.status = 'processing'`
- [x] Nach Abschluss: `status = 'unread'`, `processing_step = NULL`
- [x] API: `GET /api/entries` liefert `processing_step` im Response
- [x] DB-Migration via ALTER TABLE (kein Reset)

### Frontend (`index.html`)
- [x] Eintrag mit `status='processing'` zeigt pulsierendes "Processing"-Badge (cyan)
- [x] Darunter: lesbarer Schritt-Name (z.B. "📥 Lade Audio herunter...")
- [x] Auto-Polling alle 5s für Processing-Einträge
- [x] Nach Abschluss: Eintrag aktualisiert sich automatisch

### Verifikation
- [x] Server starten, YouTube-Dump abschicken → Processing-Schritte sichtbar
- [x] Nach ~25-30s: Eintrag ist fertig analysiert, Badge wechselt zu "unread"