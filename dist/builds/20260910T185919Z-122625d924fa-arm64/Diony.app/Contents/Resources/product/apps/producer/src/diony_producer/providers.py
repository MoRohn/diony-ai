"""Provider-neutral generation adapters for the launch control plane.

The cloud engines the Director shoots on are registered by default; explicit
BYOK adapters may be added at runtime.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from diony_contracts.launch import CostBreakdown, GenerationJob, ProviderCapability


class ProviderUnavailable(RuntimeError):
    pass


class ProviderPending(RuntimeError):
    """The signed-site engine has not attached durable completion proof yet."""

    pass


class ProviderSpec(BaseModel):
    id: str
    label: str
    capabilities: list[ProviderCapability]
    models: list[str] = Field(default_factory=list)
    # A Diony default engine (the Director's cloud providers and its own
    # assembly) as opposed to a BYOK adapter added at runtime; workflows run
    # on defaults unless a run's config lifts `require_local`.
    local: bool = True
    async_provider: bool = False
    enabled: bool = True
    priority: int = 100
    internal_credits_per_unit: int = 0
    health: str = "ready"
    transparency: str = "Inputs, model, cost, and outputs are recorded."


class ProviderResult(BaseModel):
    external_job_id: str = ""
    output_asset_ids: list[str] = Field(default_factory=list)
    cost_breakdown: CostBreakdown | None = None
    internal_credits_used: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderAdapter(Protocol):
    spec: ProviderSpec

    def invoke(self, job: GenerationJob, payload: dict[str, Any]) -> ProviderResult: ...

    def cancel(self, external_job_id: str) -> None: ...


class LocalCheckpointProvider:
    """Records control-plane stages whose heavy work is owned by the engines.

    The adapter never invents media. Output asset IDs are attached by the
    Director; this adapter only checkpoints the orchestration stages.
    """

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    def invoke(self, job: GenerationJob, payload: dict[str, Any]) -> ProviderResult:
        if payload.get("checkpoint_complete") is not True:
            raise ProviderPending(
                f"{self.spec.label} is waiting for its signed-site engine checkpoint"
            )
        return ProviderResult(
            external_job_id=f"local:{job.id}",
            output_asset_ids=list(payload.get("output_asset_ids") or []),
            cost_breakdown=(
                CostBreakdown.model_validate(payload["cost_breakdown"])
                if payload.get("cost_breakdown") is not None
                else None
            ),
            internal_credits_used=(
                int(payload["actual_internal_credits"])
                if payload.get("actual_internal_credits") is not None
                else None
            ),
            metadata={
                "site_owned": True,
                "checkpoint": True,
                "engine": self.spec.id,
                **dict(payload.get("metadata") or {}),
            },
        )

    def cancel(self, external_job_id: str) -> None:
        return None


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    def register(self, provider: ProviderAdapter) -> None:
        if provider.spec.id in self._providers:
            raise ValueError(f"Provider {provider.spec.id} is already registered")
        self._providers[provider.spec.id] = provider

    def providers(self, capability: ProviderCapability | None = None) -> list[ProviderAdapter]:
        rows = [provider for provider in self._providers.values() if provider.spec.enabled]
        if capability is not None:
            rows = [provider for provider in rows if capability in provider.spec.capabilities]
        return sorted(
            rows,
            key=lambda provider: (
                not provider.spec.local,
                provider.spec.priority,
                provider.spec.id,
            ),
        )

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            provider = self._providers[provider_id]
        except KeyError as exc:
            raise ProviderUnavailable(f"Provider {provider_id} is not registered") from exc
        if not provider.spec.enabled:
            raise ProviderUnavailable(f"Provider {provider_id} is disabled")
        return provider

    def select(
        self,
        capability: ProviderCapability,
        *,
        preferred: str | None = None,
        require_local: bool = False,
    ) -> ProviderAdapter:
        chain = self.chain(capability, preferred=preferred, require_local=require_local)
        if not chain:
            raise ProviderUnavailable(
                f"No {capability.value} provider is configured. Configure a local engine or an explicit BYOK adapter."
            )
        return chain[0]

    def chain(
        self,
        capability: ProviderCapability,
        *,
        preferred: str | None = None,
        require_local: bool = False,
    ) -> list[ProviderAdapter]:
        rows = self.providers(capability)
        if require_local:
            rows = [provider for provider in rows if provider.spec.local]
        if preferred:
            rows.sort(key=lambda provider: provider.spec.id != preferred)
        return rows

    def catalog(self) -> list[dict[str, Any]]:
        return [provider.spec.model_dump(mode="json") for provider in self.providers()]

    @classmethod
    def with_diony_defaults(cls) -> ProviderRegistry:
        """The engines the Director shoots on, as control-plane providers.

        These adapters checkpoint orchestration stages; the Director's own
        provider clients (`diony_director.providers`) make the API calls and
        attach the media. Nothing here renders a frame."""
        registry = cls()
        defaults = [
            ProviderSpec(
                id="grok",
                label="Grok Imagine (xAI)",
                capabilities=[ProviderCapability.VIDEO, ProviderCapability.LLM],
                models=[
                    "grok-imagine-video-1.5",
                    "grok-imagine-video",
                    "grok-imagine-image-2.0",
                    "grok-4.6",
                ],
                async_provider=True,
                priority=10,
                transparency="Metered per second by xAI; prompts, references, and receipts stay in the vault.",
            ),
            ProviderSpec(
                id="google-flow",
                label="Google Flow via useapi.net",
                capabilities=[ProviderCapability.VIDEO],
                models=[
                    "omni-flash",
                    "veo-3.1-lite",
                    "veo-3.1-fast",
                    "veo-3.1-quality",
                    "nano-banana-2",
                ],
                async_provider=True,
                priority=20,
                transparency="Charged in the Google AI plan's credits; dollars are modeled from the declared plan.",
            ),
            ProviderSpec(
                id="diony.assembly",
                label="Diony assembly",
                capabilities=[ProviderCapability.ASSEMBLY],
                models=["ffmpeg-conform-v1"],
                priority=10,
                transparency="The cut, the loudness lock, and the narration mix run on this desk with ffmpeg.",
            ),
        ]
        for spec in defaults:
            registry.register(LocalCheckpointProvider(spec))
        return registry
