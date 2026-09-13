"""Composition root for the provider-neutral built-in tool registry."""

from collections.abc import Sequence

import httpx

from apps.api.settings import Settings
from packages.harness.tool_gateway import ToolDefinition, ToolRegistry, fixture_echo_tool
from packages.tools.amap import AmapAdapter
from packages.tools.http_provider import HttpProviderPolicy, ResilientHttpProvider
from packages.tools.routes import AmapRouteAdapter
from packages.tools.travel_stubs import FixtureTravelProviders
from packages.tools.weather import OpenMeteoAdapter


def default_tool_definitions(
    settings: Settings, client: httpx.AsyncClient
) -> tuple[ToolDefinition, ...]:
    """Build all REST/fixture definitions behind one ToolGateway boundary."""

    amap_http = ResilientHttpProvider(
        provider="amap",
        client=client,
        policy=HttpProviderPolicy(requests_per_second=5, cache_ttl_seconds=300),
    )
    weather_http = ResilientHttpProvider(
        provider="open_meteo",
        client=client,
        policy=HttpProviderPolicy(requests_per_second=3, cache_ttl_seconds=900),
    )
    definitions: Sequence[ToolDefinition] = (
        fixture_echo_tool(),
        *AmapAdapter(amap_http, settings.amap_web_service_key).tool_definitions(),
        *AmapRouteAdapter(amap_http, settings.amap_web_service_key).tool_definitions(),
        OpenMeteoAdapter(weather_http).tool_definition(),
        *FixtureTravelProviders().tool_definitions(),
    )
    return tuple(definitions)


def build_default_registry(settings: Settings, client: httpx.AsyncClient) -> ToolRegistry:
    """Return the canonical registry used by CLI and future REST/MCP adapters."""

    return ToolRegistry(default_tool_definitions(settings, client))
