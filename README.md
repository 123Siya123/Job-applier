# Job-Applier — Multi-Agent Bewerbungssystem

Eine 24/7 laufende Multi-Agenten-Software, die in **50 Batches** automatisiert nach
Praktikums- und Werkstudentenstellen sucht, sich bewirbt, eingehende Antworten
parst und alles in einem Live-Dashboard visualisiert.

## Architektur in einem Satz

Ein zentraler **Orchestrator** koordiniert vier Agenten — **Sourcing**,
**Application**, **Monitoring**, **Dashboard** — wobei das **Brain** (Gemini 3.1
via API) die Logik/Vision liefert und der **Actor** (OpenClaw lokal) die
sichtbaren UI- und OS-Aktionen ausführt.

```
                     ┌──────────────────────┐
                     │    Orchestrator      │
                     │  (Scheduler · State) │
                     └─────────┬────────────┘
            ┌─────────┬────────┼────────┬─────────────┐
            ▼         ▼        ▼        ▼             ▼
       Sourcing  Application Monitoring Dashboard  Persistence
       Agent     Agent       Agent      (FastAPI)  (SQLite)
            │         │        │
            ▼         ▼        ▼
         Brain (Gemini 3.1)  ←──── Vision · Form-Logic · Cover-Letter
            │
            ▼
         Actor (OpenClaw)    ←──── Maus · Tastatur · Datei-Explorer
            │
            ▼
         Browser (Playwright headed)
```

## Phasen

| Phase | Komponente            | Aufgabe                                                       |
|-------|-----------------------|---------------------------------------------------------------|
| 1     | Configuration         | Profil, Lebenslauf-Pfad, Suchparameter, Relaxation-Strategie  |
| 2     | Sourcing-Agent        | Findet exakt 50 qualifizierte Unternehmen                     |
| 3     | Application-Agent     | Iteriert durch die 50 Firmen, navigiert UIs, lädt CV hoch     |
| 4     | Monitoring-Agent      | IMAP-Cronjob (alle 2h) — parst Antworten, updatet Status      |
| 5     | Dashboard             | FastAPI + Live-UI, zeigt Counter und Status-Aggregate         |

## Schnellstart

```bash
# 1. Dependencies installieren
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# 2. Konfiguration anlegen
cp .env.example .env
cp config/profile.example.json config/profile.json
cp config/search_params.example.json config/search_params.json
# danach .env, profile.json und search_params.json mit eigenen Werten füllen

# 3. Datenbank initialisieren
python -m scripts.init_db

# 4. System starten (orchestriert alle Agenten + Dashboard)
python -m src.main

# Optional: nur Dashboard
python -m scripts.run_dashboard

# Optional: nur Email-Monitor (läuft sonst automatisch im Orchestrator)
python -m scripts.run_monitor
```

Dashboard: http://localhost:8765

## OpenClaw-Integration

Der `Actor` ist eine abstrakte Klasse (`src/actor/actor_interface.py`).
Der vorhandene `PlaywrightActor` ist die direkte Browser-Steuerung.
Um OpenClaw zu integrieren, reicht das Implementieren der Methoden in
`src/actor/openclaw_actor.py` und ein einziger Setting-Wechsel
(`ACTOR_BACKEND=openclaw`). Details: [`docs/OPENCLAW_INTEGRATION.md`](docs/OPENCLAW_INTEGRATION.md).

## Dokumentation

- [Architecture](docs/ARCHITECTURE.md) — Komponenten, Datenfluss, Concurrency
- [OpenClaw Integration](docs/OPENCLAW_INTEGRATION.md) — Schritt-für-Schritt
- [Deployment](docs/DEPLOYMENT.md) — Windows Task Scheduler, 24/7 Betrieb
- [Data Model](docs/DATA_MODEL.md) — SQLite-Schema, JSON-Strukturen

## Anforderungen

- Python 3.11+
- Windows 10/11 (für OpenClaw-Modus) oder beliebiges OS (Playwright-Modus)
- Gemini API-Key (Vision-fähiges Modell)
- IMAP-Zugang oder Gmail App Password

## Lizenz

Privatprojekt — keine Lizenz vergeben.
