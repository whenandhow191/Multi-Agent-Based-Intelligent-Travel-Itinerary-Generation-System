"""Validated provider and per-Agent profiles loaded from YAML configuration."""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
import yaml
from pydantic import AnyHttpUrl, Field, SecretStr, StringConstraints

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness import ModelGateway, ModelPolicy, ModelRequest, ModelTurn
from packages.models.adapters import OllamaProvider, OpenAICompatibleProvider, ResponsesProvider
from packages.models.routing import ModelCandidate, ModelRoutingPolicy, RoutedModelGateway

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
ProfileTier = Literal["default", "advanced", "flagship"]
EnvironmentVariable = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$", max_length=120)
]


class ProviderProfile(DomainModel):
    """One transport endpoint and credential reference, never a credential value."""

    adapter: Literal["responses", "openai_compatible", "ollama"]
    base_url: AnyHttpUrl
    api_key_env: EnvironmentVariable | None = None
    default_model: NonEmptyText


class ProviderCatalog(DomainModel):
    providers: dict[Identifier, ProviderProfile]


class AgentModelSelection(DomainModel):
    """Model knobs configurable without changing Agent implementation code."""

    provider: Identifier
    model: NonEmptyText
    reasoning: ReasoningEffort = "low"
    max_output_tokens: Annotated[int, Field(ge=1, le=128_000)] = 4096
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 60


class AgentProfiles(DomainModel):
    default: AgentModelSelection
    advanced: AgentModelSelection | None = None
    flagship: AgentModelSelection | None = None


class AgentCatalog(DomainModel):
    agents: dict[Identifier, AgentProfiles]


class ResolvedModelProfile(DomainModel):
    """Frozen selection recorded with a run and used to build one gateway."""

    agent_id: Identifier
    tier: ProfileTier
    provider_id: Identifier
    adapter: Literal["responses", "openai_compatible", "ollama"]
    base_url: AnyHttpUrl
    api_key_env: EnvironmentVariable | None = None
    model_id: NonEmptyText
    reasoning: ReasoningEffort
    max_output_tokens: int
    timeout_seconds: float

    def apply_policy(self, source: ModelPolicy) -> ModelPolicy:
        return source.model_copy(
            update={
                "reasoning_effort": self.reasoning,
                "max_output_tokens": self.max_output_tokens,
                "timeout_seconds": self.timeout_seconds,
            }
        )


class ProfiledModelGateway(ModelGateway):
    """Override only model controls while preserving the Agent's logical alias."""

    def __init__(self, profile: ResolvedModelProfile, gateway: ModelGateway) -> None:
        self.profile = profile
        self.gateway = gateway

    async def generate(self, request: ModelRequest) -> ModelTurn:
        return await self.gateway.generate(
            request.model_copy(update={"policy": self.profile.apply_policy(request.policy)})
        )


class ModelProfileRegistry:
    """Resolve YAML profiles and construct the requested provider adapter."""

    def __init__(self, providers: ProviderCatalog, agents: AgentCatalog) -> None:
        self.providers = providers
        self.agents = agents
        for profiles in agents.agents.values():
            for selection in (profiles.default, profiles.advanced, profiles.flagship):
                if selection is not None and selection.provider not in providers.providers:
                    raise ValueError(f"unknown model provider: {selection.provider}")

    @classmethod
    def from_files(cls, providers_path: Path, agents_path: Path) -> "ModelProfileRegistry":
        providers = ProviderCatalog.model_validate(_load_yaml(providers_path))
        agents = AgentCatalog.model_validate(_load_yaml(agents_path))
        return cls(providers, agents)

    def resolve(self, agent_id: str, tier: ProfileTier = "default") -> ResolvedModelProfile:
        try:
            profiles = self.agents.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"agent profile is not configured: {agent_id}") from exc
        selection = getattr(profiles, tier)
        if selection is None:
            raise KeyError(f"agent {agent_id} has no {tier} profile")
        provider = self.providers.providers[selection.provider]
        return ResolvedModelProfile(
            agent_id=agent_id,
            tier=tier,
            provider_id=selection.provider,
            adapter=provider.adapter,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            model_id=selection.model,
            reasoning=selection.reasoning,
            max_output_tokens=selection.max_output_tokens,
            timeout_seconds=selection.timeout_seconds,
        )

    def build_gateway(
        self,
        agent_id: str,
        client: httpx.AsyncClient,
        environment: Mapping[str, str],
        tier: ProfileTier = "default",
    ) -> ProfiledModelGateway:
        profile = self.resolve(agent_id, tier)
        raw_key = environment.get(profile.api_key_env, "") if profile.api_key_env else ""
        api_key = SecretStr(raw_key) if raw_key else None
        base_url = str(profile.base_url).rstrip("/")
        if profile.adapter == "ollama":
            gateway: ModelGateway = OllamaProvider(
                model_id=profile.model_id, client=client, base_url=base_url
            )
        elif profile.adapter == "openai_compatible":
            gateway = OpenAICompatibleProvider(
                provider_id=profile.provider_id,
                base_url=base_url,
                model_id=profile.model_id,
                client=client,
                api_key=api_key,
            )
        else:
            gateway = ResponsesProvider(
                provider_id=profile.provider_id,
                base_url=base_url,
                model_id=profile.model_id,
                client=client,
                api_key=api_key,
            )
        return ProfiledModelGateway(profile, gateway)

    def build_routed_gateway(
        self,
        agent_id: str,
        client: httpx.AsyncClient,
        environment: Mapping[str, str],
        *,
        tiers: tuple[ProfileTier, ...] = ("default", "advanced", "flagship"),
        policy: ModelRoutingPolicy | None = None,
    ) -> RoutedModelGateway:
        candidates: list[ModelCandidate] = []
        tier_numbers: dict[ProfileTier, Literal[0, 1, 2]] = {
            "default": 0,
            "advanced": 1,
            "flagship": 2,
        }
        for tier in tiers:
            try:
                profile = self.resolve(agent_id, tier)
            except KeyError:
                continue
            candidates.append(
                ModelCandidate(
                    provider_id=profile.provider_id,
                    model_id=profile.model_id,
                    tier=tier_numbers[tier],
                    gateway=self.build_gateway(agent_id, client, environment, tier),
                    max_attempts=2 if tier == tiers[0] else 1,
                )
            )
        return RoutedModelGateway(agent_id=agent_id, candidates=candidates, policy=policy)


def load_default_profiles(project_root: Path | None = None) -> ModelProfileRegistry:
    root = project_root or Path.cwd()
    return ModelProfileRegistry.from_files(
        root / "config" / "providers.yaml", root / "config" / "agents.yaml"
    )


def _load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be an object: {path.name}")
    return value
