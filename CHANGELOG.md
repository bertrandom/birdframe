# Changelog

## Unreleased

* Replaced Samsung The Frame delivery with the Electric Objects EO1 running
  the custom EO1 firmware. BirdFrame asks the frame to show
  `/api/v1/display/compositions/{revision}.jpg` through its HTTP API on port
  12345; the frame downloads the image and pauses its own slideshow. Setup's
  display step is now "EO1 & display", with the EO1 hostname, BirdFrame's
  address as seen from the EO1, orientation, update interval, quiet hours, and
  an auto-update toggle. The dashboard button is now "Send current art to EO1"
  (`POST /api/v1/eo1/push`).
* Compositions now default to 1080×1920 portrait to match the EO1 panel, with
  a 1920×1080 landscape option.
* The Avian Visitors original layout gathers birds in a tall oval on portrait
  canvases, mirroring its wide oval on landscape ones.
* Removed the Samsung client and `samsungtvws` dependency, TV discovery and
  pairing, Wake-on-LAN, TV MAC address, and matte settings. Existing `tv_*`
  settings are migrated on startup (update interval, auto-update, and quiet
  hours carry over; TV host, MAC, matte, wake, and Samsung token are deleted).
  Pushes are recorded in a new `display_pushes` table instead of `tv_uploads`.

* Norwegian bird names are bundled from the Norwegian Bird Names Committee's
  AviList workbook and can be refreshed with the localization importer.
* Added selectable illustration/sketch packs, paginated activity logs, and the
  stable current-display API.
* Wired the display policy settings: detections below the confidence
  threshold no longer change artwork, and per-species duplicate cooldown
  coalesces repeat sightings into a single render.
* Implemented automatic TV wake (Wake-on-LAN via the TV MAC address) and quiet
  hours for the TV sync worker; Settings gained a Display & TV section with
  display mode, collage window, update cadence, wake, and quiet-hour controls.
* Added retention cleanup: one composition (file and record) per day for the
  past year and a 30-day activity log, applied at startup and then daily.
* Fixed first-run routing: the health endpoint now reports whether the setup
  wizard has been completed, so a fresh install opens the wizard instead of
  landing on an empty dashboard.
* Added accounts and API keys: the first account created is the admin, logins
  issue per-account API keys, and the management API requires a valid key.
  Settings gained an API keys and users panel with key generation, revocation,
  and (admin-only) user management.
* Moved collage style and display/TV controls into the Setup guide as Artwork
  and Display & TV wizard steps; Settings now covers artwork generation, the
  display API, and asset packs.

## 0.1.2

* Hardened BirdNET-Go SSE monitoring with heartbeat-aware timeouts, automatic
  worker recovery, source runtime status, and stream-aware connection tests.
* Detection records and activity logs are preserved when artwork rendering
  fails, so a display problem cannot stop source ingestion.

## 0.1.1

* Improved BirdNET-Go streaming reliability, connection-test logs, detection
  exclusion diagnostics, and confidence-threshold controls.
* Added the Birdnet-GO or Birdframe Birdprovider label and setup connection test.
* Preloaded the artwork catalog URL and kept setup on the Assets step with
  feedback when artwork packs finish loading.

## 0.1.0

Initial hobby release: BirdWeather and BirdNET-Go detection adapters, 16:9
collages, Samsung Frame delivery, Docker Compose deployment, and web setup.
