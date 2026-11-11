"""Earth Trends – region/layer selection, change detection and report archiving.

Data honesty is a feature, not a footnote:

* every layer names its NASA product and URL,
* live NASA POWER data (no API key needed) is labelled ``live`` / ``cache``,
* when live data is unavailable the bundled educational model is used and
  labelled ``simulated`` – the response says why.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.errors import ExternalServiceError, InvalidInputError, ReportNotFoundError
from app.core.logging import get_logger
from app.core.utils import clamp, new_id, round_to
from app.db.repositories import EarthReportRepository
from app.db.session import Database
from app.missions.registry import GameContent
from app.simulations.earth_trends import (
    LayerSpec,
    PeriodGrid,
    RegionSpec,
    compare_periods,
    grid_from_points,
    narrate,
    synthesise_grid,
)

logger = get_logger("nova.earth")

POWER_FILL_VALUE = -999.0
MONTHS = tuple(range(1, 13))


class EarthService:
    def __init__(
        self,
        database: Database,
        content: GameContent,
        settings: Settings,
        *,
        nasa: Any | None = None,
    ) -> None:
        self.db = database
        self.content = content
        self.settings = settings
        self.nasa = nasa
        self._dataset: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    @property
    def dataset(self) -> dict[str, Any]:
        if self._dataset is None:
            self._dataset = self.content.read_json("earth/datasets.json")
        return self._dataset

    def regions(self) -> list[RegionSpec]:
        return [RegionSpec(**raw) for raw in self.dataset["regions"]]

    def layers(self) -> list[LayerSpec]:
        return [LayerSpec(**raw) for raw in self.dataset["layers"]]

    def periods(self) -> list[str]:
        return list(self.dataset["periods"])

    def region(self, region_id: str) -> RegionSpec:
        region = next((r for r in self.regions() if r.id == region_id), None)
        if region is None:
            raise InvalidInputError(
                f"Unknown region '{region_id}'.", available=[r.id for r in self.regions()]
            )
        return region

    def layer(self, layer_id: str) -> LayerSpec:
        layer = next((item for item in self.layers() if item.id == layer_id), None)
        if layer is None:
            raise InvalidInputError(
                f"Unknown layer '{layer_id}'.", available=[item.id for item in self.layers()]
            )
        return layer

    def catalogue(self) -> dict[str, Any]:
        return {
            "regions": [r.model_dump(mode="json") for r in self.regions()],
            "layers": [item.model_dump(mode="json") for item in self.layers()],
            "periods": self.periods(),
            "default_comparison": self.dataset["default_comparison"],
            "live_capable_layers": [item.id for item in self.layers() if item.live_parameters],
            "note": {
                "en": "Layers with live_capable=true can query NASA POWER monthly climate data; others use a clearly-labelled educational model.",
                "ar": "الطبقات التي تدعم البيانات الحية تقدر تطلب بيانات NASA POWER الشهرية؛ الباقي نموذج تعليمي موضح بوضوح.",
            },
        }

    # ------------------------------------------------------------------
    # Grids
    # ------------------------------------------------------------------
    @staticmethod
    def _period_years(period: str) -> tuple[int, int]:
        try:
            start, end = period.split("-")
            return int(start), int(end)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid period '{period}'. Expected 'YYYY-YYYY'.") from exc

    def _validate_period(self, period: str) -> str:
        if period not in self.periods():
            raise InvalidInputError(f"Unknown period '{period}'.", available=self.periods())
        return period

    async def _live_grid(
        self, region: RegionSpec, layer: LayerSpec, period: str
    ) -> PeriodGrid | None:
        """Monthly climatology for a period from NASA POWER, or None."""
        if self.nasa is None or not layer.live_parameters:
            return None
        start_year, end_year = self._period_years(period)
        lat, lon = region.centre
        try:
            response = await self.nasa.power_monthly(
                latitude=lat,
                longitude=lon,
                parameters=layer.live_parameters,
                start_year=start_year,
                end_year=end_year,
            )
        except ExternalServiceError as exc:
            logger.warning("earth.live_unavailable", extra={"error": str(exc)})
            return None

        parameter = layer.live_parameters[0]
        series = (
            response.get("data", {})
            .get("properties", {})
            .get("parameter", {})
            .get(parameter, {})
        )
        if not series:
            return None

        monthly: list[float] = []
        for month in MONTHS:
            values = [
                float(series[key])
                for key in series
                if key.endswith(f"{month:02d}")
                and float(series[key]) > POWER_FILL_VALUE
                and start_year <= int(key[:4]) <= end_year
            ]
            monthly.append(round_to(sum(values) / len(values), 4) if values else float("nan"))
        valid = [value for value in monthly if value == value]  # filter NaN
        if not valid:
            return None
        mean = sum(valid) / len(valid)
        monthly = [value if value == value else round_to(mean, 4) for value in monthly]

        return grid_from_points(
            region,
            layer,
            period,
            monthly,
            provenance=response.get("provenance", "live"),
            source=f"{layer.nasa_product} via NASA POWER ({parameter})",
        )

    async def grid(
        self,
        region: RegionSpec,
        layer: LayerSpec,
        period: str,
        *,
        live: bool = False,
        grid_size: int = 8,
        seed: int = 7,
    ) -> PeriodGrid:
        self._validate_period(period)
        if live:
            grid = await self._live_grid(region, layer, period)
            if grid is not None:
                return grid
        return synthesise_grid(region, layer, period, grid_size=grid_size, seed=seed)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    async def compare(
        self,
        *,
        region_id: str,
        layer_id: str,
        period_before: str,
        period_after: str,
        live: bool = False,
        grid_size: int = 8,
        seed: int = 7,
    ) -> dict[str, Any]:
        region, layer = self.region(region_id), self.layer(layer_id)
        self._validate_period(period_before)
        self._validate_period(period_after)
        if period_before == period_after:
            raise InvalidInputError("Choose two different periods to compare.")
        size = int(clamp(grid_size, 4, 16))

        before = await self.grid(region, layer, period_before, live=live, grid_size=size, seed=seed)
        after = await self.grid(region, layer, period_after, live=live, grid_size=size, seed=seed)
        analysis = compare_periods(before, after, layer)
        analysis["region"] = region.id
        analysis["region_name"] = region.name
        analysis["layer_name"] = layer.name
        analysis["biome"] = region.biome
        analysis["requested_live"] = bool(live)
        analysis["live_supported"] = bool(layer.live_parameters)
        if live and analysis["provenance"] == "simulated":
            analysis["live_fallback_reason"] = (
                "NASA POWER data was unavailable for this region/layer; the bundled educational model was used."
            )
        analysis["narrative"] = narrate(region, layer, analysis)
        return analysis

    @staticmethod
    def summarise(analysis: dict[str, Any]) -> dict[str, Any]:
        """Compact form stored inside the save document (no full grids)."""
        keys = (
            "region",
            "layer",
            "unit",
            "period_before",
            "period_after",
            "provenance",
            "mean_delta",
            "mean_delta_pct",
            "direction",
            "verdict",
            "cells_changed",
            "cells_improved",
            "cells_degraded",
            "nasa_product",
            "nasa_url",
        )
        summary = {key: analysis[key] for key in keys if key in analysis}
        summary["hotspots"] = analysis.get("hotspots", [])[:3]
        summary["narrative"] = analysis.get("narrative", {})
        return summary

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    async def save_report(
        self,
        game_id: str,
        analysis: dict[str, Any],
        *,
        repository: EarthReportRepository,
        title: str = "",
    ) -> dict[str, Any]:
        report_id = new_id("earth")
        payload = {
            "report_id": report_id,
            "game_id": game_id,
            "region_id": analysis["region"],
            "layer_id": analysis["layer"],
            "period_before": analysis["period_before"],
            "period_after": analysis["period_after"],
            "provenance": analysis["provenance"],
            "verdict": analysis["verdict"],
            "mean_delta": float(analysis["mean_delta"]),
            "analysis": {**analysis, "title": title or f"{analysis['region']} – {analysis['layer']}"},
        }
        await repository.create(payload)
        return {
            "report_id": report_id,
            "region_id": payload["region_id"],
            "layer_id": payload["layer_id"],
            "verdict": payload["verdict"],
            "mean_delta": payload["mean_delta"],
            "provenance": payload["provenance"],
        }

    async def list_reports(self, game_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            return await EarthReportRepository(session).list(game_id)

    async def get_report(self, game_id: str, report_id: str) -> dict[str, Any]:
        async with self.db.session() as session:
            report = await EarthReportRepository(session).get(report_id)
        if report is None or report["game_id"] != game_id:
            raise ReportNotFoundError(
                f"Earth report '{report_id}' not found.", report_id=report_id
            )
        return report