import base64
import json

import httpx
import pytest

from birdframe.adapters import (BIRDNET_GO_SSE_READ_TIMEOUT, AdapterError, BirdNETGoClient, BirdWeatherClient, EO1Client, OpenRouterClient,
                                ProviderUnavailable, PublicBirdWeatherClient, eo1_base_url)


def async_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.mark.asyncio
async def test_birdnet_sse_yields_detection_and_ignores_heartbeat():
    activity = []

    async def handler(request):
        assert request.headers["accept"] == "text/event-stream"
        return httpx.Response(200, content=(
            b"event: connected\ndata: {\"message\":\"ok\"}\n\n"
            b"event: heartbeat\ndata: {\"clients\":1}\n\n"
            b"event: detection\ndata: {\"id\":7,\"timestamp\":\"2026-01-02T03:04:05Z\",\"commonName\":\"Eurasian Blackbird\",\"scientificName\":\"Turdus merula\",\"confidence\":0.91}\n\n"
        ))
    client = async_client(handler)
    adapter = BirdNETGoClient("http://birdnet:8080", client=client, on_activity=activity.append)
    received = [item async for item in adapter.detections()]
    assert [(item.provider, item.external_id, item.common_name) for item in received] == [("birdnet-go", "7", "Eurasian Blackbird")]
    assert activity == ["connected", "heartbeat", "detection"]
    await client.aclose()


@pytest.mark.asyncio
async def test_birdnet_stream_health_checks_the_sse_endpoint():
    async def handler(request):
        assert request.url.path == "/api/v2/detections/stream"
        return httpx.Response(200, content=b"event: connected\ndata: {\"type\":\"detections\"}\n\n")

    client = async_client(handler)
    adapter = BirdNETGoClient("http://birdnet:8080", client=client)
    result = await adapter.stream_health()
    assert result.available is True
    assert result.detail == "detection stream connected"
    await client.aclose()


@pytest.mark.asyncio
async def test_birdnet_sse_allows_three_provider_heartbeat_intervals():
    adapter = BirdNETGoClient("http://birdnet:8080")
    assert adapter._client.timeout.read == BIRDNET_GO_SSE_READ_TIMEOUT
    await adapter.aclose()


@pytest.mark.asyncio
async def test_birdweather_polls_avian_cursor_and_normalizes_oldest_first():
    async def handler(request):
        assert request.url.path == "/api/v1/stations/a token/detections"
        assert dict(request.url.params) == {"limit": "10", "classification": "avian", "cursor": "40"}
        return httpx.Response(200, json={"success": True, "detections": [
            {"id": 42, "timestamp": "2026-01-02T10:00:00+00:00", "confidence": .8, "species": {"id": 2, "commonName": "Later", "scientificName": "Laterus birdus"}},
            {"id": 41, "timestamp": "2026-01-02T09:00:00+00:00", "confidence": .7, "species": {"id": 1, "commonName": "Earlier", "scientificName": "Earlierus birdus"}},
        ]})
    client = async_client(handler)
    result = await BirdWeatherClient("a token", base_url="http://test/api/v1", client=client).poll(cursor="40", limit=10)
    assert [item.common_name for item in result.detections] == ["Earlier", "Later"]
    assert result.cursor == "42"
    await client.aclose()


@pytest.mark.asyncio
async def test_public_birdweather_polls_a_public_station_without_a_token():
    async def handler(request):
        assert request.url.path == "/graphql"
        body = json.loads(request.content)
        assert body["variables"] == {"stationIds": ["2505"], "first": 10, "after": None}
        assert "classifications" in body["query"]
        return httpx.Response(200, json={"data": {"detections": {"nodes": [
            {"id": "42", "timestamp": "2026-01-02T10:00:00+00:00", "confidence": .8, "coords": {"lat": 59.9, "lon": 10.7}, "species": {"id": "2", "commonName": "Later", "scientificName": "Laterus birdus"}},
            {"id": "41", "timestamp": "2026-01-02T09:00:00+00:00", "confidence": .7, "coords": {"lat": 59.8, "lon": 10.6}, "species": {"id": "1", "commonName": "Earlier", "scientificName": "Earlierus birdus"}},
        ]}}})
    client = async_client(handler)
    result = await PublicBirdWeatherClient(2505, base_url="http://test/graphql", client=client).poll(limit=10)
    assert [(item.provider, item.external_id, item.common_name) for item in result.detections] == [("birdweather-public", "41", "Earlier"), ("birdweather-public", "42", "Later")]
    await client.aclose()


@pytest.mark.asyncio
async def test_public_birdweather_history_follows_graphql_cursors():
    requests = []
    async def handler(request):
        body = json.loads(request.content); requests.append(body["variables"])
        if body["variables"]["after"] is None:
            return httpx.Response(200, json={"data": {"detections": {"nodes": [
                {"id": "2", "timestamp": "2026-01-02T10:00:00+00:00", "confidence": .8, "coords": {}, "species": {"id": "2", "commonName": "Later", "scientificName": "Laterus birdus"}},
            ], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}})
        return httpx.Response(200, json={"data": {"detections": {"nodes": [
            {"id": "1", "timestamp": "2026-01-02T09:00:00+00:00", "confidence": .7, "coords": {}, "species": {"id": "1", "commonName": "Earlier", "scientificName": "Earlierus birdus"}},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
    client = async_client(handler)
    result = await PublicBirdWeatherClient(2505, base_url="http://test/graphql", client=client).history()
    assert [item.external_id for item in result.detections] == ["1", "2"]
    assert [item["after"] for item in requests] == [None, "next"]
    await client.aclose()


@pytest.mark.asyncio
async def test_openrouter_filters_image_models_and_decodes_generated_b64():
    async def handler(request):
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "image/model", "name": "Image", "supported_parameters": ["size"], "pricing": {"image": "1"}},
                {"id": "text/model", "name": "Text", "supported_parameters": ["temperature"]},
            ]})
        assert request.url.path == "/api/v1/images"
        assert b'"model":"image/model"' in request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"png bytes").decode(), "revised_prompt": "bird"}]})
    client = async_client(handler)
    adapter = OpenRouterClient("secret", base_url="http://test/api/v1", client=client)
    assert [model.id for model in await adapter.image_models()] == ["image/model"]
    image = await adapter.generate(model="image/model", prompt="a bird", size="1024x1024")
    assert image.content == b"png bytes" and image.revised_prompt == "bird"
    await client.aclose()


def test_eo1_host_accepts_hostname_host_port_or_url():
    assert eo1_base_url("eo1.local") == "http://eo1.local:12345/"
    assert eo1_base_url(" 192.168.1.158:8080 ") == "http://192.168.1.158:8080/"
    assert eo1_base_url("http://eo1.local:12345/") == "http://eo1.local:12345/"
    with pytest.raises(ValueError):
        eo1_base_url("")


@pytest.mark.asyncio
async def test_eo1_show_posts_the_image_url_and_returns_state():
    async def handler(request):
        assert request.method == "POST" and request.url.path == "/api/v1/show"
        assert json.loads(request.content) == {"url": "http://birdframe.test/a.jpg", "type": "image"}
        return httpx.Response(200, json={"mode": "single", "item": {"url": "http://birdframe.test/a.jpg"}})
    client = async_client(handler)
    eo1 = EO1Client("eo1.local", client=client)
    assert eo1.base_url == "http://eo1.local:12345/"
    assert (await eo1.show("http://birdframe.test/a.jpg"))["mode"] == "single"
    await client.aclose()


@pytest.mark.asyncio
async def test_eo1_errors_carry_the_frame_message():
    async def handler(request):
        return httpx.Response(400, json={"error": {"code": "invalid_value", "field": "url", "message": "url is required"}})
    client = async_client(handler)
    with pytest.raises(AdapterError, match="HTTP 400: url is required"):
        await EO1Client("eo1.local", client=client).show("")
    await client.aclose()

    async def unreachable(request):
        raise httpx.ConnectError("refused", request=request)
    client = async_client(unreachable)
    with pytest.raises(ProviderUnavailable, match="unreachable"):
        await EO1Client("eo1.local", client=client).state()
    await client.aclose()

