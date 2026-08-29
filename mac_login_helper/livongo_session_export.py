#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://my.livongo.com/login"
OUTPUT = Path("livongo-session-bundle.json")


def main() -> int:
    print(
        "Livongo session exporter\n\n"
        "1. A Chromium window will open.\n"
        "2. Log into Livongo normally.\n"
        "3. Navigate to Blood Pressure -> All Logs.\n"
        "4. Wait for your readings to appear.\n"
        "5. Return to this Terminal window and press Enter.\n\n"
        "The generated JSON contains authenticated browser state. Treat it like a password.\n"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)

        input("After All Logs is loaded, press ENTER here...\n")
        page.wait_for_timeout(1000)

        try:
            storage_state = context.storage_state(indexed_db=True)
        except TypeError:
            storage_state = context.storage_state()

        session_storage = page.evaluate(
            "Object.fromEntries(Array.from({length: sessionStorage.length}, "
            "(_, i) => { const k = sessionStorage.key(i); return [k, sessionStorage.getItem(k)]; }))"
        )

        bundle = {
            "format": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "storage_state": storage_state,
            "session_storage": {page.url.split("/", 3)[0] + "//" + page.url.split("/", 3)[2]: session_storage},
        }

        OUTPUT.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        try:
            os.chmod(OUTPUT, 0o600)
        except OSError:
            pass

        context.close()
        browser.close()

    print(f"\nSaved: {OUTPUT.resolve()}")
    print("Upload this file only through the Livongo BP Collector web UI in Home Assistant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
