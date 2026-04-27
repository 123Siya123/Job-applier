# OpenClaw-Integration

Der gesamte Code interagiert mit dem Browser/OS **ausschließlich** über die
abstrakte Klasse `ActorInterface` (`src/actor/actor_interface.py`). Es gibt
zwei Implementierungen:

| Backend       | Datei                              | Zweck                                       |
|---------------|------------------------------------|---------------------------------------------|
| `playwright`  | `src/actor/playwright_actor.py`    | Direkte Browser-Steuerung (default)         |
| `openclaw`    | `src/actor/openclaw_actor.py`      | OS-Mausbewegung, Datei-Explorer, sichtbar   |

## Warum diese Trennung?

Damit OpenClaw **später** mit einem einzigen Setting-Wechsel produktiv geht —
ohne Änderungen an Sourcing-, Application- oder Monitoring-Agent. Der
ApplicationAgent ruft z.B. `actor.upload_file(spec)` auf — er weiß nicht, ob
das per Playwright-`set_input_files` läuft oder ob OpenClaw den Windows-
Datei-Dialog öffnet und die Pfad-Zeichenkette tippt.

## Schritt-für-Schritt

### 1. OpenClaw-Daemon starten

OpenClaw exponiert auf der lokalen Maschine einen JSON-RPC-Endpunkt
(Default: `127.0.0.1:4711`). Pfad und Start-Skript hängen von Ihrer
OpenClaw-Installation ab — siehe deren Docs.

### 2. `.env` umstellen

```dotenv
ACTOR_BACKEND=openclaw
OPENCLAW_HOST=127.0.0.1
OPENCLAW_PORT=4711
OPENCLAW_TIMEOUT_SECONDS=60
```

Das war's. `python -m src.main` benutzt jetzt OpenClaw.

### 3. RPC-Methoden vervollständigen

Falls Ihre OpenClaw-Distribution nicht das im Stub angenommene JSON-RPC-
Schema fährt: in `src/actor/openclaw_actor.py` sind genau zwei Stellen
anzupassen:

- `_rpc(method, params)` → Transport (HTTP/JSON-RPC, gRPC, Named Pipe, …)
- Methodennamen (`browser.goto`, `input.click`, `input.upload_file`, …) →
  was OpenClaw real exponiert.

Die öffentliche API von `OpenClawActor` (alle Methoden aus `ActorInterface`)
**bleibt unverändert**.

### 4. OS-Datei-Dialog

Der ApplicationAgent ruft beim Hochladen:

```python
await actor.upload_file(UploadFileSpec(target=upload_btn, file_path=cv))
```

In Playwright wird daraus ein `set_input_files()`-Aufruf — komplett ohne
echten Maus-Click. In OpenClaw soll daraus:

1. Klick auf den Upload-Button (`input.click`) → öffnet Windows-Dialog.
2. Senden des absoluten Pfades an die Routine `input.upload_file`. OpenClaw
   intern (z.B. via `pywinauto`):
   - wartet auf das Fenster mit Titel "Öffnen" / "Datei zum Hochladen…",
   - tippt den Pfad in das `Edit`-Feld,
   - drückt Enter.

Diese Logik liegt **außerhalb** dieses Repos. Der Stub in
`openclaw_actor.upload_file` schickt nur den Pfad weiter.

### 5. Was bleibt im Repo?

Falls Sie OS-Dialog-Code lieber hier behalten wollen, gibt es zwei Optionen:

- **Hybrid**: `OpenClawActor` ruft selbst `pywinauto` für das Datei-Dialog-
  Handling auf. Nur diese eine Methode wird hier komplettiert.
- **Pur**: alles bleibt drüben in OpenClaw. Diese Repo bleibt schlank.

Der Stub im Repo ist für die *pure* Variante geschrieben. Der Hybrid-Pfad
würde innerhalb von `OpenClawActor.upload_file()` etwa so aussehen:

```python
from pywinauto.application import Application
import asyncio

async def upload_file(self, spec):
    # 1) Klick öffnet OS-Dialog
    await self.click(spec.target)

    # 2) Wir warten kurz und treiben den Dialog selbst
    def _drive_dialog(path: str) -> None:
        app = Application(backend="uia").connect(title_re="Öffnen|Open|Datei.*hochladen", timeout=10)
        dlg = app.top_window()
        dlg.child_window(auto_id="1148", control_type="Edit").set_text(path)
        dlg.child_window(auto_id="1", control_type="Button").click()

    await asyncio.to_thread(_drive_dialog, str(spec.file_path))
```

## Verträge / Garantien für OpenClaw

`ActorInterface` macht folgende Zusicherungen, die jeder Agent vorausgesetzt:

- `goto(url)` → Browser zeigt am Ende exakt diese URL (oder wirft `ActorError`).
- `snapshot()` → liefert zum Aufrufzeitpunkt sichtbares PNG + Text + (opt.) DOM.
- `click(target)` → bewegt sichtbar zum Target und klickt.
- `type_text(target, "x")` → fokussiert das Feld, leert es (default), tippt "x".
- `upload_file(spec)` → Datei aus `spec.file_path` ist im Formular angehängt.
- Methoden sind **idempotent unter Wiederholung nicht**: ein doppelter Klick
  klickt zweimal. Retry-Logik liegt beim Aufrufer (Brain entscheidet,
  Repository schreibt erst nach Erfolg).

## Test-Setup

Für End-to-End-Smoke-Tests mit Playwright reicht es:

```bash
ACTOR_BACKEND=playwright BROWSER_HEADED=true python -m src.main
```

Für OpenClaw:

```bash
# erst OpenClaw starten, danach:
ACTOR_BACKEND=openclaw python -m src.main
```

Beim Wechsel ändert sich **kein einziger Agent-Code**.
