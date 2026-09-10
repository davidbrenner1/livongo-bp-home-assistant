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

LOGIN_URL = "https://member.teladoc.com/signin"
OUTPUT = Path("livongo-session-bundle.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Teladoc Health / Livongo authenticated browser session bundle")
    parser.add_argument(
        "--login-url",
        "-l",
        default="https://member.teladoc.com/signin",
        help="Initial login URL to open in browser (defaults to https://member.teladoc.com/signin)",
    )
    parser.add_argument(
        "--upload-url",
        "-u",
        default="http://192.168.50.116:8099/upload",
        help="Optional URL to automatically POST the session bundle to (defaults to http://192.168.50.116:8099/upload)",
    )
    parser.add_argument(
        "--user-data-dir",
        default=str(Path(__file__).parent / ".chrome_profile"),
        help="Path to persistent browser profile directory (defaults to .chrome_profile)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headlessly using saved persistent browser profile",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    user_data_dir = Path(args.user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    if not args.headless:
        print(
            "Teladoc Health / Livongo Session Exporter\n\n"
            f"1. A Chromium browser window will open to {args.login_url}.\n"
            "2. Log into your Teladoc Health account (complete 2FA if prompted).\n"
            "3. Navigate to your Blood Pressure readings / history page.\n"
            "4. Return to this Terminal window and press ENTER.\n\n"
            "The generated JSON contains authenticated browser state. Treat it like a password.\n"
        )
    else:
        print(f"Running headless session export with persistent profile at {user_data_dir}...")

    captured_apis: list[str] = []

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                channel="chrome",
                headless=args.headless,
                permissions=["clipboard-read", "clipboard-write"],
            )
        except Exception:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=args.headless,
                permissions=["clipboard-read", "clipboard-write"],
            )

        # Force unblock paste events and handle React inputs
        context.add_init_script(
            """
            (() => {
                const unblock = (e) => e.stopImmediatePropagation();
                window.addEventListener('paste', unblock, true);
                document.addEventListener('paste', unblock, true);
                window.addEventListener('copy', unblock, true);
                window.addEventListener('cut', unblock, true);

                document.addEventListener('keydown', async (e) => {
                    if ((e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V')) {
                        const target = document.activeElement;
                        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
                            try {
                                const text = await navigator.clipboard.readText();
                                if (text) {
                                    const proto = target.tagName === 'INPUT' ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
                                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                                    if (setter) {
                                        setter.call(target, text);
                                    } else {
                                        target.value = text;
                                    }
                                    target.dispatchEvent(new Event('input', { bubbles: true }));
                                    target.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            } catch (err) {}
                        }
                    }
                }, true);

                setInterval(() => {
                    document.querySelectorAll('input, textarea').forEach(el => {
                        el.onpaste = null;
                        el.removeAttribute('onpaste');
                    });
                }, 500);
            })();
            """
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_request(req: Any) -> None:
            url = req.url
            if any(k in url.lower() for k in ("reading", "bp", "blood_pressure", "graphql", "/api/", "livongo", "teladoc")):
                auth = req.headers.get("authorization")
                if auth or "reading" in url.lower() or "bp" in url.lower():
                    print(f"Captured API request: {req.method} {url[:100]}")
                    if url not in captured_apis:
                        captured_apis.append(url)

        page.on("request", on_request)

        if not args.headless:
            page.goto(args.login_url, wait_until="domcontentloaded", timeout=120_000)
            input(">>> After logging in and viewing your BP readings, press ENTER here...\n")
            page.wait_for_timeout(1000)
        else:
            target_url = "https://my.teladoc.com/condition-management/blood-pressure/all-logs"
            print(f"Navigating to {target_url}...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            # Wait up to 20s for BP requests to complete
            deadline = time.monotonic() + 20
            while not any("reading/bp" in u for u in captured_apis) and time.monotonic() < deadline:
                page.wait_for_timeout(500)

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

    print(f"\nSaved: {OUTPUT.resolve()}")

    uploaded = False
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
                uploaded = True
        except urllib.error.HTTPError as e:
            print(f"✗ Upload failed: HTTP {e.code} - {e.read().decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"✗ HTTP Upload failed ({e}); attempting SSH transfer to Home Assistant...")
            try:
                import subprocess
                ha_host = "192.168.50.116"
                ssh_user = "root"
                remote_tmp = "/tmp/livongo-session-bundle.json"
                subprocess.run(
                    ["scp", "-o", "ConnectTimeout=5", str(OUTPUT.resolve()), f"{ssh_user}@{ha_host}:{remote_tmp}"],
                    check=True,
                    capture_output=True,
                )
                cmd = (
                    f"CID=$(docker ps -q --filter name=livongo | head -n1); "
                    f"if [ -n \"$CID\" ]; then "
                    f"docker cp {remote_tmp} $CID:/data/livongo-session-bundle.json && "
                    f"docker exec $CID chmod 600 /data/livongo-session-bundle.json && "
                    f"docker exec $CID python3 -c 'import urllib.request; req=urllib.request.Request(\"http://127.0.0.1:8099/sync\", method=\"POST\"); urllib.request.urlopen(req)'; "
                    f"fi; rm -f {remote_tmp}"
                )
                subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5", f"{ssh_user}@{ha_host}", cmd],
                    check=True,
                    capture_output=True,
                )
                print("✓ Successfully transferred session bundle and triggered sync via SSH!")
                uploaded = True
            except Exception as ssh_err:
                print(f"✗ SSH transfer fallback failed: {ssh_err}")

    if not uploaded:
        print("Upload this file through the Livongo BP Collector web UI in Home Assistant.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
