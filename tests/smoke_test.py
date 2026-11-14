"""End-to-end smoke test: start the server, send sample detections, check the results.

Run from the repo root:  python3 tests/smoke_test.py
Uses only the standard library plus the app's own requirements.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PORT = int(os.environ.get("TEST_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode()
        return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def main():
    db = os.path.join(tempfile.mkdtemp(), "test.sqlite")
    env = dict(os.environ, APP_DB=db, APP_NAME="Test Dash")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(PORT)],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                call("/api/state")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        else:
            raise SystemExit("server did not start:\n" + srv.stdout.read().decode())

        page = call("/")
        check("<title>Test Dash</title>" in page, "page title uses APP_NAME")

        now = time.time()
        call("/api/ingest?node=waitsburg-1", [
            {"source": "wifi-beacon", "uas_id": "1581F5FJD239C00DW22E", "id_type": "serial",
             "lat": 46.27, "lon": -118.15, "alt_m": 80},
            {"transport": "ble5lr", "mac": "60:60:1f:aa:bb:cc", "drone_lat": 46.28, "drone_long": -118.16},
            {"source": "dji", "uas_id": "3NZCH7K0021Q8T", "lat": 46.275, "lon": -118.155},
        ])
        # RF with no ID at Dayton: needs 3 hits before it counts as an unknown drone
        for i in range(3):
            call("/api/ingest?node=dayton-1", {"source": "rf", "band": "5.8GHz", "ts": now + i})
        # A position jump of ~14 km in 1 s should be flagged as a possible spoof
        call("/api/ingest?node=waitsburg-1", {"uas_id": "1748C1234567890ABC", "id_type": "serial",
                                              "lat": 46.27, "lon": -118.15, "ts": now})
        call("/api/ingest?node=waitsburg-1", {"uas_id": "1748C1234567890ABC", "id_type": "serial",
                                              "lat": 46.40, "lon": -118.15, "ts": now + 1})

        state = call("/api/state")
        by_id = {t["track_id"]: t for t in state["tracks"]}
        check(state["app_name"] == "Test Dash", "API reports app name")
        check(by_id["1581F5FJD239C00DW22E"]["cls"] == "identified"
              and by_id["1581F5FJD239C00DW22E"]["manufacturer"] == "DJI", "DJI serial -> identified (DJI)")
        check(by_id["MAC:60:60:1F:AA:BB:CC"]["cls"] == "rid", "MAC-only broadcast -> Remote ID")
        check(by_id["3NZCH7K0021Q8T"]["cls"] == "dji", "DJI DroneID source -> dji")
        check(by_id["RF:dayton-1:5.8GHz"]["cls"] == "unknown", "3 RF hits with no ID -> unknown drone")
        check(by_id["1748C1234567890ABC"]["spoof"] == 1
              and by_id["1748C1234567890ABC"]["manufacturer"] == "Autel Robotics", "impossible jump -> spoof flag")
        check(len(call("/api/history?hours=1")) == 5, "history lists all 5 tracks")
        print("All checks passed.")
    finally:
        srv.terminate()
        srv.wait(timeout=10)


if __name__ == "__main__":
    main()
