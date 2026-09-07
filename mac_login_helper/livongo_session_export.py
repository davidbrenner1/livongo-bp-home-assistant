#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://my.livongo.com/login"
OUTPUT = Path("livongo-session-bundle.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Livongo authenticated browser session bundle")
    parser.add_argument(
        "--upload-url",
        "-u",
        default="http://192.168.50.116:8099/upload",
        help="Optional URL to automatically POST the session bundle to (defaults to http://192.168.50.116:8099/upload)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(
        "Livongo session exporter\n\n"
        "1. A Chromium window will open.\n"
        "2. Log into Livongo normally.\n"
        "3. Navigate to Blood Pressure -> All Logs.\n"
        "4. Wait for your readings to appear.\n"
        "5. Return to this Terminal window and press Enter.\n\n"
        "The generated JSON contains authenticated browser state. Treat it like a password.\n"
    )

    captured_apis: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        def on_request(req: Any) -> None:
            url = req.url
            if any(k in url.lower() for k in ("reading", "bp", "blood_pressure", "graphql", "/api/", "livongo", "teladoc")):
                auth = req.headers.get("authorization")
                if auth or "reading" in url.lower() or "bp" in url.lower():
                    print(f"Captured API request: {req.method} {url[:100]}")
                    if url not in captured_apis:
                        captured_apis.append(url)

        page.on("request", on_request)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)

        input(">>> After logging in, clicking 'Go to Teladoc Health', and viewing your BP readings, press ENTER here...\n")
        page.wait_for_timeout(1000)

        try:
            storage_state = context.storage_state(indexed_db=True)
        except TypeError:
            storage_state = context.storage_state()

        try:
            session_storage = page.evaluate(
                "Object.fromEntries(Array.from({length: sessionStorage.length}, "
                "(_, i) => { const k = sessionStorage.key(i); return [k, sessionStorage.getItem(k)]; }))"
            )
        except Exception:
            session_storage = {}

        bundle = {
            "format": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "final_url": page.url,
            "captured_apis": captured_apis,
            "storage_state": storage_state,
            "session_storage": {page.url.split("/", 3)[0] + "//" + page.url.split("/", 3)[2]: session_storage} if page.url.startswith("http") else {},
        }

        data_bytes = json.dumps(bundle, indent=2).encode("utf-8")
        OUTPUT.write_bytes(data_bytes)
        try:
            os.chmod(OUTPUT, 0o600)
        except OSError:
            pass

        context.close()
        browser.close()

    print(f"\nSaved: {OUTPUT.resolve()}")

    if args.upload_url:
        print(f"Uploading session bundle to: {args.upload_url}...")
        req = urllib.request.Request(
            args.upload_url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"✓ Upload succeeded! Status code: {resp.status}")
        except urllib.error.HTTPError as e:
            print(f"✗ Upload failed: HTTP {e.code} - {e.read().decode('utf-8', errors='ignore')}")
            return 1
        except Exception as e:
            print(f"✗ Upload failed: {e}")
            return 1
    else:
        print("Upload this file through the Livongo BP Collector web UI in Home Assistant, or pass --upload-url.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
