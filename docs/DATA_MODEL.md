# Datenmodell

SQLite-Schema, lebt in `data/applier.sqlite3`. Erstellt durch
`python -m scripts.init_db`. Migrations werden bei diesem Projekt nicht
verwendet — `Base.metadata.create_all` reicht für ein lokales Tool, und das
Schema ändert sich selten. Wer mehrere Versionen unterstützen will, fügt
Alembic hinzu.

## Tabellen

### `companies`

Die Hauptliste — entspricht dem Konzept der Top-50.

| Spalte                | Typ            | Hinweis |
|-----------------------|----------------|--------|
| id                    | int PK         |        |
| name                  | str unique     | Quell-Identität |
| domain                | str?           | apex domain |
| careers_url           | str?           | Einstiegspunkt für Phase 3 |
| location_city         | str?           |        |
| location_country      | str?           |        |
| industry              | str?           |        |
| size                  | enum           | startup/sme/large/enterprise/unknown |
| status                | enum           | discovered → qualified → in_progress → applied / schon_fuer_alles_beworben / skipped / error |
| sourcing_query        | str?           | welche Query hat sie gefunden |
| sourcing_stage        | str?           | PRIMARY / DE / EU / GLOBAL |
| notes                 | text?          | freie Notizen, append-only |
| discovered_at         | datetime       |        |
| last_processed_at     | datetime?      |        |

### `jobs`

Stellen einer Firma. Eindeutig per `(company_id, title, url)`.

### `applications`

Eine pro Job. Enthält Status, Cover-Letter-Text und Submit-Bestätigungs-ID
des Portals.

Statusübergänge (zulässige Pfade):

```
new → applied → confirmed → interview_invitation → offer
                       ↘                         ↘
                        rejected                  rejected
new → failed
```

### `application_events`

Append-only Audit-Log. Speist sowohl das Dashboard als auch die forensische
Analyse nach Crashes.

### `email_logs`

Idempotenz-Tabelle: jede verarbeitete IMAP-Message-UID landet hier mit
ihrem Klassifikations-Ergebnis und einer Verlinkung auf
`matched_application_id`.

## Beispiel-Dictionary

Aus dem User-Spec:

```json
{
  "Unternehmen": "Beispiel GmbH",
  "Jobs": [{"Titel": "Werkstudent Mechatronik", "Status": "Applied"}]
}
```

… entspricht in unserem Modell:

```sql
SELECT c.name AS Unternehmen,
       j.title AS Titel,
       a.status AS Status
FROM companies c
JOIN jobs j ON j.company_id = c.id
JOIN applications a ON a.job_id = j.id;
```

## Aggregat-Counters

Das Dashboard berechnet:

- `counts.total` — alle Firmen
- `counts.applied` — `status = applied_to_something`
- `counts.exhausted` — `status = schon_fuer_alles_beworben`
- `applications.{applied,confirmed,rejected,interview_invitation,offer,failed}`
