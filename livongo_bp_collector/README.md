# Livongo BP Collector

A local Home Assistant add-on that retrieves blood pressure readings from the Livongo member portal's JSON API using an authenticated browser session.

The add-on:

- keeps Livongo credentials out of Home Assistant configuration
- stores the uploaded browser session only in the add-on's persistent `/data` volume
- retrieves readings from Livongo's `/reading/bp` JSON API
- stores readings locally in SQLite
- creates Home Assistant sensor states for the latest reading and sync status
- fires a `livongo_bp_reading` Home Assistant event for each newly discovered reading after the initial import
- provides a Home Assistant ingress page to upload a session, sync now, and replay stored readings

See `DOCS.md` for setup instructions.
