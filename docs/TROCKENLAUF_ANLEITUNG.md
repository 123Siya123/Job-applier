# Trockenlauf — Schritt für Schritt

Diese Anleitung führt Sie auf Ihrer **Windows-Maschine** durch den ersten
sichtbaren Lauf. Ziel: das System startet, sucht Firmen, öffnet einen
Browser, füllt ein Formular sichtbar aus — alles unter Ihrer Aufsicht,
mit Playwright (kein OpenClaw nötig). Erst wenn das stabil funktioniert,
wechseln Sie auf OpenClaw — siehe `OPENCLAW_INTEGRATION_GUIDE.md`.

---

## 0. Vorbereitung (einmalig)

### 0.1 Geleakte Credentials rotieren

> **Wichtig:** Falls Sie API-Keys oder ein Gmail-App-Passwort jemals in
> einem Chat oder E-Mail im Klartext gepostet haben — **jetzt** widerrufen
> und neu erzeugen, bevor Sie weitermachen.

- Gemini-Keys: https://aistudio.google.com/apikey → alte löschen, neue erzeugen
- Gmail-App-Password: https://myaccount.google.com/apppasswords → altes löschen, neues erzeugen

### 0.2 Voraussetzungen prüfen

- Windows 10/11
- Python 3.11 oder neuer (`python --version`)
- Git (`git --version`)
- ~2 GB freier Plattenplatz (für Chromium)

---

## 1. Repo holen

```powershell
cd C:\Users\<IhrName>
git clone https://github.com/123Siya123/Job-applier.git
cd Job-applier
git checkout main
```

## 2. Virtuelle Umgebung + Dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Der letzte Befehl lädt einen Headless-Chromium herunter (~150 MB). Das
brauchen Sie auch für den Headed-Modus.

## 3. Konfigurationsdateien anlegen

Drei Dateien — alle drei sind via `.gitignore` aus dem Repo
ausgeschlossen, also nie versehentlich committet.

```powershell
copy .env.example .env
copy config\profile.example.json config\profile.json
copy config\search_params.example.json config\search_params.json
```

### 3.1 `.env` ausfüllen

```dotenv
# Mehrere Keys mit Komma — automatischer Failover bei Quota
GEMINI_API_KEYS=NEUER_KEY_1,NEUER_KEY_2,NEUER_KEY_3,NEUER_KEY_4
GEMINI_MODEL=gemini-2.5-pro
GEMINI_VISION_MODEL=gemini-2.5-pro

# Trockenlauf läuft mit Playwright im sichtbaren Modus
ACTOR_BACKEND=playwright
BROWSER_HEADED=true
BROWSER_SLOW_MO_MS=120

# Gmail mit App-Password (NICHT Ihr normales Gmail-Passwort)
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_IMAP_USER=ihre.adresse@gmail.com
EMAIL_IMAP_PASSWORD=neues-app-password-ohne-leerzeichen
EMAIL_POLL_INTERVAL_MINUTES=120

DATABASE_URL=sqlite+aiosqlite:///./data/applier.sqlite3

DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8765

TARGET_COMPANY_COUNT=50
APPLICATION_DAILY_LIMIT=200
SOURCING_DAILY_LIMIT=300
GLOBAL_RATE_LIMIT_RPS=2.0

LOG_LEVEL=INFO
LOG_FILE=./data/applier.log
```

> **Hinweis zum Modellnamen:** Falls "gemini-3.1-pro" für Ihren Account
> noch nicht freigeschaltet ist, nutzen Sie `gemini-2.5-pro` (sowohl text
> als auch vision).

### 3.2 `config/profile.json` ausfüllen

Mindestens diese Felder mit echten Werten füllen:

```json
{
  "personal": {
    "first_name": "Siyamthanda",
    "last_name": "Kuhlmann",
    "email": "ihre.adresse@gmail.com",
    "phone": "+49 170 0000000",
    "address": {
      "street": "Ihre Straße 1",
      "postal_code": "60311",
      "city": "Frankfurt am Main",
      "country": "Deutschland"
    },
    "linkedin": "https://www.linkedin.com/in/...",
    "languages": [
      { "language": "Deutsch", "level": "Muttersprache" },
      { "language": "Englisch", "level": "C1" }
    ]
  },
  "education": [
    { "degree": "...", "institution": "...", "start": "2022-10", "gpa": "..." }
  ],
  "experience": [
    { "title": "...", "company": "...", "start": "...", "end": null, "description": "..." }
  ],
  "skills": {
    "hard": ["Python", "C++", "ROS2"],
    "soft": ["Selbstständige Arbeitsweise"]
  },
  "files": {
    "cv_path": "C:\\Downloads\\Kuhlmann_Siyamthanda_Bewerbung.pdf"
  },
  "self_description": "Mechatronik-Student … (2–4 Sätze)",
  "preferences": {
    "earliest_start": "2026-06-01",
    "min_weekly_hours": 10,
    "max_weekly_hours": 20,
    "remote_ok": true,
    "willing_to_relocate": false
  }
}
```

> Pfade auf Windows brauchen **doppelte Backslashes** in JSON.

### 3.3 `config/search_params.json` (meist nur prüfen)

Defaults passen schon zu Frankfurt + Mechatronik/Robotik. Erweitern bei
Bedarf.

## 4. Datenbank anlegen

```powershell
python -m scripts.init_db
```

Erzeugt `data/applier.sqlite3`. Nach Schema-Änderungen einfach `del data\applier.sqlite3`
und Befehl wiederholen.

## 5. Pre-Flight-Check (der eigentliche Trockenlauf)

```powershell
python -m scripts.verify_setup
```

Erwartete Ausgabe (verkürzt):

```
======================================================================
  Job-Applier — Pre-Flight Check
======================================================================
  [OK]   Found 4 Gemini API key(s): AIzaSy…YAg, AIzaSy…abM, …
  [OK]   Profile loaded — CV at C:\Downloads\Kuhlmann_Siyamthanda_Bewerbung.pdf
  [OK]   Search params: target=50, city=Frankfurt am Main, job_types=Praktikum, Werkstudent
  [OK]   SQLite DB ready at sqlite+aiosqlite:///./data/applier.sqlite3
  [OK]   IMAP login OK — 5 recent messages visible
  [OK]   Gemini reachable — answered: 'pong'
======================================================================
  All checks passed (0 warning(s)) — ready to run `python -m src.main`
```

**Bei FAIL:** die Zeile sagt genau, was falsch ist. Häufige Ursachen:

| Fehler | Ursache | Fix |
|---|---|---|
| CV path not found | Pfad falsch in profile.json | doppelte Backslashes, Datei wirklich vorhanden |
| IMAP login failed | App-Password mit Leerzeichen | Leerzeichen entfernen, "2FA muss aktiv sein" prüfen |
| Gemini ping failed: NOT_FOUND | Modellname nicht freigeschaltet | `gemini-2.5-pro` versuchen |
| Gemini ping failed: API key invalid | falscher/abgelaufener Key | Key in AI Studio neu erzeugen |
| Profile JSON invalid | JSON-Syntax-Fehler | mit `python -m json.tool config\profile.json` prüfen |

## 6. Kleiner sichtbarer Probelauf

Bevor Sie den 24h-Lauf anwerfen, lassen Sie nur **einen einzigen Batch**
laufen. Dafür temporär in `.env`:

```dotenv
TARGET_COMPANY_COUNT=3
APPLICATION_DAILY_LIMIT=3
```

Dann:

```powershell
python -m src.main
```

Was sollte passieren — **in dieser Reihenfolge**:

1. Konsole zeigt `sourcing_started`
2. Konsole zeigt `sourcing_query` für Frankfurt-Begriffe
3. Konsole zeigt `sourcing_added count=...`
4. Sobald 3 Firmen erreicht: `sourcing_target_reached`
5. **Browser-Fenster öffnet sich sichtbar**
6. Browser navigiert zur ersten Karriereseite
7. Ein Job wird angeklickt, Felder werden gefüllt
8. Dashboard auf http://localhost:8765 zeigt Live-Aktualisierung

Mit `Strg+C` sauber beenden. Status bleibt in der DB erhalten.

## 7. Wenn alles flüssig läuft → echter Lauf

`.env` zurücksetzen:

```dotenv
TARGET_COMPANY_COUNT=50
APPLICATION_DAILY_LIMIT=200
```

und

```powershell
python -m src.main
```

Browser öffnet sich, läuft 24/7. Dashboard zeigt Fortschritt 0/50 → 50/50.
Bei Bugs: Strg+C, Logs in `data/applier.log` ansehen, Fix machen, neu
starten — die DB wird nicht verworfen.

## 8. Was beobachten Sie sinnvoll mit?

- **Dashboard** http://localhost:8765 — Counter und Live-Events
- **Log** `data/applier.log` — strukturierte JSON-Logs
- **DB** `data/applier.sqlite3` — `sqlite3 data\applier.sqlite3 "SELECT name, status FROM companies"`

## 9. Häufige Anpassungen

### Nur eine bestimmte Stadt durchsuchen

`config/search_params.json` → `primary_location.city` ändern, evtl.
`neighbouring_cities_seed` leeren.

### Andere Branchen

`config/search_params.json` → `fields` und `search_keywords.abstract_queries`.

### Bewerbungs-Tempo bremsen

`.env`:
```dotenv
BROWSER_SLOW_MO_MS=400
GLOBAL_RATE_LIMIT_RPS=0.5
APPLICATION_DAILY_LIMIT=20
```

---

## Wenn der Trockenlauf zufriedenstellend funktioniert

Lesen Sie als Nächstes **`OPENCLAW_INTEGRATION_GUIDE.md`** in diesem
Verzeichnis. Dort steht, wie Sie auf OpenClaw umstellen — meistens reicht
`ACTOR_BACKEND=openclaw` in `.env`.
