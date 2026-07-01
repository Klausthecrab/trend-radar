# Browser-Extension einrichten

> **Voraussetzung:** Trend Radar läuft auf Butler (Port 9126). Du bist per SSH verbunden.

---

## 1. SSH-Tunnel — eine Zeile für `butler-tunnel.vbs`

Öffne deine `butler-tunnel.vbs` (Windows) und füge diese Zeile ein:

```vbs
-L 9126:127.0.0.1:9126
```

**Beispiel — wie es aussehen könnte:**

```vbs
' Butler Tunnel
WScript.CreateObject("WScript.Shell").Run "cmd /c ssh butleruser@butler -L 18080:127.0.0.1:8080 -L 9124:127.0.0.1:9124 -L 9126:127.0.0.1:9126 -N", 0, False
```

**Was passiert:** Port 9126 auf deinem Windows-Rechner wird durchgereicht zu Port 9126 auf Butler. Du erreichst Trend Radar dann unter `http://localhost:9126` im Brave-Browser.

---

## 2. Extension in Brave laden

1. Brave öffnen → `brave://extensions` in die Adressleiste tippen
2. Oben rechts: **"Entwicklermodus"** aktivieren (Toggle anschalten)
3. Klick auf **"Entpackte Erweiterung laden"**
4. Wähle den Ordner `trend-radar/extension/` aus (dort wo `manifest.json` liegt)
5. Die Extension erscheint in der Toolbar mit einem 📡-Icon

> **Tipp:** Wenn das Repo auf Windows liegt (z.B. `C:\Users\Max\repos\trend-radar\extension\`), wähle den lokalen Pfad. Wenn du über SSH arbeitest, kopiere den `extension/`-Ordner vorher auf Windows oder klone das Repo dort.

---

## 3. Test-Szenario

1. **SSH-Tunnel starten** (Doppelklick auf `butler-tunnel.vbs`)
2. **Brave** → 3 beliebige Tabs öffnen (z.B. Reddit, YouTube, Blog)
3. **Extension-Icon** (📡) in der Toolbar klicken
4. Du siehst alle 3 Tabs in der Liste
5. **"📡 Alle schließen & senden"** klicken
6. ✅ Alle 3 Tabs werden geschlossen
7. Trend Radar auf `http://localhost:9126` öffnen → 3 neue Einträge mit Status `new`

---

## 4. Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| Extension-Icon grau / Popup zeigt "Lade Tabs …" ewig | Keine http/https-Tabs offen | Normale Webseite öffnen, nicht `brave://`-Seiten |
| "Verbindung fehlgeschlagen" beim Senden | Tunnel läuft nicht | `butler-tunnel.vbs` prüfen, Port 9126 muss in der Zeile sein |
| "Nur 2/3 gesendet" | Ein POST war fehlerhaft | Trend Radar läuft? `localhost:9126` im Browser aufrufen |
| Extension wird nicht geladen | manifest.json nicht gefunden | Stelle sicher dass du den `extension/`-Ordner auswählst, nicht das Repo-Root |
| Popup öffnet sich nicht | Extension deaktiviert | `brave://extensions` → "Trend Radar" muss AN sein |
| Duplikate in Trend Radar | Gleiche URL zweimal gesendet | Backend hat `INSERT OR IGNORE` — Duplikate werden ignoriert |

---

## 5. Dateien (nur zum Nachschlagen)

```
extension/
├── manifest.json     ← Chrome Extension v3 Konfiguration
├── popup.html        ← Popup-HTML (was du siehst)
├── popup.css         ← Dark-UI (Catppuccin Mocha)
├── popup.js          ← Logik: Tabs laden + senden + schließen
└── icons/            ← Toolbar-Icons (16/48/128px)
```

Kein Build-Tool, kein npm, kein Node.js nötig. Die Extension ist reiner HTML/CSS/JS-Code.