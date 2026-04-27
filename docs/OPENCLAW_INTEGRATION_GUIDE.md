# OpenClaw — Integration & optimale Einstellungen

Diese Anleitung folgt zeitlich auf `TROCKENLAUF_ANLEITUNG.md`. Erst muss
das System mit Playwright stabil laufen, dann übergeben Sie die Steuerung
an OpenClaw, damit ein **echter Mauszeiger sichtbar** über den Bildschirm
fährt und der **Windows-Datei-Explorer** beim CV-Upload bedient wird.

---

## Warum überhaupt OpenClaw?

| Aspekt | Playwright | OpenClaw |
|---|---|---|
| Schnelligkeit | sehr schnell | langsamer, aber sichtbar |
| Bot-Erkennung | manche Portale blocken | weniger auffällig (echter OS-Click) |
| OS-Datei-Dialog | per `set_input_files` (umgeht Dialog) | echte Tastatureingabe im Windows-Dialog |
| Captcha-Lösen | nicht vorgesehen | manuell durch Sie übernehmbar |
| Sie schauen live zu | Browser sichtbar | Maus + Browser sichtbar |

Sobald die Karriereportale realistisch verhalten reagieren sollen
(LinkedIn, Workday, SuccessFactors), zahlt sich OpenClaw aus.

---

## Architektur in 30 Sekunden

```
   ApplicationAgent                FormAnalyzer (Gemini Vision)
        │                                  │
        ▼                                  │
   ActorInterface  ◄──────── (eine Abstraktion, zwei Backends)
        │                                  │
        ├── PlaywrightActor (Trockenlauf)  │
        └── OpenClawActor   (Produktion)   │
                │                          │
                ▼                          │
            JSON-RPC (lokal)               │
                │                          │
                ▼                          │
        OpenClaw-Daemon  ──► OS-Maus, Tastatur, Datei-Dialog
                │
                ▼
        sichtbarer Chrome
```

Der Wechsel zwischen den Backends ist **eine Zeile** in der `.env`.
Kein Agent-Code ändert sich.

---

## 1. OpenClaw installieren und starten

> Die folgenden Hinweise sind generisch — die genauen Befehle hängen davon
> ab, welche OpenClaw-Distribution Sie nutzen. Schauen Sie in deren
> README/Docs für Installer und Startbefehl.

Typischer Ablauf:

1. OpenClaw als Dienst/EXE installieren.
2. Daemon auf einem lokalen Port lauschen lassen — Default in unserer
   Config: **`127.0.0.1:4711`**.
3. Daemon erlaubt JSON-RPC-Aufrufe wie `browser.goto`, `input.click`,
   `input.upload_file` (Methoden-Liste siehe `src/actor/openclaw_actor.py`).
4. Daemon startet einen sichtbaren Chrome (kein Headless), den nur er
   steuert.

**Test-Ping:**
```powershell
curl -X POST http://127.0.0.1:4711/rpc -H "Content-Type: application/json" `
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"session.start\",\"params\":{}}"
```

Sie sollten ein `result`-Objekt zurückbekommen. Wenn nicht: Daemon nicht
erreichbar — OpenClaw-Logs prüfen.

---

## 2. `.env` umstellen (eine Zeile)

```dotenv
# vorher: ACTOR_BACKEND=playwright
ACTOR_BACKEND=openclaw

OPENCLAW_HOST=127.0.0.1
OPENCLAW_PORT=4711
OPENCLAW_TIMEOUT_SECONDS=60
```

Pre-flight kontrollieren:

```powershell
python -m scripts.verify_setup
```

Dann:

```powershell
python -m src.main
```

Sie sollten jetzt sehen, wie der Cursor sichtbar zu jedem Eingabefeld
fährt, klickt, tippt — und beim Upload öffnet sich der Windows-Dialog,
in dem die Pfad-Zeichenfolge sichtbar getippt wird.

---

## 3. Optimale OpenClaw-Einstellungen für hohe Bewerbungsquote

### 3.1 Bildschirmauflösung

**Empfehlung: 1920×1080 oder höher, eine einzige Skalierung (100 %)**.

OpenClaw klickt nach Pixel-Koordinaten, die der `FormAnalyzer` aus dem
Screenshot ermittelt. Bei 125 %/150 % DPI-Skalierung schlagen die Klicks
sonst neben das Ziel.

```
Windows-Einstellungen → System → Anzeige → Skalierung → 100 %
```

Falls Sie zwingend 125 % brauchen: setzen Sie das Browser-Fenster auf
einen Monitor ohne Skalierung oder erzwingen Sie pro App 100 % via
Eigenschaften → Kompatibilität → "DPI-Skalierungsverhalten überschreiben".

### 3.2 Browser-Fenster

- **Position:** linke obere Ecke, **maximiert**, immer im **Vordergrund**
- **Tabs:** OpenClaw startet einen sauberen Chrome ohne weitere Tabs
- **Cookies/Login:** wenn Sie LinkedIn/Stepstone manuell vor-eingeloggt
  haben wollen, lassen Sie OpenClaw mit einem **persistenten User-Profile**
  starten (siehe deren Config). Sonst wird jede Session frisch.

### 3.3 OpenClaw-Daemon-Config

Hängt vom Distributionspaket ab. Sinnvolle Defaults:

| Setting | Empfehlung | Begründung |
|---|---|---|
| Mouse-move-speed | mittel (200–400 ms zu Ziel) | langsamer wirkt natürlicher, aber zu langsam frustriert Sie |
| Click-delay | 80–150 ms zwischen Maus-Down und Up | nahe an menschlich |
| Type-delay | 30–60 ms pro Zeichen | menschlich |
| Wait-after-click | mind. 800 ms | Karrieresites brauchen Zeit |
| Dialog-detection-timeout | 15 s | langsame Datei-Dialoge unter Last |

In unserer `.env` korrespondiert dazu:

```dotenv
# Beim OpenClaw-Modus übernimmt OpenClaw das Tempo selbst,
# unser slow_mo gilt nur im Playwright-Modus.
BROWSER_SLOW_MO_MS=0

# Globaler Rate-Limit für Gemini bleibt:
GLOBAL_RATE_LIMIT_RPS=2.0
```

### 3.4 Windows-Datei-Dialog

Damit der CV-Upload zuverlässig funktioniert, helfen Sie OpenClaw mit:

1. **Sprache des Datei-Dialogs**: ist die Windows-Sprache Deutsch, heißt
   der Dialog "Öffnen". Englisch: "Open". OpenClaw muss **eine** dieser
   Varianten erkennen — manche Distributionen brauchen das in der Config.
2. **Datei-Dialog-Modus**: Standard ("klassisch", nicht „Schnellzugriff").
   Die meisten OpenClaw-Implementierungen tippen den Pfad ins Dateiname-
   Feld und drücken Enter — das funktioniert nur im klassischen Modus
   stabil.
3. **CV-Pfad ohne Sonderzeichen**: Verschieben Sie die PDF nach
   `C:\Bewerbung\Lebenslauf.pdf` — kürzere Pfade sind robuster gegen
   Eingabefehler im Dialog. Pfad in `config\profile.json` aktualisieren.

### 3.5 Captcha- und 2FA-Pausen

Wenn ein Portal eine reCAPTCHA oder 2FA-Mail abfragt:

- Unser `ApplicationAgent` erkennt das (`page_kind=captcha`) und überspringt
  die Bewerbung.
- Mit OpenClaw können Sie **eingreifen**: pausieren Sie OpenClaw kurz
  (Hotkey hängt von der Distribution ab — meist `Pause`/`F12`), lösen Sie
  selbst und resumen. Der Agent würde sonst aufgeben.

### 3.6 Konkurrierende Maus-Aktivität

**Während OpenClaw läuft, nicht selber die Maus benutzen.** OpenClaw
denkt, Sie würden ihm widersprechen, und Klicks landen woanders. Wenn Sie
eingreifen müssen: erst pausieren.

Ein zweiter Monitor mit Tabellen, Browser, etc. ist erlaubt — solange
OpenClaw allein auf seinem Ziel-Monitor klickt.

### 3.7 Sicherheit / Account-Lockouts

LinkedIn und Indeed reagieren empfindlich auf zu schnelle Bewerbungen.
**Default in unserer Config**:

```dotenv
APPLICATION_DAILY_LIMIT=200      # für 50 Firmen genug Spielraum
GLOBAL_RATE_LIMIT_RPS=2.0
```

Für die ersten ein, zwei Tage konservativer:

```dotenv
APPLICATION_DAILY_LIMIT=30
GLOBAL_RATE_LIMIT_RPS=1.0
```

Wenn LinkedIn meckert: Limit weiter senken oder LinkedIn aus den
`platform_queries` in `search_params.json` entfernen.

---

## 4. Wechselbetrieb Playwright ↔ OpenClaw

Sie können jederzeit zwischen den beiden hin und her wechseln —
der Status bleibt in der gleichen SQLite-DB, nichts geht verloren:

```powershell
# OpenClaw aus, schnellen Test mit Playwright machen:
notepad .env       # ACTOR_BACKEND=playwright
python -m src.main

# Zurück zu OpenClaw:
notepad .env       # ACTOR_BACKEND=openclaw
python -m src.main
```

Praxis-Tipp: **Sourcing** (Phase 2) mit Playwright laufen lassen — geht 5×
schneller. Erst die **Application-Phase** auf OpenClaw umstellen.
Realisierbar, indem Sie:

1. `TARGET_COMPANY_COUNT=50` lassen, mit Playwright starten,
2. nach `sourcing_target_reached` mit Strg+C abbrechen,
3. `.env` auf OpenClaw umstellen,
4. `python -m src.main` neu starten — die Application-Phase startet
   direkt mit den schon gefundenen 50 Firmen.

---

## 5. Wenn etwas nicht klappt

| Symptom | Ursache | Fix |
|---|---|---|
| `OpenClaw RPC ... failed` | Daemon nicht erreichbar | OpenClaw-Daemon starten, Port prüfen |
| Klicks landen daneben | DPI-Skalierung ≠ 100 % | Skalierung auf 100 %, neu starten |
| Datei-Dialog wird nicht bedient | Sprache/Modus | Klassischer Dialog, deutsche Sprache, Pfad ohne Umlaute |
| Browser bleibt schwarz | OpenClaw-Profil zerschossen | Profilordner löschen, OpenClaw neu starten |
| Maus zuckt zwischen zwei Punkten | Sie bewegen die Maus parallel | nicht selbst klicken; Hände weg |
| Form-Felder bleiben leer | Vision-Analyse hat Felder nicht erkannt | DOM-Snapshot in `data/applier.log` prüfen, ggf. `BROWSER_SLOW_MO_MS=400` setzen, damit die Seite voll geladen ist |

Logs ansehen:

```powershell
type data\applier.log | Select-String "form_analyzer|click|upload"
```

---

## 6. Sicherheitstipp: 24h-Betrieb

OpenClaw-Modus erfordert ein entsperrtes Display. Empfehlungen:

- Eigener Mini-PC / NUC im Büro, nur für diesen Job
- Bildschirmschoner aus, Sleep aus, Auto-Lock aus
- Stromsparmodus → "Höchstleistung"
- Anti-Virus-Scan außerhalb der Arbeitszeit planen (frisst CPU)

Logs werden so groß, dass Rotieren sinnvoll ist:

```powershell
# einmal pro Woche vor Mitternacht
move data\applier.log data\applier.log.%date:~6,4%-%date:~3,2%-%date:~0,2%
```

---

## 7. Wenn Sie zwischen Maschinen umziehen

Die SQLite-DB (`data/applier.sqlite3`) ist portabel. Kopieren Sie sie auf
die neue Maschine, ebenso `.env` und `config\profile.json` — und das
System läuft weiter, als wäre nichts passiert.

---

## TL;DR — minimale Checkliste für maximale Erfolgsquote

- [ ] Trockenlauf mit Playwright **funktioniert reibungslos** auf 3 Firmen
- [ ] Display 100 % Skalierung
- [ ] Browser maximiert und allein im Vordergrund auf dem Ziel-Monitor
- [ ] OpenClaw-Daemon läuft und antwortet auf `session.start`
- [ ] `.env` → `ACTOR_BACKEND=openclaw`
- [ ] Konservative Limits für die ersten 24 h
- [ ] CV unter `C:\Bewerbung\Lebenslauf.pdf` (kurzer, einfacher Pfad)
- [ ] Maus 24 h nicht selbst bewegen
- [ ] Dashboard im Auge behalten: http://localhost:8765
