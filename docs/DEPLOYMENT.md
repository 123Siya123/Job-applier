# Deployment — 24/7-Betrieb

## Lokal (manuell)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env                # eigene Werte eintragen
cp config/profile.example.json config/profile.json
cp config/search_params.example.json config/search_params.json

python -m scripts.init_db
python -m src.main
```

Dashboard: http://localhost:8765

## Windows — Task Scheduler (24h)

Die robusteste Variante ist eine Task-Scheduler-Aufgabe:

1. Aktion: **Programm starten** → `C:\Path\To\Job-applier\.venv\Scripts\python.exe`
2. Argumente: `-m src.main`
3. Starten in: `C:\Path\To\Job-applier`
4. Trigger: **Beim Start des Computers** + **Bei Anmeldung**
5. Einstellungen:
   - "Task neu starten, wenn fehlgeschlagen, alle 5 Min"
   - "Task anhalten, wenn länger als 24 Std läuft" — **ausschalten**
6. Sicherheitsoptionen:
   - "Nur ausführen wenn Benutzer angemeldet ist" (Pflicht für Headed-Browser)

Hinweis: der Browser ist sichtbar — der Rechner darf nicht gesperrt sein,
sonst sieht OpenClaw kein Display.

## Windows — Monitoring als separater Cron

Wenn das Hauptsystem nicht läuft (Backup-Scenario), sorgt ein dedizierter
Task dafür, dass die E-Mail-Verarbeitung trotzdem alle 2h passiert:

1. Aktion: `python.exe -m scripts.run_monitor`
2. Trigger: **Täglich, alle 2 Stunden, unbegrenzt**

## Linux / macOS

`systemd`-Service (Linux):

```ini
# /etc/systemd/system/job-applier.service
[Unit]
Description=Job-Applier multi-agent system
After=network-online.target

[Service]
Type=simple
User=YOURUSER
WorkingDirectory=/home/YOURUSER/Job-applier
ExecStart=/home/YOURUSER/Job-applier/.venv/bin/python -m src.main
Restart=always
RestartSec=10
Environment="DISPLAY=:0"          # nur nötig wenn Headed-Browser auf X-Server

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now job-applier
journalctl -u job-applier -f      # Live-Logs
```

`cron` für Monitoring (alternativ):

```cron
0 */2 * * * cd /home/YOURUSER/Job-applier && .venv/bin/python -m scripts.run_monitor
```

## Logging und Beobachtbarkeit

- **Strukturierte Logs**: JSON in `data/applier.log`, lesbar in Console.
- **Dashboard**: live SSE-Stream, fasst Status-Counter und neue Events zusammen.
- **DB**: SQLite-Datei (`data/applier.sqlite3`) — kann mit dem Tool Ihrer
  Wahl (`sqlite3`, DBeaver, …) inspiziert werden.
- **Crash-Recovery**: alle Status werden vor jedem Schritt persistiert; ein
  Neustart fängt nahtlos da an, wo der Crash war.

## Was muss zusätzlich laufen?

- **Browser**: Playwright-Chromium (wird durch `playwright install` geholt)
  oder ein offener echter Chrome bei OpenClaw.
- **Gemini-Quota**: Vision-Calls sind die teuerste Ressource. Default
  `GLOBAL_RATE_LIMIT_RPS=2.0` ist konservativ.
- **IMAP/Gmail App-Password**: für die Mail-Status-Erkennung. Ohne
  funktioniert der Rest weiter — das Monitoring loggt nur eine Warnung.

## Sicherheit / Daten

- `.env` und `config/profile.json` enthalten persönliche Daten und
  API-Keys — `gitignore` hält beide aus dem Repo.
- Die SQLite-Datei enthält Bewerbungs-Historie inkl. Cover-Letter-Texten.
  Backup nach Bedarf.
