"""Pre-flight check — your real trockenlauf.

Run this BEFORE `python -m src.main` to verify that:
    1. .env loads and contains at least one Gemini API key
    2. config/profile.json validates and the CV file exists
    3. config/search_params.json validates
    4. The SQLite DB can be opened/created
    5. The IMAP credentials can authenticate
    6. The Gemini API answers a tiny ping

If any step fails, the system would also fail at runtime — but here you
get a clear, fast diagnostic instead of a 2-minute startup followed by
a stack trace deep in the application loop.

Usage
-----
    python -m scripts.verify_setup
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.brain.gemini_client import GeminiClient
from src.monitoring.imap_client import ImapClient
from src.persistence.db import init_db
from src.settings import get_settings
from src.utils.logger import configure_logging, get_logger

_log = get_logger("verify_setup")

OK = "  [OK]   "
FAIL = "  [FAIL] "
WARN = "  [WARN] "


async def _amain() -> int:
    configure_logging()
    print("=" * 70)
    print("  Job-Applier — Pre-Flight Check")
    print("=" * 70)
    failures = 0
    warnings = 0

    # 1) settings + keys
    try:
        s = get_settings()
        keys = s.gemini_key_list()
        if not keys:
            print(f"{FAIL}No Gemini API keys configured (set GEMINI_API_KEY or GEMINI_API_KEYS)")
            failures += 1
        else:
            redacted = [f"{k[:6]}…{k[-4:]}" for k in keys]
            print(f"{OK}Found {len(keys)} Gemini API key(s): {', '.join(redacted)}")
    except Exception as exc:
        print(f"{FAIL}Settings load failed: {exc}")
        return 1

    # 2) profile
    try:
        profile = s.load_profile()
        cv = Path(profile.files.cv_path)
        if cv.exists():
            print(f"{OK}Profile loaded — CV at {cv}")
        else:
            print(f"{FAIL}CV path not found: {cv}")
            failures += 1
    except Exception as exc:
        print(f"{FAIL}Profile JSON invalid: {exc}")
        failures += 1

    # 3) search params
    try:
        params = s.load_search_params()
        print(
            f"{OK}Search params: target={params.target_company_count}, "
            f"city={params.primary_location.city}, "
            f"job_types={', '.join(params.job_types)}"
        )
    except Exception as exc:
        print(f"{FAIL}search_params.json invalid: {exc}")
        failures += 1

    # 4) DB
    try:
        await init_db()
        print(f"{OK}SQLite DB ready at {s.database_url}")
    except Exception as exc:
        print(f"{FAIL}DB init failed: {exc}")
        failures += 1

    # 5) IMAP
    if s.email_imap_user and s.email_imap_password:
        try:
            imap = ImapClient()
            messages = await imap.fetch_recent(since_hours=24, limit=5)
            print(f"{OK}IMAP login OK — {len(messages)} recent messages visible")
        except Exception as exc:
            print(f"{FAIL}IMAP login failed: {exc}")
            failures += 1
    else:
        print(f"{WARN}IMAP credentials missing — monitoring will be skipped")
        warnings += 1

    # 6) Gemini ping (tiny — won't burn quota)
    if keys:
        try:
            gemini = GeminiClient()
            answer = await gemini.generate_text(
                "Reply with exactly the word: pong", temperature=0.0, max_output_tokens=10
            )
            print(f"{OK}Gemini reachable — answered: {answer.strip()[:30]!r}")
        except Exception as exc:
            print(f"{FAIL}Gemini ping failed: {exc}")
            failures += 1

    print("=" * 70)
    if failures:
        print(f"  {failures} failure(s), {warnings} warning(s) — fix before running main")
        return 2
    print(f"  All checks passed ({warnings} warning(s)) — ready to run `python -m src.main`")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
