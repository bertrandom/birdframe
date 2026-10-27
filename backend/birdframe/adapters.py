"""Network adapters for detection providers, image generation, and the EO1 display.

They are deliberately independent of FastAPI and persistence.  The application
can map :class:`Detection` and the result DTOs to its own database models.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

import httpx


class AdapterError(RuntimeError): pass
class ProviderUnavailable(AdapterError): pass


@dataclass(frozen=True, slots=True)
class Detection:
    provider: str; external_id: str; occurred_at: datetime; common_name: str
    scientific_name: str | None; confidence: float | None
    species_code: str | None = None; latitude: float | None = None; longitude: float | None = None
    raw: dict[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class Health:
    available: bool; detail: str | None = None

@dataclass(frozen=True, slots=True)
class PollResult:
    detections: tuple[Detection, ...]; cursor: str | None

@dataclass(frozen=True, slots=True)
class ImageModel:
    id: str; name: str; description: str | None; context_length: int | None
    pricing: dict[str, Any] | None; supported_parameters: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes; media_type: str; revised_prompt: str | None = None



BIRDNET_GO_SSE_READ_TIMEOUT = 90.0


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value: raise AdapterError("provider response is missing a timestamp")
    try: result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise AdapterError(f"invalid provider timestamp: {value!r}") from exc
    if result.tzinfo is None: raise AdapterError("provider timestamp has no timezone")
    return result

def _float(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


class BirdNETGoClient:
    """Async consumer of BirdNET-Go's public ``detection`` SSE events."""
    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None,
                 on_activity: Callable[[str], None] | None = None) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20, read=BIRDNET_GO_SSE_READ_TIMEOUT))
        self._owns = client is None
        self._on_activity = on_activity

    def _activity(self, kind: str) -> None:
        if self._on_activity:
            self._on_activity(kind)

    async def aclose(self) -> None:
        if self._owns: await self._client.aclose()
    async def health(self) -> Health:
        try:
            response = await self._client.get(urljoin(self.base_url, "api/v2/sse/status")); response.raise_for_status(); body = response.json()
            return Health(body.get("status") == "active", body.get("status"))
        except (httpx.HTTPError, ValueError) as exc: return Health(False, str(exc))

    async def stream_health(self) -> Health:
        """Verify that the detection SSE endpoint opens and emits an event."""
        try:
            async with self._client.stream("GET", urljoin(self.base_url, "api/v2/detections/stream"), headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status(); event = "message"
                async for line in response.aiter_lines():
                    if not line:
                        if event in {"connected", "heartbeat", "detection"}:
                            return Health(True, f"detection stream {event}")
                        event = "message"
                        continue
                    if line.startswith("event:"): event = line[6:].strip()
                return Health(False, "detection stream ended before sending an event")
        except (httpx.HTTPError, ValueError) as exc:
            return Health(False, str(exc))

    async def detections(self) -> AsyncIterator[Detection]:
        try:
            async with self._client.stream("GET", urljoin(self.base_url, "api/v2/detections/stream"), headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status(); self._activity("connected"); event, lines = "message", []
                async for line in response.aiter_lines():
                    if not line:
                        if event == "heartbeat":
                            self._activity("heartbeat")
                        elif event == "detection" and lines:
                            self._activity("detection")
                            yield self._parse(json.loads("\n".join(lines)))
                        event, lines = "message", []; continue
                    if line.startswith("event:"): event = line[6:].strip()
                    elif line.startswith("data:"): lines.append(line[5:].lstrip())
                if event == "detection" and lines:
                    self._activity("detection")
                    yield self._parse(json.loads("\n".join(lines)))
        except (httpx.HTTPError, json.JSONDecodeError) as exc: raise AdapterError(f"BirdNET-Go stream failed: {exc}") from exc
    @staticmethod
    def _parse(data: dict[str, Any]) -> Detection:
        name = data.get("commonName")
        if not isinstance(name, str) or not name: raise AdapterError("BirdNET-Go detection misses commonName")
        timestamp = _timestamp(data.get("timestamp") or data.get("beginTime"))
        return Detection("birdnet-go", str(data.get("id") or f"{name}:{timestamp.isoformat()}"), timestamp, name, data.get("scientificName"), _float(data.get("confidence")), data.get("speciesCode"), _float(data.get("latitude")), _float(data.get("longitude")), data)


class BirdWeatherClient:
    """Read-only, cursor-based BirdWeather station poller."""
    def __init__(self, station_token: str, *, base_url: str = "https://app.birdweather.com/api/v1/", client: httpx.AsyncClient | None = None) -> None:
        if not station_token: raise ValueError("station token is required")
        self.station_token = station_token; self.base_url = base_url.rstrip("/") + "/"; self._client = client or httpx.AsyncClient(timeout=20); self._owns = client is None
    async def aclose(self) -> None:
        if self._owns: await self._client.aclose()
    @property
    def url(self) -> str: return urljoin(self.base_url, f"stations/{quote(self.station_token, safe='')}/detections")
    async def health(self) -> Health:
        try:
            response = await self._client.get(self.url, params={"limit": 1, "classification": "avian"}); response.raise_for_status(); body = response.json()
            return Health(bool(body.get("success", True)))
        except (httpx.HTTPError, ValueError) as exc: return Health(False, str(exc))
    async def poll(self, *, cursor: str | None = None, limit: int = 100) -> PollResult:
        if not 1 <= limit <= 100: raise ValueError("limit must be from 1 to 100")
        params: dict[str, str | int] = {"limit": limit, "classification": "avian"}
        if cursor: params["cursor"] = cursor
        try:
            response = await self._client.get(self.url, params=params); response.raise_for_status(); body = response.json()
        except (httpx.HTTPError, ValueError) as exc: raise AdapterError(f"BirdWeather polling failed: {exc}") from exc
        if body.get("success") is False: raise AdapterError("BirdWeather rejected the station request")
        rows = body.get("detections", [])
        if not isinstance(rows, list): raise AdapterError("BirdWeather response has invalid detections")
        detections = tuple(sorted((self._parse(row) for row in rows), key=lambda item: item.occurred_at))
        ids = [int(item.external_id) for item in detections if item.external_id.isdigit()]
        return PollResult(detections, str(max(ids)) if ids else cursor)
    @staticmethod
    def _parse(data: dict[str, Any]) -> Detection:
        species = data.get("species") if isinstance(data.get("species"), dict) else data; name = species.get("commonName")
        if not isinstance(name, str) or not name: raise AdapterError("BirdWeather detection misses species.commonName")
        timestamp = _timestamp(data.get("timestamp"))
        return Detection("birdweather", str(data.get("id") or f"{name}:{timestamp.isoformat()}"), timestamp, name, species.get("scientificName"), _float(data.get("confidence")), str(species["id"]) if species.get("id") is not None else None, _float(data.get("lat")), _float(data.get("lon")), data)


class PublicBirdWeatherClient:
    """Read recent avian detections from a public BirdWeather station.

    BirdWeather's GraphQL API exposes public station data by numeric station ID;
    unlike the REST station endpoints it does not require the station owner's
    authentication token.  We deliberately re-read the small latest window on
    every poll and rely on the application's source-event de-duplication.  A
    Relay cursor would advance into older rows after the first poll and miss
    newly arriving detections in the default newest-first ordering.
    """

    QUERY = """
    query PublicStationDetections($stationIds: [ID!], $first: Int, $after: String) {
      detections(first: $first, after: $after, stationIds: $stationIds, classifications: [\"avian\"]) {
        nodes {
          id timestamp confidence
          coords { lat lon }
          species { id commonName scientificName }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
    """

    def __init__(self, station_id: int, *, base_url: str = "https://app.birdweather.com/graphql", client: httpx.AsyncClient | None = None) -> None:
        if station_id < 1:
            raise ValueError("public station ID must be positive")
        self.station_id = station_id
        self.base_url = base_url
        self._client = client or httpx.AsyncClient(timeout=20)
        self._owns = client is None

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def health(self) -> Health:
        try:
            await self.poll(limit=1)
            return Health(True)
        except AdapterError as exc:
            return Health(False, str(exc))

    async def poll(self, *, limit: int = 100, after: str | None = None) -> PollResult:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        try:
            response = await self._client.post(self.base_url, json={"query": self.QUERY, "variables": {"stationIds": [str(self.station_id)], "first": limit, "after": after}})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterError(f"Public BirdWeather polling failed: {exc}") from exc
        errors = body.get("errors")
        if errors:
            detail = errors[0].get("message", "GraphQL query failed") if isinstance(errors, list) and errors and isinstance(errors[0], dict) else "GraphQL query failed"
            raise AdapterError(f"Public BirdWeather rejected the station request: {detail}")
        connection = body.get("data", {}).get("detections", {}) if isinstance(body.get("data"), dict) else {}
        rows = connection.get("nodes", []) if isinstance(connection, dict) else []
        if not isinstance(rows, list):
            raise AdapterError("Public BirdWeather response has invalid detections")
        detections = tuple(sorted((self._parse(row) for row in rows), key=lambda item: item.occurred_at))
        page_info = connection.get("pageInfo", {}) if isinstance(connection, dict) else {}
        cursor = page_info.get("endCursor") if isinstance(page_info, dict) and page_info.get("hasNextPage") else None
        return PollResult(detections, cursor if isinstance(cursor, str) else None)

    async def history(self, *, max_results: int = 2000) -> PollResult:
        """Fetch the public station's complete default 24-hour window.

        BirdWeather's public GraphQL query defaults to the last 24 hours.
        Cursor paging is used only for this startup seed; live polling keeps
        requesting the newest page so new detections are never skipped.
        """
        if max_results < 1:
            raise ValueError("max_results must be positive")
        cursor: str | None = None
        collected: list[Detection] = []
        while len(collected) < max_results:
            page = await self.poll(limit=min(100, max_results - len(collected)), after=cursor)
            collected.extend(page.detections)
            if not page.cursor:
                break
            cursor = page.cursor
        return PollResult(tuple(sorted(collected, key=lambda item: item.occurred_at)), None)

    @staticmethod
    def _parse(data: dict[str, Any]) -> Detection:
        species = data.get("species") if isinstance(data.get("species"), dict) else {}
        name = species.get("commonName")
        if not isinstance(name, str) or not name:
            raise AdapterError("Public BirdWeather detection misses species.commonName")
        coords = data.get("coords") if isinstance(data.get("coords"), dict) else {}
        timestamp = _timestamp(data.get("timestamp"))
        return Detection("birdweather-public", str(data.get("id") or f"{name}:{timestamp.isoformat()}"), timestamp, name, species.get("scientificName"), _float(data.get("confidence")), str(species["id"]) if species.get("id") is not None else None, _float(coords.get("lat")), _float(coords.get("lon")), data)


class OpenRouterClient:
    """Client for OpenRouter model catalog and dedicated image endpoint."""
    def __init__(self, api_key: str, *, base_url: str = "https://openrouter.ai/api/v1/", client: httpx.AsyncClient | None = None) -> None:
        if not api_key: raise ValueError("OpenRouter API key is required")
        self.base_url = base_url.rstrip("/") + "/"; self._client = client or httpx.AsyncClient(timeout=120); self._owns = client is None
        self._headers = {"Authorization": f"Bearer {api_key}"}
    async def aclose(self) -> None:
        if self._owns: await self._client.aclose()
    async def image_models(self) -> tuple[ImageModel, ...]:
        try:
            response = await self._client.get(urljoin(self.base_url, "models"), headers=self._headers); response.raise_for_status(); rows = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc: raise AdapterError(f"OpenRouter model lookup failed: {exc}") from exc
        models = []
        for row in rows:
            params = tuple(row.get("supported_parameters") or ())
            # Catalog semantics evolve.  Include known image capable entries only.
            modalities = row.get("architecture", {}).get("output_modalities") or []
            if any(p in {"images", "image_generation", "size", "quality"} for p in params) or "image" in modalities:
                models.append(ImageModel(row["id"], row.get("name", row["id"]), row.get("description"), row.get("context_length"), row.get("pricing"), params))
        return tuple(models)
    async def generate(self, *, model: str, prompt: str, size: str | None = None, quality: str | None = None) -> GeneratedImage:
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        if size: payload["size"] = size
        if quality: payload["quality"] = quality
        try:
            response = await self._client.post(urljoin(self.base_url, "images"), headers=self._headers, json=payload); response.raise_for_status(); body = response.json()
        except (httpx.HTTPError, ValueError) as exc: raise AdapterError(f"OpenRouter image generation failed: {exc}") from exc
        images = body.get("data") or body.get("images") or []
        if not images: raise AdapterError("OpenRouter returned no generated image")
        image = images[0]; revised = image.get("revised_prompt") or body.get("revised_prompt")
        if image.get("b64_json"):
            try: return GeneratedImage(base64.b64decode(image["b64_json"], validate=True), "image/png", revised)
            except ValueError as exc: raise AdapterError("OpenRouter returned malformed image data") from exc
        if image.get("url"):
            try:
                download = await self._client.get(image["url"], follow_redirects=False); download.raise_for_status()
                return GeneratedImage(download.content, download.headers.get("content-type", "image/png").split(";", 1)[0], revised)
            except httpx.HTTPError as exc: raise AdapterError(f"OpenRouter image download failed: {exc}") from exc
        raise AdapterError("OpenRouter response had neither image data nor URL")


EO1_DEFAULT_PORT = 12345


def eo1_base_url(host: str) -> str:
    """Normalise an EO1 hostname, ``host:port`` or URL to its API base URL."""
    host = host.strip().rstrip("/")
    if not host:
        raise ValueError("EO1 host is required")
    if "://" not in host:
        host = f"http://{host}"
    parsed = urlsplit(host)
    if not parsed.hostname:
        raise ValueError(f"Not a valid EO1 host: {host}")
    try:
        port = parsed.port or EO1_DEFAULT_PORT
    except ValueError as exc:
        raise ValueError(f"Not a valid EO1 host: {host}") from exc
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{hostname}:{port}/"


class EO1Client:
    """Client for the EO1 frame's HTTP API (``/api/v1`` on port 12345).

    The frame downloads what it shows itself, so BirdFrame hands it the URL of
    a composition rather than uploading image bytes.
    """
    def __init__(self, host: str, *, client: httpx.AsyncClient | None = None, timeout: float = 15) -> None:
        self.base_url = eo1_base_url(host)
        self._client = client or httpx.AsyncClient(timeout=timeout); self._owns = client is None
    async def aclose(self) -> None:
        if self._owns: await self._client.aclose()
    async def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, urljoin(self.base_url, path), json=payload)
        except httpx.HTTPError as exc: raise ProviderUnavailable(f"EO1 at {self.base_url} is unreachable: {type(exc).__name__}") from exc
        try: body = response.json()
        except ValueError: body = {}
        if response.is_error:
            error = body.get("error") if isinstance(body, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            raise AdapterError(f"EO1 returned HTTP {response.status_code}" + (f": {message}" if message else ""))
        return body if isinstance(body, dict) else {}
    async def show(self, url: str) -> dict[str, Any]:
        """Show one image outside the frame's playlist, pausing its slideshow; returns the frame's state."""
        return await self._call("POST", "api/v1/show", {"url": url, "type": "image"})
    async def state(self) -> dict[str, Any]:
        return await self._call("GET", "api/v1/state")
