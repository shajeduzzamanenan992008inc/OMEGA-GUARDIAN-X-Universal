"""Engine registry and capability negotiation."""

from __future__ import annotations

from typing import Iterable

from ..models import ProjectProfile
from .android import AndroidEngine
from .contract import BaseEngine, EngineContext, EngineResult, CAPABILITY_NAMES
from .language import all_language_engines


class EngineRegistry:
    def __init__(self, engines: Iterable[BaseEngine] | None = None):
        self._engines: dict[str, BaseEngine] = {}
        for engine in engines or ():
            self.register(engine)
        if engines is None:
            for engine in default_engines():
                self.register(engine)

    def register(self, engine: BaseEngine, *, replace: bool = False) -> None:
        if not engine.engine_id or engine.engine_id == "base":
            raise ValueError("Engine must declare a stable engine_id.")
        if engine.engine_id in self._engines and not replace:
            raise ValueError(f"Duplicate engine_id: {engine.engine_id}")
        self._engines[engine.engine_id] = engine

    def get(self, engine_id: str) -> BaseEngine:
        try:
            return self._engines[engine_id]
        except KeyError as exc:
            raise KeyError(f"Unknown engine: {engine_id}") from exc

    def all(self) -> list[BaseEngine]:
        return [self._engines[key] for key in sorted(self._engines)]

    def select(self, engine_ids: Iterable[str] | None = None) -> list[BaseEngine]:
        if not engine_ids:
            return self.all()
        selected: list[BaseEngine] = []
        for engine_id in engine_ids:
            selected.append(self.get(engine_id))
        return sorted({engine.engine_id: engine for engine in selected}.values(), key=lambda item: item.engine_id)

    def detected(self, context: EngineContext, *, engine_ids: Iterable[str] | None = None) -> list[BaseEngine]:
        result: list[BaseEngine] = []
        for engine in self.select(engine_ids):
            try:
                if engine.detect(context):
                    result.append(engine)
            except Exception:
                # Detection failure is reported during evaluate; never crash the registry.
                continue
        return result

    def evaluate(self, context: EngineContext, *, engine_ids: Iterable[str] | None = None) -> list[EngineResult]:
        return [engine.evaluate(context) for engine in self.select(engine_ids)]

    def health(self) -> list[dict]:
        return [engine.health() for engine in self.all()]

    def capability_matrix(self) -> dict[str, dict[str, str]]:
        matrix: dict[str, dict[str, str]] = {}
        for engine in self.all():
            matrix[engine.engine_id] = {name: "declared_at_runtime" for name in CAPABILITY_NAMES}
        return matrix


def default_engines() -> list[BaseEngine]:
    engines: list[BaseEngine] = [AndroidEngine()]
    engines.extend(all_language_engines())
    return engines


def language_engine_ids() -> list[str]:
    return [engine.engine_id for engine in all_language_engines()]


def profile_execution_plan(profile: ProjectProfile, registry: EngineRegistry) -> dict[str, list[str]]:
    """Map detected languages to candidate engine IDs without executing them."""
    plan: dict[str, list[str]] = {}
    for language in profile.languages:
        normalized = language.lower()
        if "javascript" in normalized:
            plan[language] = ["lang-javascript", "lang-typescript"]
        elif "kotlin" in normalized:
            plan[language] = ["lang-kotlin", "android"]
        elif "java" in normalized:
            plan[language] = ["lang-java"]
        elif "c#" in normalized or ".net" in normalized:
            plan[language] = ["lang-csharp"]
        elif "c/c++" in normalized:
            plan[language] = ["lang-c", "lang-cpp"]
        else:
            plan[language] = [f"lang-{normalized.replace(' ', '-').replace('/', '-')}"]
    known = {engine.engine_id for engine in registry.all()}
    return {language: [engine_id for engine_id in ids if engine_id in known] for language, ids in plan.items()}
