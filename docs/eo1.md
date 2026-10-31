# Electric Objects EO1

BirdFrame sends its artwork to an Electric Objects EO1 picture frame running
the replacement [EO1 firmware](https://github.com/bertrandom/EO1). The stock
EO1 software cannot receive images from BirdFrame; follow the firmware's
[README](https://github.com/bertrandom/EO1#readme) to install it first.

The firmware runs a small HTTP API on port 12345 (see
[Controlling the EO1](https://github.com/bertrandom/EO1#controlling-the-eo1)).
BirdFrame only uses `POST /api/v1/show`; everything else about the frame —
playlists, brightness, its own quiet hours — stays under the firmware's
control.

## How pushing works

The EO1 pulls the image itself. To show new artwork, BirdFrame calls

```text
POST http://<eo1>:12345/api/v1/show
{"url": "<image url>", "type": "image"}
```

and the frame downloads that URL, shows it, and pauses its own playlist
slideshow while BirdFrame's artwork is on screen. The image URL is

```text
{birdframe_url}/api/v1/display/compositions/{revision}.jpg
```

Each composition revision has its own immutable URL, so the frame can cache it
safely. The URL is served by the [display API](display-api.md); if the display
API requires a token, BirdFrame appends `?token=...`.

A background worker checks every 30 seconds and pushes the newest composition
when it has changed, the update interval has elapsed, it is not quiet hours,
and automatic updates are on. A burst of detections therefore results in at
most one push per interval. Every push is recorded in the `display_pushes`
table.

### Visitor spotlight

With **Spotlight each detected bird** on, the Collage and Field journal
layouts briefly hand the frame to each qualifying detection, one that passes
the confidence threshold and the duplicate cooldown. BirdFrame renders the
bird on its own with "Bird detected" and its English detection name, and sends
it to the EO1 straight away, without waiting for the update interval. Each
new qualifying detection of a different species replaces the bird and
restarts the timer, which runs from the detection's time. A repeat of the bird
already on screen is ignored: the timer keeps running and nothing is sent. When the timer runs out, the collage is
re-rendered and sent straight away as well. Quiet hours still apply. The
spotlight is kept in memory, so after a restart BirdFrame goes back to the
collage. The Latest visitor layout ignores the setting.

To push by hand, use **Send current art to EO1** on the dashboard. It calls
`POST /api/v1/eo1/push`, which queues an `eo1_push` job and returns `202` with
the job ID. Manual pushes ignore quiet hours and the auto-update switch.

## Settings

All of these are in the Setup guide's **EO1 & display** step.

| Setting | Setup field | Notes |
| --- | --- | --- |
| `eo1_host` | EO1 hostname | Hostname, `host:port`, or URL of the frame. The port defaults to 12345. |
| `birdframe_url` | BirdFrame address for the EO1 | BirdFrame's address as seen from the EO1, e.g. `http://192.168.1.20:8765`. |
| `output_width` / `output_height` | Orientation | Portrait (1080×1920, the default) or Landscape (1920×1080). |
| `display_update_minutes` | Update the EO1 at most every | 1–1440 minutes, default 5. |
| `quiet_hours_start` / `quiet_hours_end` | Quiet hours start / end | `HH:MM` in the location timezone chosen in setup. |
| `display_auto_update_enabled` | Automatically show new artwork on the EO1 | On by default. Turn off to push only by hand. |
| `visitor_spotlight_enabled` | Spotlight each detected bird | Off by default. See [Visitor spotlight](#visitor-spotlight). |
| `visitor_spotlight_minutes` | Spotlight duration | 1–120 minutes, default 5. |

### BirdFrame address

Because the frame downloads the image, `birdframe_url` must be an address the
EO1 can reach on your network — normally the host's LAN IP and BirdFrame's
port. `localhost` or `127.0.0.1` would point the EO1 at itself, so they do not
work. The Setup page prefills the field from your browser's address unless
that is a loopback address.

Use plain `http://`. The EO1 runs Android 4.4, which cannot validate modern
certificate chains such as Let's Encrypt, so an `https://` address will
usually fail even when it works in a desktop browser.

### Orientation

The EO1 panel is 1080×1920. Choose **Portrait** when the frame hangs
vertically (the default) or **Landscape** when it hangs horizontally;
compositions are rendered at that size.

### Quiet hours

No automatic pushes happen between the quiet-hours start and end. This matters
on the EO1 because a push wakes its screen. The frame's own quiet hours (set in
the firmware) are separate; set both if you want the screen to stay dark.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Frame unreachable / push fails at once | Check `eo1_host` and that port 12345 is open: `curl http://<eo1>:12345/api/v1/state` from the BirdFrame host should return the frame's state. |
| Push succeeds but the image never appears | The EO1 cannot reach `birdframe_url`. From another device on the frame's network, open `{birdframe_url}/api/v1/display/compositions/<revision>.jpg` (revision from `/api/v1/display/current.json`). Avoid `localhost` and firewalls that block the frame. |
| Works in a browser but not on the frame | Use `http://` rather than `https://`; Android 4.4 cannot validate modern certificate chains. |
