# Issue #12 — Target Expectation

## Ziel
Klick auf einen Eintrag in der Einträge-Liste öffnet ein Modal/Overlay mit vollständigem Content, Analyse und Obsidian-Link — statt nur den truncated Text zu sehen.

## Akzeptanzkriterien

### Backend
- ✅ Keine Backend-Änderungen nötig — `GET /api/entries/:id` liefert bereits alle Felder (title, content, analysis, obsidian_note_path, status, processing_step, etc.)

### Frontend (`frontend/index.html`)
- [x] Klick auf `entry-card.title` öffnet ein Modal-Overlay (nicht `window.open`)
- [x] Modal zeigt:
  - Titel + Quellen-Badge
  - Vollständiger Content / Transkript (nicht truncated)
  - Vollständige Analyse: Alle Topics mit `what_is`, `homelab_value`, `hermi_value` (nicht nur die ersten 2)
  - Relevanz-Sterne
  - Obsidian-Link (falls vorhanden) als Obsidian-URI
  - Pipeline-Status (bei `status=processing`)
  - Link zum Original
- [x] Schließen per Klick auf Hintergrund oder X-Button
- [x] Escape-Taste schließt Modal
- [x] Keine Scroll-Blockade-Probleme (Body kann im Hintergrund scrollen bleiben)

### Verifikation
- [x] Server starten, Einträge laden, auf Dummy-Eintrag klicken → Modal öffnet sich
- [x] Volles Transkript sichtbar (nicht abgeschnitten)
- [x] Alle Analyse-Topics sichtbar
- [x] Modal schließt per X, Klick außerhalb, Escape