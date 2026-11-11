"""Martian Guide – science sampling, spectral analysis and the science database.

The pipeline mirrors real practice:

1. **context** – the outcrop is scanned before drilling,
2. **sample** – a core is drilled and sealed with a site and coordinates,
3. **analysis** – a deterministic spectrometer model produces oxide weight
   percentages, which are matched against curated mineral signatures,
4. **record** – the interpretation is stored with its provenance.

Every generated number is labelled ``simulated``. The mineral ranges are
educational simplifications inspired by published NASA results, never
measurements of an actual sample.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidInputError, SampleNotFoundError
from app.core.logging import get_logger
from app.core.utils import clamp, new_id, round_to, seeded_rng, utcnow
from app.db.repositories import ScienceSampleRepository
from app.db.session import Database
from app.domain.state import GameState
from app.missions.registry import GameContent

logger = get_logger("nova.science")

#: Oxides reported by the simplified spectrometer, in display order.
OXIDES = ("sio2", "feo", "mgo", "al2o3", "so3")


class ScienceService:
    def __init__(self, database: Database, content: GameContent) -> None:
        self.db = database
        self.content = content
        self._dataset: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    @property
    def dataset(self) -> dict[str, Any]:
        if self._dataset is None:
            self._dataset = self.content.read_json("science/mars_minerals.json")
        return self._dataset

    def minerals(self) -> list[dict[str, Any]]:
        return list(self.dataset["minerals"])

    def sites(self) -> list[dict[str, Any]]:
        return list(self.dataset["sites"])

    def site(self, site_id: str) -> dict[str, Any] | None:
        return next((s for s in self.sites() if s["id"] == site_id), None)

    def mineral(self, mineral_id: str) -> dict[str, Any] | None:
        return next((m for m in self.minerals() if m["id"] == mineral_id), None)

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------
    def choose_site(self, state: GameState) -> dict[str, Any]:
        """Pick the sampling site from the player's chosen base site."""
        base_site = str(state.world.get("base_site") or "")
        mapping = {
            "jezero_ridge": "jezero_delta",
            "delta_flats": "jezero_delta",
            "crater_rim": "valles_marineris",
        }
        site = self.site(mapping.get(base_site, "jezero_delta"))
        return site or self.sites()[0]

    def generate_composition(self, state: GameState, mineral_id: str, sample_id: str) -> dict[str, float]:
        """Deterministic oxide percentages inside the mineral's curated range."""
        mineral = self.mineral(mineral_id)
        if mineral is None:  # pragma: no cover - guarded by weighted choice
            raise InvalidInputError(f"Unknown mineral '{mineral_id}'.")
        rng = seeded_rng(state.seed, sample_id, mineral_id)
        composition: dict[str, float] = {}
        for oxide in OXIDES:
            band = mineral["signature"].get(oxide)
            if band is None:
                composition[oxide] = round_to(rng.uniform(0.2, 2.0), 2)
            else:
                low, high = float(band[0]), float(band[1])
                composition[oxide] = round_to(rng.uniform(low, high), 2)
        total = sum(composition.values())
        if total > 100.0:  # normalise to a plausible oxide total
            composition = {k: round_to(v * 100.0 / total, 2) for k, v in composition.items()}
        return composition

    def pick_mineral(self, state: GameState, site: dict[str, Any], sample_id: str) -> str:
        """Weighted draw from the site's mineral assemblage."""
        rng = seeded_rng(state.seed, site["id"], sample_id)
        weights: dict[str, float] = site.get("weights", {})
        if not weights:
            return "basalt"
        total = sum(weights.values())
        threshold = rng.uniform(0, total)
        cumulative = 0.0
        for mineral_id, weight in weights.items():
            cumulative += weight
            if threshold <= cumulative:
                return mineral_id
        return next(iter(weights))

    async def collect(
        self,
        state: GameState,
        *,
        mission_id: str,
        repository: ScienceSampleRepository,
        note: str = "",
    ) -> dict[str, Any]:
        """Drill a core: create the sample row and return its public record."""
        site = self.choose_site(state)
        sample_id = new_id("smp")
        mineral_id = self.pick_mineral(state, site, sample_id)
        composition = self.generate_composition(state, mineral_id, sample_id)

        payload = {
            "sample_id": sample_id,
            "game_id": state.game_id,
            "mission_id": mission_id,
            "site": site["id"],
            "rock_type": "unanalysed",
            "composition": composition,
            "analysis": {
                "status": "pending",
                "provenance": "simulated",
                "site_name": site["name"],
                "coordinates": site["coordinates"],
                "spectral_bands_hint": "Run the spectrometer to classify this core.",
            },
            "notes": note,
        }
        await repository.create(payload)
        state.sample_ids.append(sample_id)
        logger.info("science.sample_collected", extra={"game_id": state.game_id})
        return {
            "sample_id": sample_id,
            "site": site["id"],
            "site_name": site["name"],
            "coordinates": site["coordinates"],
            "composition": composition,
            "status": "pending",
            "provenance": "simulated",
            "disclaimer": self.dataset["disclaimer"],
        }

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def classify(self, composition: dict[str, float]) -> dict[str, Any]:
        """Score every curated mineral against the measured composition."""
        scored: list[dict[str, Any]] = []
        for mineral in self.minerals():
            signature: dict[str, list[float]] = mineral["signature"]
            hits = 0
            deviations: list[float] = []
            for oxide, band in signature.items():
                value = float(composition.get(oxide, 0.0))
                low, high = float(band[0]), float(band[1])
                if low <= value <= high:
                    hits += 1
                    deviations.append(0.0)
                else:
                    distance = low - value if value < low else value - high
                    span = max(high - low, 1.0)
                    deviations.append(clamp(distance / span, 0.0, 3.0))
            coverage = hits / max(1, len(signature))
            penalty = sum(deviations) / max(1, len(signature))
            confidence = clamp(coverage - 0.28 * penalty, 0.0, 1.0)
            scored.append(
                {
                    "mineral_id": mineral["id"],
                    "name": mineral["name"],
                    "confidence_pct": round_to(100 * confidence, 2),
                    "oxides_in_range": hits,
                    "oxides_checked": len(signature),
                    "spectral_bands": mineral["spectral_bands"],
                    "habitability": mineral["habitability"],
                    "interpretation": mineral["interpretation"],
                    "source": mineral["source"],
                    "url": mineral.get("url"),
                }
            )
        scored.sort(key=lambda entry: entry["confidence_pct"], reverse=True)
        best = scored[0]
        return {
            "rock_type": best["mineral_id"],
            "confidence_pct": best["confidence_pct"],
            "best_match": best,
            "candidates": scored[:3],
            "habitability": best["habitability"],
            "interpretation": best["interpretation"],
            "spectral_bands": best["spectral_bands"],
            "source": best["source"],
            "url": best.get("url"),
            "provenance": "simulated",
            "method": "Oxide-range matching against curated NASA-derived mineral signatures.",
            "disclaimer": self.dataset["disclaimer"],
        }

    async def analyse(
        self,
        state: GameState,
        sample_id: str,
        *,
        repository: ScienceSampleRepository,
    ) -> dict[str, Any]:
        row = await repository.get(sample_id)
        if row is None or row.game_id != state.game_id:
            raise SampleNotFoundError(
                f"Sample '{sample_id}' not found in this game.", sample_id=sample_id
            )
        composition = dict(row.composition or {})
        analysis = self.classify(composition)
        analysis["status"] = "analysed"
        analysis["sample_id"] = sample_id
        analysis["analysed_at"] = utcnow().isoformat()
        analysis["spectrometer_condition"] = (
            state.equipment["spectrometer"].condition if "spectrometer" in state.equipment else 100
        )
        # A worn spectrometer degrades confidence – maintenance matters.
        condition = analysis["spectrometer_condition"]
        if condition < 60:
            factor = 0.6 + 0.4 * (condition / 60.0)
            analysis["confidence_pct"] = round_to(analysis["confidence_pct"] * factor, 2)
            analysis["calibration_warning"] = (
                f"Spectrometer at {condition}% condition: confidence reduced. Service the tool."
            )
        await repository.analyse(sample_id, analysis, analysis["rock_type"])
        return analysis

    async def record_finding(
        self,
        state: GameState,
        sample_id: str,
        *,
        repository: ScienceSampleRepository,
        note: str = "",
    ) -> dict[str, Any]:
        row = await repository.get(sample_id)
        if row is None or row.game_id != state.game_id:
            raise SampleNotFoundError(
                f"Sample '{sample_id}' not found in this game.", sample_id=sample_id
            )
        analysis = dict(row.analysis or {})
        if analysis.get("status") != "analysed":
            raise InvalidInputError("Analyse the sample before logging the finding.")
        analysis["logged"] = True
        analysis["logged_at"] = utcnow().isoformat()
        analysis["player_note"] = note
        await repository.analyse(sample_id, analysis, row.rock_type)
        return {
            "sample_id": sample_id,
            "logged": True,
            "rock_type": row.rock_type,
            "confidence_pct": analysis.get("confidence_pct", 0.0),
            "note": note,
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_samples(self, game_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            return await ScienceSampleRepository(session).list(game_id)

    async def get_sample(self, game_id: str, sample_id: str) -> dict[str, Any]:
        async with self.db.session() as session:
            row = await ScienceSampleRepository(session).get(sample_id)
        if row is None or row.game_id != game_id:
            raise SampleNotFoundError(
                f"Sample '{sample_id}' not found in this game.", sample_id=sample_id
            )
        return ScienceSampleRepository._serialise(row)

    async def latest_sample_id(self, game_id: str) -> str | None:
        samples = await self.list_samples(game_id)
        return samples[0]["sample_id"] if samples else None

    async def database_view(self, game_id: str) -> dict[str, Any]:
        """The in-game science database: samples grouped by rock type."""
        samples = await self.list_samples(game_id)
        by_type: dict[str, int] = {}
        for sample in samples:
            key = sample["rock_type"]
            by_type[key] = by_type.get(key, 0) + 1
        analysed = [s for s in samples if (s.get("analysis") or {}).get("status") == "analysed"]
        return {
            "game_id": game_id,
            "samples_total": len(samples),
            "samples_analysed": len(analysed),
            "by_rock_type": by_type,
            "samples": samples,
            "mineral_reference": self.minerals(),
            "sites": self.sites(),
            "disclaimer": self.dataset["disclaimer"],
        }