# `data/`

Runtime-Verzeichnis. Wird von der Software befüllt:

- `applier.sqlite3` — die SQLite-Datenbank (siehe `docs/DATA_MODEL.md`)
- `applier.log` — strukturierte Logs (JSON-Lines)
- `screenshots/` — optional (von `FormAnalyzer` für Debug)
- `uploads/` — optional (vom Cover-Letter-Generator für Anhang-Versionen)

Alles in diesem Ordner ist via `.gitignore` aus dem Repo ausgeschlossen.
