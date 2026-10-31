# Setup wizard

The first browser visit opens a resumable wizard. Settings are persisted under
the BirdFrame data directory; provider tokens are never returned to the browser
after saving.

Before the wizard, the first visitor creates the administrator account (this
is also where the web UI's session API key is issued). Later visitors sign in
with that account or with accounts the admin adds under Settings → API keys
and users.

1. **Privacy and license.** Confirm the non-commercial licensing notice and
   review the external data flows.
2. **Location.** Enter/search a location, verify latitude/longitude, choose
   timezone and language. Location improves regional preparation and local
   detection filtering.
3. **Detection source.** Choose exactly one active source: BirdWeather or
   **Birdnet-GO or Birdframe Birdprovider**. Test the connection before saving it.
4. **EO1 & display.** Enter the EO1 hostname (port 12345 unless you add
   another), BirdFrame's address as the EO1 sees it (prefilled from your
   browser unless that is localhost), and the orientation: Portrait
   (1080×1920) or Landscape (1920×1080). Then choose the collage window, how
   often the EO1 may be updated, quiet hours, whether new artwork is shown
   automatically, and whether each detected bird is briefly spotlighted on its
   own. See [eo1.md](eo1.md).
5. **Artwork.** Install a signed/checksummed package or connect OpenRouter,
   choose an image-capable model, configure a budget, review ranked species and
   estimated maximum cost, then start a resumable job.
6. **Display behavior.** Choose Collage (default) or Latest visitor and
   detection confidence/cooldown.

Use Settings after setup to change the current-image API access policy,
appearance preset, regional package catalog, and retention settings.
Changing the active source preserves history but does not merge sources.
