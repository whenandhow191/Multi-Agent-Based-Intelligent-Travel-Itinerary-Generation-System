"""Tool gateway contracts and provider adapters."""

from packages.tools.amap import (
    AmapAdapter,
    AmapProviderError,
    GeocodeCandidate,
    GeocodeInput,
    GeocodeOutput,
    PlaceSearchInput,
    PlaceSearchOutput,
)
from packages.tools.http_provider import (
    CircuitOpenError,
    HttpProviderPolicy,
    MemoryAuditSink,
    ProviderCallAudit,
    ProviderHttpError,
    ProviderResponseTooLargeError,
    ProviderUnavailableError,
    ResilientHttpProvider,
)
from packages.tools.routes import (
    AmapRouteAdapter,
    MapPolyline,
    RouteComputeInput,
    RouteComputeOutput,
    RouteEndpoint,
    RouteMatrixInput,
    RouteMatrixOutput,
    to_gcj02,
)
from packages.tools.weather import (
    OpenMeteoAdapter,
    WeatherForecastInput,
    WeatherForecastOutput,
)

__all__ = [
    "AmapAdapter",
    "AmapProviderError",
    "AmapRouteAdapter",
    "CircuitOpenError",
    "GeocodeCandidate",
    "GeocodeInput",
    "GeocodeOutput",
    "HttpProviderPolicy",
    "MemoryAuditSink",
    "MapPolyline",
    "OpenMeteoAdapter",
    "PlaceSearchInput",
    "PlaceSearchOutput",
    "ProviderCallAudit",
    "ProviderHttpError",
    "ProviderResponseTooLargeError",
    "ProviderUnavailableError",
    "ResilientHttpProvider",
    "RouteComputeInput",
    "RouteComputeOutput",
    "RouteEndpoint",
    "RouteMatrixInput",
    "RouteMatrixOutput",
    "WeatherForecastInput",
    "WeatherForecastOutput",
    "to_gcj02",
]
