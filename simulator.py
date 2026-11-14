"""Fake drones for testing Drone Dash without hardware.

Usage: python3 simulator.py [http://localhost:8080] [seconds]
Sends one message per drone per second:
  - an identified DJI (Remote ID serial 1581F...) circling Waitsburg
  - a Remote ID drone with only a MAC/session ID flying a line near Dayton
  - a DJI DroneID detection of a different drone
  - drone-like RF activity at Dayton with no ID (becomes an "unknown drone" after 3 hits)
  - node heartbeats
"""
import json
import math
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 300
WB = (46.2704, -118.1530)
DY = (46.3207, -117.9719)


def post(items, node):
    req = urllib.request.Request(f"{BASE}/api/ingest?node={node}", data=json.dumps(items).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def offset(base, north_m, east_m):
    return (base[0] + north_m / 111320, base[1] + east_m / (111320 * math.cos(math.radians(base[0]))))


start = time.time()
while time.time() - start < DURATION:
    t = time.time() - start
    a = t / 40                                    # circle period ~4 minutes
    lat, lon = offset(WB, 500 * math.sin(a), 500 * math.cos(a))
    plat, plon = offset(WB, -120, 200)
    post([
        {"type": "status", "node": "waitsburg-1", "cpu_temp": 52},
        {"source": "wifi-beacon", "uas_id": "1581F5FJD239C00DW22E", "id_type": "serial",
         "lat": lat, "lon": lon, "alt_m": 85, "speed_mps": 13.1, "heading": (math.degrees(a) + 90) % 360,
         "pilot_lat": plat, "pilot_lon": plon, "rssi": -68},
        {"source": "dji", "uas_id": "3NZCH7K0021Q8T", "lat": offset(WB, 900 - t * 3, -600)[0],
         "lon": offset(WB, 900 - t * 3, -600)[1], "alt_m": 40, "speed_mps": 3,
         "pilot_lat": offset(WB, 950, -700)[0], "pilot_lon": offset(WB, 950, -700)[1], "rssi": -80},
    ], "waitsburg-1")

    blat, blon = offset(DY, -700 + t * 6 % 1400, 300)
    items = [{"type": "status", "node": "dayton-1", "cpu_temp": 48}]
    if t < 30:   # Mesh-Mapper-style field names, no serial: known only by MAC. Lands after 30 s.
        items.append({"transport": "ble5lr", "mac": "60:60:1f:aa:bb:cc", "drone_lat": blat, "drone_long": blon,
                      "drone_altitude": 60, "speed": 6, "pilot_lat": DY[0] - 0.006,
                      "pilot_long": DY[1] + 0.004, "rssi": -74})
    if t > 95:   # RF with no ID, >60 s after the last ID'd drone at Dayton -> "unknown drone"
        items.append({"source": "rf", "band": "5.8GHz-FPV", "freq": 5800, "rssi": -61})
    post(items, "dayton-1")
    time.sleep(1)
