# Issue #12 — Target Expectation

## Ziel
Klick auf einen Eintrag in der Einträge-Liste öffnet ein Modal/Overlay mit vollständigem Content, Analyse und Obsidian-Link — statt nur den truncated Text zu sehen.

## Akzeptanzkriterien

### Backend
- Keine Backend-Änderungen nötig — `GET /api/entries/:id` liefert bereits alle Felder (title, content, analysis, obsidian_note_path, status, processing_step, etc.)

### Frontend (`frontend/index.html`)
- [ ] Klick auf `entry-card.title` öffnet ein Modal-Overlay (nicht `window.open`)
- [ ] Modal zeigt:
  - Titel + Quellen-Badge
  - Vollständiger Content / Transkript (nicht truncated)
  - Vollständige Analyse: Alle Topics mit `what_is`, `homelab_value`, `hermi_value` (nicht nur die ersten 2)
  - Relevanz-Sterne
  - Obsidian-Link (falls vorhanden) als Obsidian-URI
  - Pipeline-Status (bei `status=processing`)
  - Link zum Original
- [ ] Schließen per Klick auf Hintergrund oder X-Button
- [ ] Escape-Taste schließt Modal
- [ ] Keine Scroll-Blockade-Probleme (Body kann im Hintergrund scrollen bleiben)

### Verifikation
- [ ] Server starten, Einträge laden, auf Dummy-Eintrag klicken → Modal öffnet sich
- [ ] Volles Transkript sichtbar (nicht abgeschnitten)
- [ ] Alle Analyse-Topics sichtbar
- [ ] Modal schließt per X, Klick außerhalb, Escape