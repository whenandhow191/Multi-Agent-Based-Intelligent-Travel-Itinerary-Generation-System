"""Credential-free transport and lodging provider contracts and safe MVP stubs."""

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from packages.domain import (
    Availability,
    Coordinate,
    CoordinateSystem,
    CostEstimate,
    CostKind,
    Evidence,
    Freshness,
    IntercityMode,
    IntercityTransport,
    Lodging,
    StoragePolicy,
)
from packages.domain.common import DomainModel, NonEmptyText, ShortText
from packages.harness import ToolContext, ToolDefinition


class FlightSearchInput(DomainModel):
    origin: ShortText
    destination: ShortText
    departure_date: date
    adults: int = Field(default=1, ge=1, le=9)


class FlightSearchOutput(DomainModel):
    provider_mode: Literal["fixture", "manual"]
    options: tuple[IntercityTransport, ...]
    evidence: tuple[Evidence, ...]
    inventory_live: Literal[False] = False
    warnings: tuple[NonEmptyText, ...]


class RailOfficialLinkInput(DomainModel):
    origin: ShortText
    destination: ShortText
    departure_date: date


class RailOfficialLinkOutput(DomainModel):
    provider_mode: Literal["official_link"] = "official_link"
    url: NonEmptyText
    inventory_live: Literal[False] = False
    manual_confirmation_required: Literal[True] = True
    warning: NonEmptyText


class LodgingSearchInput(DomainModel):
    city: ShortText
    area: ShortText | None = None
    check_in: date
    check_out: date
    limit: int = Field(default=3, ge=1, le=10)


class LodgingSearchOutput(DomainModel):
    provider_mode: Literal["fixture", "manual"]
    candidates: tuple[Lodging, ...]
    evidence: tuple[Evidence, ...]
    inventory_live: Literal[False] = False
    warnings: tuple[NonEmptyText, ...]


class FlightProvider(Protocol):
    async def search(self, request: FlightSearchInput) -> FlightSearchOutput: ...


class LodgingProvider(Protocol):
    async def search(self, request: LodgingSearchInput) -> LodgingSearchOutput: ...


class FixtureTravelProviders:
    """Synthetic providers that never claim current schedules, prices, or availability."""

    def __init__(self, *, now: datetime | None = None) -> None:
        self.now = now

    async def search_flights(self, request: FlightSearchInput) -> FlightSearchOutput:
        now = self.now or datetime.now(UTC)
        timezone = ZoneInfo("Asia/Shanghai")
        departure = datetime.combine(request.departure_date, time(8, 0), timezone)
        transport_id = _stable_id(
            "transport_fixture_flight", f"{request.origin}:{request.destination}:{departure.date()}"
        )
        evidence_id = f"evidence_{transport_id}"
        option = IntercityTransport(
            transport_id=transport_id,
            provider="fixture",
            mode=IntercityMode.FLIGHT,
            service_number="SYN1001",
            origin_name=request.origin,
            destination_name=request.destination,
            departure_at=departure,
            arrival_at=departure + timedelta(hours=2, minutes=15),
            price=CostEstimate(
                kind=CostKind.UNKNOWN,
                basis="Synthetic fixture has no live fare",
            ),
            availability=Availability.REFERENCE_ONLY,
            retrieved_at=now,
            evidence_ids=(evidence_id,),
        )
        evidence = _fixture_evidence(evidence_id, transport_id, now, "flight fixture")
        return FlightSearchOutput(
            provider_mode="fixture",
            options=(option,),
            evidence=(evidence,),
            warnings=(
                "Synthetic flight schedule and unknown price; confirm on an authorized provider.",
            ),
        )

    async def rail_link(self, request: RailOfficialLinkInput) -> RailOfficialLinkOutput:
        query = urlencode(
            {
                "from": request.origin,
                "to": request.destination,
                "date": request.departure_date.isoformat(),
            }
        )
        return RailOfficialLinkOutput(
            url=f"https://www.12306.cn/index/?{query}",
            warning=(
                "Official-site handoff only; no schedule, inventory, login, or purchase automation."
            ),
        )

    async def search_lodging(self, request: LodgingSearchInput) -> LodgingSearchOutput:
        if request.check_out <= request.check_in:
            raise ValueError("lodging check_out must be after check_in")
        now = self.now or datetime.now(UTC)
        candidates: list[Lodging] = []
        evidence: list[Evidence] = []
        for index in range(min(request.limit, 3)):
            lodging_id = _stable_id(
                "lodging_fixture",
                f"{request.city}:{request.area or 'central'}:{request.check_in}:{index}",
            )
            evidence_id = f"evidence_{lodging_id}"
            candidates.append(
                Lodging(
                    lodging_id=lodging_id,
                    provider="fixture",
                    provider_lodging_id=f"synthetic_{index + 1}",
                    name=f"合成住宿候选 {index + 1}",
                    area=request.area or f"{request.city}中心区域",
                    coordinate=Coordinate(
                        longitude=116.397 + index * 0.01,
                        latitude=39.908 + index * 0.01,
                        system=CoordinateSystem.GCJ02,
                    ),
                    nightly_price=CostEstimate(
                        kind=CostKind.UNKNOWN,
                        basis="Synthetic fixture has no live room rate",
                    ),
                    evidence_ids=(evidence_id,),
                )
            )
            evidence.append(_fixture_evidence(evidence_id, lodging_id, now, "lodging fixture"))
        return LodgingSearchOutput(
            provider_mode="fixture",
            candidates=tuple(candidates),
            evidence=tuple(evidence),
            warnings=(
                "Synthetic lodging candidates; room price and availability require confirmation.",
            ),
        )

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        async def flight_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.search_flights(FlightSearchInput.model_validate(arguments))

        async def rail_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.rail_link(RailOfficialLinkInput.model_validate(arguments))

        async def lodging_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.search_lodging(LodgingSearchInput.model_validate(arguments))

        return (
            ToolDefinition(
                name="flight.search",
                description=(
                    "Return reference-only flight fixtures until a licensed provider is enabled."
                ),
                input_model=FlightSearchInput,
                output_model=FlightSearchOutput,
                handler=flight_handler,
            ),
            ToolDefinition(
                name="rail.official_link",
                description=(
                    "Build an official 12306 handoff without scraping or purchase automation."
                ),
                input_model=RailOfficialLinkInput,
                output_model=RailOfficialLinkOutput,
                handler=rail_handler,
            ),
            ToolDefinition(
                name="lodging.search",
                description="Return non-live lodging fixtures with explicit unknown prices.",
                input_model=LodgingSearchInput,
                output_model=LodgingSearchOutput,
                handler=lodging_handler,
            ),
        )


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _fixture_evidence(
    evidence_id: str, object_id: str, now: datetime, source_name: str
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_name=source_name,
        source_url_or_provider_id=object_id,
        provider="fixture",
        retrieved_at=now,
        field_paths=("identity", "reference_only"),
        freshness=Freshness.UNKNOWN,
        confidence=0.2,
        storage_policy=StoragePolicy.FULL_LICENSED,
    )
