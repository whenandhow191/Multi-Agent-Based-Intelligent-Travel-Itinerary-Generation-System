"""A2 mobility and lodging Agent with conservative degradation contracts."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from packages.domain import Availability, Evidence, IntercityTransport, Lodging, Route
from packages.domain.common import DomainModel, Identifier, NonEmptyText, SchemaVersion
from packages.domain.travel import CostKind
from packages.harness.agent import AgentSpec, BaseAgent
from packages.harness.model_gateway import ModelGateway, ModelPolicy
from packages.harness.tool_gateway import ToolGateway

MOBILITY_LODGING_TOOL_ALLOWLIST = (
    "currency.convert",
    "flight.search",
    "lodging.search",
    "rail.official_link",
    "routes.compute",
)


class RouteScope(StrEnum):
    """Route scope A2 may produce before candidate merging."""

    TARGETED_EXISTING_ENDPOINTS = "targeted_existing_endpoints"


class ProviderFailure(DomainModel):
    """A normalized provider failure safe to expose to fallback policy."""

    provider: Identifier
    operation: Identifier
    message: NonEmptyText
    retryable: bool


class ConnectionRisk(DomainModel):
    """A structured transfer/check-in risk linked to known objects."""

    risk_id: Identifier
    severity: Annotated[int, Field(ge=1, le=3)]
    summary: NonEmptyText
    related_ids: tuple[Identifier, ...]
    manual_confirmation_required: bool = False


class MobilityLodgingArtifact(DomainModel):
    """Canonical A2 output without a premature all-POI route matrix."""

    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    run_id: Identifier
    producer_agent: Identifier
    transport_options: tuple[IntercityTransport, ...] = ()
    lodging_candidates: tuple[Lodging, ...] = ()
    targeted_routes: tuple[Route, ...] = ()
    route_scope: RouteScope = RouteScope.TARGETED_EXISTING_ENDPOINTS
    connection_risks: tuple[ConnectionRisk, ...] = ()
    provider_failures: tuple[ProviderFailure, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    unknowns: tuple[NonEmptyText, ...] = ()
    manual_confirmation_required: bool = False
    created_at: AwareDatetime

    @model_validator(mode="after")
    def provenance_and_fallback_are_explicit(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("mobility evidence IDs must be unique")
        known_evidence = set(evidence_ids)
        referenced: list[tuple[str, tuple[str, ...]]] = [
            *(
                (f"transport {item.transport_id}", item.evidence_ids)
                for item in self.transport_options
            ),
            *(
                (f"lodging {item.lodging_id}", item.evidence_ids)
                for item in self.lodging_candidates
            ),
            *((f"route {item.route_id}", item.evidence_ids) for item in self.targeted_routes),
        ]
        for label, references in referenced:
            missing = set(references) - known_evidence
            if missing:
                raise ValueError(f"{label} references missing evidence: {sorted(missing)}")

        uncertain_cost = any(
            option.price.kind is CostKind.UNKNOWN for option in self.transport_options
        ) or any(
            lodging.nightly_price.kind is CostKind.UNKNOWN for lodging in self.lodging_candidates
        )
        non_live_inventory = any(
            option.availability is not Availability.LIVE for option in self.transport_options
        )
        needs_confirmation = bool(
            uncertain_cost
            or non_live_inventory
            or self.provider_failures
            or any(risk.manual_confirmation_required for risk in self.connection_risks)
        )
        if needs_confirmation and not self.manual_confirmation_required:
            raise ValueError("uncertain mobility data requires manual confirmation")
        if needs_confirmation and not self.unknowns:
            raise ValueError("manual confirmation requires an explicit unknown or caveat")
        return self


LogisticsArtifact = MobilityLodgingArtifact


class MobilityLodgingAgent(BaseAgent[MobilityLodgingArtifact]):
    """Research transport/lodging options and targeted connection risks."""

    def __init__(self, model_gateway: ModelGateway, tool_gateway: ToolGateway) -> None:
        super().__init__(
            AgentSpec(
                agent_id="mobility_lodging",
                system_prompt=(
                    "Research intercity transport, lodging areas, check-in/out constraints and "
                    "targeted connections through allowlisted tools. Preserve unknown prices and "
                    "provider failures, request manual confirmation when needed, and never build "
                    "the full POI route matrix."
                ),
                output_model=MobilityLodgingArtifact,
                model_policy=ModelPolicy(
                    model_alias="mobility-lodging",
                    temperature=0,
                    max_tool_calls=8,
                ),
                tool_allowlist=MOBILITY_LODGING_TOOL_ALLOWLIST,
                max_steps=10,
                timeout_seconds=120,
            ),
            model_gateway,
            tool_gateway,
        )
