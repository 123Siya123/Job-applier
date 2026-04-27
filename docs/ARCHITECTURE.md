# Architektur

## Komponenten-Diagramm

```
                              ┌─────────────────────────┐
                              │      Orchestrator       │
                              │  (asyncio main loop)    │
                              └──────────┬──────────────┘
                                         │
       ┌─────────────────┬───────────────┼───────────────┬──────────────────┐
       ▼                 ▼               ▼               ▼                  ▼
 SourcingAgent   ApplicationAgent   MonitoringAgent   Dashboard         EventBus
 (Phase 2)       (Phase 3)          (Phase 4)         (FastAPI/SSE)     (in-process)
       │                 │               │
       ▼                 ▼               ▼
  GoogleSearch       PortalNavigator   ImapClient
  Extractor          FieldResolver     EmailClassifier
  RelaxationEngine   CoverLetter       ↓
       │                 │            (Brain)
       ▼                 ▼
   Brain (Gemini 3.1)  ←─── FormAnalyzer · GeoReasoner · JobClassifier · CoverLetter
       │                 │
       ▼                 ▼
   GoogleSearch       Actor (PlaywrightActor / OpenClawActor)
                          │
                          ▼
                       Browser (headed, sichtbar)
```

## Datenfluss in einem Batch

1. **Orchestrator** ruft `SourcingAgent.run()` auf bis 50 Firmen `QUALIFIED` sind.
2. Für jeden der 50 Batches ruft er `ApplicationAgent.process_batch(batch_size=N)`.
3. Pro Firma:
   - `PortalNavigator` öffnet die Karriereseite, ermittelt Job-Leads
     (strikte Suche → Filter-Fallback → visuelles Scrollen).
   - Pro Lead: Snapshot → Gemini `FormAnalyzer` → `FieldResolver` →
     Felder befüllen → CV hochladen → Cover-Letter generieren → submit.
   - Status pro Bewerbung wird in `applications.status` geschrieben,
     jedes Ereignis in `application_events` geloggt.
4. **MonitoringAgent** pollt parallel alle 2h den Posteingang, klassifiziert
   neue Mails (Regex + Gemini) und aktualisiert die Bewerbungs-Status.
5. **Dashboard** zeigt das Ganze live (SSE) und gibt einen Snapshot auf
   `/api/state` aus.

## Concurrency-Modell

- **Eine** Event-Loop für alles (asyncio).
- **Sourcing + Application** laufen sequentiell — beide brauchen exklusiven
  Zugriff auf den Browser.
- **Dashboard** und **MonitoringAgent** laufen als Hintergrund-Tasks
  (`asyncio.create_task`).
- **GeminiClient** hat einen globalen Token-Bucket-Limiter, damit API-Quoten
  nicht überschritten werden.
- **GoogleSearch** hat einen eigenen, strengeren Rate-Limiter (0.4 RPS), weil
  Google bei Scraping schnell blockiert.

## Idempotenz und Crash-Recovery

- `Repository.upsert_company` und `upsert_job` sind reine Idempotenz-Funktionen.
- `Repository.email_already_processed` verhindert Doppel-Updates aus IMAP.
- Status-Übergänge sind explizit und werden im `application_events`-Log
  archiviert — bei einem Neustart sieht der Agent die zuletzt geschriebenen
  Status und macht direkt da weiter.
- Jede Firma läuft in eigener Try/Except-Klammer; eine kaputte Karriereseite
  setzt nur diese Firma auf `ERROR`, der Rest läuft weiter.

## Erweiterbarkeit

- **Neuer Actor-Backend** (z.B. Selenium): `ActorInterface` implementieren,
  Factory ergänzen.
- **Neues Portal mit Sonderlogik** (z.B. SAP SuccessFactors): eigene
  `PortalAdapter`-Klasse einführen, vom `ApplicationAgent` per Strategy-Muster
  ausgewählt.
- **Neuer Brain-Anbieter** (z.B. lokales LLM): `GeminiClient`-Interface
  duplizieren oder das bestehende per Adapter ersetzen.
