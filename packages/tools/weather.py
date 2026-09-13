"""Weather provider adapter with explicit forecast horizon and conservative fallback."""

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, JsonValue, model_validator

from packages.domain import (
    Coordinate,
    CoordinateSystem,
    Evidence,
    Freshness,
    StoragePolicy,
    WeatherForecast,
)
from packages.domain.common import DomainModel, NonEmptyText
from packages.harness import ToolContext, ToolDefinition
from packages.tools.http_provider import ProviderHttpError, ResilientHttpProvider

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherForecastInput(DomainModel):
    location: Coordinate
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def date_range_is_bounded(self) -> "WeatherForecastInput":
        if self.end_date < self.start_date:
            raise ValueError("weather end_date must not precede start_date")
        if (self.end_date - self.start_date).days > 15:
            raise ValueError("weather request cannot exceed 16 days")
        return self


class WeatherForecastOutput(DomainModel):
    provider_mode: Literal["live", "fixture", "climate_guidance"]
    forecasts: tuple[WeatherForecast, ...]
    evidence: tuple[Evidence, ...]
    warnings: tuple[NonEmptyText, ...] = ()


class OpenMeteoAdapter:
    """Normalize Open-Meteo daily forecasts without inventing distant precision."""

    def __init__(
        self,
        http: ResilientHttpProvider,
        *,
        now: datetime | None = None,
        forecast_horizon_days: int = 16,
    ) -> None:
        if not 1 <= forecast_horizon_days <= 16:
            raise ValueError("forecast_horizon_days must be between 1 and 16")
        self.http = http
        self.now = now
        self.forecast_horizon_days = forecast_horizon_days

    async def forecast(self, request: WeatherForecastInput) -> WeatherForecastOutput:
        now = self.now or datetime.now(UTC)
        last_precise_date = now.date() + timedelta(days=self.forecast_horizon_days - 1)
        if request.start_date > last_precise_date or request.end_date > last_precise_date:
            return WeatherForecastOutput(
                provider_mode="climate_guidance",
                forecasts=(),
                evidence=(),
                warnings=(
                    "Requested dates exceed the configured precise forecast horizon; use seasonal "
                    "climate guidance and recheck shortly before travel.",
                ),
            )
        location = Coordinate(
            longitude=request.location.longitude,
            latitude=request.location.latitude,
            system=CoordinateSystem.WGS84,
        )
        try:
            payload = await self.http.request_json(
                operation="weather_forecast",
                method="GET",
                url=OPEN_METEO_FORECAST_URL,
                params={
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max"
                    ),
                    "timezone": "auto",
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                },
            )
        except ProviderHttpError:
            return _fixture_weather(request, location, now)
        return _normalize_open_meteo(payload, request, location, now)

    def tool_definition(self) -> ToolDefinition:
        async def handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.forecast(WeatherForecastInput.model_validate(arguments))

        return ToolDefinition(
            name="weather.forecast",
            description="Return dated forecasts or explicit climate guidance beyond the horizon.",
            input_model=WeatherForecastInput,
            output_model=WeatherForecastOutput,
            handler=handler,
        )


def _normalize_open_meteo(
    payload: JsonValue,
    request: WeatherForecastInput,
    location: Coordinate,
    now: datetime,
) -> WeatherForecastOutput:
    if not isinstance(payload, dict) or not isinstance(payload.get("daily"), dict):
        raise ProviderHttpError("Open-Meteo response does not contain daily data")
    daily = cast(dict[str, JsonValue], payload["daily"])
    dates = _list(daily.get("time"))
    codes = _list(daily.get("weather_code"))
    maximums = _list(daily.get("temperature_2m_max"))
    minimums = _list(daily.get("temperature_2m_min"))
    precipitation = _list(daily.get("precipitation_probability_max"))
    if not dates or not (len(dates) == len(codes) == len(maximums) == len(minimums)):
        raise ProviderHttpError("Open-Meteo daily arrays are incomplete")
    forecasts: list[WeatherForecast] = []
    evidence: list[Evidence] = []
    for index, raw_date in enumerate(dates):
        forecast_date = date.fromisoformat(str(raw_date))
        if not request.start_date <= forecast_date <= request.end_date:
            continue
        weather_id = _weather_id("open_meteo", forecast_date, location)
        evidence_id = f"evidence_{weather_id}"
        forecasts.append(
            WeatherForecast(
                weather_id=weather_id,
                provider="open_meteo",
                location=location,
                forecast_date=forecast_date,
                condition=_weather_condition(round(_number(codes[index]))),
                temperature_min_c=_number(minimums[index]),
                temperature_max_c=_number(maximums[index]),
                precipitation_probability=(
                    _number(precipitation[index]) / 100 if index < len(precipitation) else None
                ),
                retrieved_at=now,
                valid_until=now + timedelta(hours=6),
                evidence_ids=(evidence_id,),
            )
        )
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                source_name="Open-Meteo Forecast API",
                source_url_or_provider_id=OPEN_METEO_FORECAST_URL,
                provider="open_meteo",
                retrieved_at=now,
                valid_until=now + timedelta(hours=6),
                field_paths=("condition", "temperature_min_c", "temperature_max_c"),
                freshness=Freshness.FRESH,
                confidence=0.85,
                storage_policy=StoragePolicy.ALLOWED_FIELDS,
            )
        )
    return WeatherForecastOutput(
        provider_mode="live",
        forecasts=tuple(forecasts),
        evidence=tuple(evidence),
    )


def _fixture_weather(
    request: WeatherForecastInput, location: Coordinate, now: datetime
) -> WeatherForecastOutput:
    forecasts: list[WeatherForecast] = []
    evidence: list[Evidence] = []
    current = request.start_date
    while current <= request.end_date:
        weather_id = _weather_id("fixture", current, location)
        evidence_id = f"evidence_{weather_id}"
        forecasts.append(
            WeatherForecast(
                weather_id=weather_id,
                provider="fixture",
                location=location,
                forecast_date=current,
                condition="synthetic partly cloudy",
                temperature_min_c=16,
                temperature_max_c=25,
                precipitation_probability=None,
                retrieved_at=now,
                valid_until=now + timedelta(hours=1),
                evidence_ids=(evidence_id,),
            )
        )
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                source_name="Synthetic weather fixture",
                source_url_or_provider_id=weather_id,
                provider="fixture",
                retrieved_at=now,
                valid_until=now + timedelta(hours=1),
                field_paths=("condition", "temperature_min_c", "temperature_max_c"),
                freshness=Freshness.UNKNOWN,
                confidence=0.2,
                storage_policy=StoragePolicy.FULL_LICENSED,
            )
        )
        current += timedelta(days=1)
    return WeatherForecastOutput(
        provider_mode="fixture",
        forecasts=tuple(forecasts),
        evidence=tuple(evidence),
        warnings=("Live weather provider failed; values are synthetic and not a forecast.",),
    )


def _list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _number(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProviderHttpError("Open-Meteo daily value is not numeric")
    try:
        return float(value)
    except ValueError as exc:
        raise ProviderHttpError("Open-Meteo daily value is not numeric") from exc


def _weather_id(provider: str, forecast_date: date, location: Coordinate) -> str:
    raw = f"{provider}:{forecast_date}:{location.longitude:.4f}:{location.latitude:.4f}"
    return f"weather_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _weather_condition(code: int) -> str:
    if code == 0:
        return "clear"
    if code in {1, 2, 3}:
        return "cloudy"
    if code in {45, 48}:
        return "fog"
    if code in {51, 53, 55, 56, 57}:
        return "drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "thunderstorm"
    return "unknown"
