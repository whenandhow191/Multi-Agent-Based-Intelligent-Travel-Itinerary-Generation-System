"""A1 destination intelligence Agent and its evidence-complete artifact."""

from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from packages.domain import Claim, Evidence, Place, WeatherForecast
from packages.domain.common import DomainModel, Identifier, NonEmptyText, SchemaVersion, ShortText
from packages.harness.agent import AgentSpec, BaseAgent
from packages.harness.model_gateway import ModelGateway, ModelPolicy
from packages.harness.tool_gateway import ToolGateway

DESTINATION_TOOL_ALLOWLIST = (
    "entity.resolve",
    "places.details",
    "places.search",
    "public_page.read",
    "weather.forecast",
)


class DestinationIntelArtifact(DomainModel):
    """Canonical A1 output with complete evidence references and explicit unknowns."""

    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    run_id: Identifier
    producer_agent: Identifier
    destination: ShortText
    places: Annotated[tuple[Place, ...], Field(min_length=1)]
    weather: tuple[WeatherForecast, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]
    unknowns: tuple[NonEmptyText, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def references_are_complete(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("destination evidence IDs must be unique")
        known_evidence = set(evidence_ids)
        place_ids = [place.place_id for place in self.places]
        if len(place_ids) != len(set(place_ids)):
            raise ValueError("destination place IDs must be unique")

        for label, references in (
            *((f"place {place.place_id}", place.evidence_ids) for place in self.places),
            *((f"weather {item.weather_id}", item.evidence_ids) for item in self.weather),
            *((f"claim {claim.claim_id}", claim.evidence_ids) for claim in self.claims),
        ):
            missing = set(references) - known_evidence
            if missing:
                raise ValueError(f"{label} references missing evidence: {sorted(missing)}")
        return self


DestinationResearchArtifact = DestinationIntelArtifact


class DestinationIntelligenceAgent(BaseAgent[DestinationIntelArtifact]):
    """Discover and normalize destination facts without creating an itinerary."""

    def __init__(self, model_gateway: ModelGateway, tool_gateway: ToolGateway) -> None:
        super().__init__(
            AgentSpec(
                agent_id="destination_intelligence",
                system_prompt=(
                    "Research destination candidates through allowlisted tools only. Separate "
                    "provider facts from estimates, preserve unknown values, attach evidence to "
                    "every external fact, and return DestinationIntelArtifact. Do not create "
                    "a day schedule."
                ),
                output_model=DestinationIntelArtifact,
                model_policy=ModelPolicy(
                    model_alias="destination-intelligence",
                    temperature=0,
                    max_tool_calls=8,
                ),
                tool_allowlist=DESTINATION_TOOL_ALLOWLIST,
                max_steps=10,
                timeout_seconds=120,
            ),
            model_gateway,
            tool_gateway,
        )
