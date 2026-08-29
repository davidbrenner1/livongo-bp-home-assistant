# Livongo BP Collector documentation

## First-time setup

1. On a Mac or other desktop computer, run the included `livongo_session_export.py` helper.
2. Log into Livongo normally and navigate to Blood Pressure -> All Logs.
3. Return to the terminal and press Enter.
4. The helper creates `livongo-session-bundle.json`.
5. In Home Assistant, open this add-on and choose **Open Web UI**.
6. Upload `livongo-session-bundle.json`.
7. Click **Sync now**.

The session bundle contains authenticated browser state. Treat it like a password. Do not email it or store it in a public location.

## Home Assistant entities

The add-on maintains these state entities:

- `sensor.livongo_bp_systolic`
- `sensor.livongo_bp_diastolic`
- `sensor.livongo_bp_heart_rate`
- `sensor.livongo_bp_last_reading`
- `sensor.livongo_bp_sync_status`

## Home Assistant events

After the first historical import, each newly discovered valid reading fires:

`livongo_bp_reading`

Example event data:

```yaml
livongo_id: 90449240
client_record_id: livongo:90449240
reading_time: "2026-08-29T07:11:40.000-05:00"
systolic: 109
diastolic: 79
heart_rate: 77
mean_arterial: 89
triple_mode: true
source: livongo
```

The first import does not emit historical events unless `emit_initial_events` is enabled in the add-on configuration. You can replay stored readings from the add-on web UI later.

## Authentication expiration

If Livongo expires the browser session, the add-on status changes to `auth_required`. Run the desktop login helper again and upload the new bundle.
