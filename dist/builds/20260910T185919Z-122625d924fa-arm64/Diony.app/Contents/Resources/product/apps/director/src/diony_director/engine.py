"""The engine flag: cloud is the engine; `local_legacy` only makes paid runs refuse."""

from __future__ import annotations

from diony_contracts.director import EngineMode


def engine_mode() -> EngineMode:
    from diony_contracts.cluster import engine_is_cloud

    return EngineMode.CLOUD if engine_is_cloud() else EngineMode.LOCAL_LEGACY


def cloud_primary() -> bool:
    return engine_mode() is EngineMode.CLOUD
