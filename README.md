# Drone Dash

A live map of drones detected by your sensor node(s) in Waitsburg and Dayton, WA.

*Drone Dash* is a working name. See [Renaming](#renaming): the name lives in one setting.

- **server.py** receives detections over HTTP, sorts them into four classes (identified, Remote ID, DJI DroneID, unknown), stores 90 days in SQLite, and pushes live updates to the page. It can also send phone alerts through ntfy.
- **index.html** is the dashboard: a map with trails, pilot points and node coverage rings, plus a now-flying list, details, node health and the last 24 hours.
- **simulator.py** generates fake drones, so you can see everything working before the hardware arrives.
- **install.sh** installs it as a service on the Pi. **rename.sh** moves an installed copy to a new name.

## 1. Try it on any computer (5 minutes)

```bash
pip install -r requirements.txt
uvicorn server:app --port 8080          # terminal 1
python3 simulator.py                    # terminal 2 (runs 5 minutes)
```

Open http://localhost:8080. An identified DJI drone circles Waitsburg, a DJI DroneID drone drifts nearby, and a MAC-only Remote ID drone flies at Dayton for 30 s. After about 95 s, an "unknown drone" ring starts pulsing at Dayton.

To run the automated checks (GitHub runs these on every push): `python3 tests/smoke_test.py`.

## 2. Install on the Pi

```bash
sudo ./install.sh
sudo nano /etc/default/drone-dash        # name, token, ntfy topic, node positions
sudo systemctl restart drone-dash
```

The dashboard is then at `http://<pi-address>:8080` on your home network. To reach it from anywhere, install Tailscale on the Pi and on your phone. Don't open the port on your router: the page has no login.

Re-running `install.sh` updates the code and keeps your settings and data.

## 3. Connect the test node (Mesh-Mapper)

In Mesh-Mapper, set its webhook URL to:

```
http://localhost:8080/api/ingest?node=waitsburg-1&token=YOUR_TOKEN
```

Set this in Mesh-Mapper's web page (port 5000) or with its `/api/set_webhook_url` endpoint. Use `dayton-1` instead of `waitsburg-1` if the node is in Dayton.

The server accepts many common field names (`uas_id`/`basic_id`, `lat`/`drone_lat`, `lon`/`drone_long`, `pilot_lat`, `rssi`, `mac`…). Drones are matched by Remote ID serial when there is one, otherwise by MAC address.

**First real flight:** run `sqlite3 /opt/drone-dash/detections.sqlite "select raw from detections order by id desc limit 1"`. If a field you expect (such as altitude) shows in `raw` but not on the map, add its name to `ALIASES` in server.py.

## Renaming

The product name is a setting, not code. Nothing in the API, the database or the message format depends on it.

| What changes | How |
| --- | --- |
| Name on screen and in phone alerts | Edit `APP_NAME` in `/etc/default/drone-dash`, then `sudo systemctl restart drone-dash`. Takes effect immediately; no reinstall. |
| Folder, settings file and service name (optional) | `sudo ./rename.sh drone-dash new-slug "New Name"`. Moves `/opt`, `/etc/default` and the service, keeping data and settings. |
| Running locally without the Pi service | `APP_NAME="New Name" uvicorn server:app --port 8080` |

Other parts are deliberately neutral so they never need renaming: setting names (`APP_…`), the database (`detections.sqlite`), the API paths (`/api/…`) and the node names.

## Settings (`/etc/default/drone-dash`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | Drone Dash | Display name |
| `APP_TOKEN` | (empty) | Shared secret senders must include; empty = no check |
| `APP_NTFY_URL` | (empty) | e.g. `https://ntfy.sh/a-long-random-topic` for phone alerts |
| `APP_NODES` | Waitsburg + Dayton | JSON of node name → [lat, lon] |
| `APP_LIVE_SECS` | 120 | How long a drone stays on the "now flying" list after its last message |
| `APP_RETENTION_DAYS` | 90 | Older detections are deleted |
| `APP_DB` | detections.sqlite | Database path |

## Sending other data

POST JSON (one object or a list) to `/api/ingest?node=<name>`:

| Field | Meaning |
| --- | --- |
| `uas_id`, `id_type` | Remote ID serial or session ID (`serial`, `session`, …) |
| `source` | `wifi-beacon`, `wifi-nan`, `ble4`, `ble5lr`, `dji`, or `rf` (no-ID radio activity) |
| `lat`, `lon`, `alt_m`, `speed_mps`, `heading` | Drone position and motion |
| `pilot_lat`, `pilot_lon` | Pilot or takeoff point |
| `rssi`, `band` | Signal strength; band for `rf` alerts (e.g. `5.8GHz-FPV`) |
| `{"type":"status"}` | Node heartbeat (marks the node online) |

## Classification rules

1. **Identified**: an ANSI/CTA-2063-A serial whose 4-character maker code is in `MFR_CODES` (DJI 1581, Autel 1748, …). Add codes from the FAA Declaration of Compliance list at https://uasdoc.faa.gov/listDocs.
2. **Remote ID**: a valid broadcast with a session ID, a MAC address only, or an unlisted maker code.
3. **DJI DroneID**: messages with `source: dji` (from an SDR, later).
4. **Unknown drone**: `source: rf` hits, shown once there are at least 3 in 90 s and no ID'd drone at that node within ±60 s.

A track is flagged **possible spoof** if its position jumps faster than 60 m/s.
