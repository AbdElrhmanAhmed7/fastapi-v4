"""Earth Trends – change detection over bundled or live NASA-style datasets.

The analysis engine is data-source agnostic: it takes two periods of gridded
values and produces statistics, per-cell deltas, hotspots and a narrative
summary. Live data (NASA POWER) can be plugged in by the Earth service; when
it is unavailable the bundled, clearly-labelled dataset is used instead.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from app.core.utils import clamp, round_to, seeded_rng


class GridCell(BaseModel):
    row: int
    col: int
    lat: float
    lon: float
    value: float


class PeriodGrid(BaseModel):
    region_id: str
    layer_id: str
    period: str
    unit: str
    cells: list[GridCell] = Field(default_factory=list)
    provenance: str = "bundled"
    source: str = ""

    def values(self) -> list[float]:
        return [cell.value for cell in self.cells]

    def mean(self) -> float:
        values = self.values()
        return round_to(sum(values) / len(values), 4) if values else 0.0


class LayerSpec(BaseModel):
    id: str
    name: dict[str, str]
    unit: str
    description: dict[str, str]
    baseline: float
    spread: float
    trend_per_decade: float
    higher_is_better: bool = True
    nasa_product: str
    nasa_url: str
    live_parameters: list[str] = Field(default_factory=list)


class RegionSpec(BaseModel):
    id: str
    name: dict[str, str]
    bbox: list[float]  # [lon_min, lat_min, lon_max, lat_max]
    biome: str
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def centre(self) -> tuple[float, float]:
        lon_min, lat_min, lon_max, lat_max = self.bbox
        return (round_to((lat_min + lat_max) / 2, 4), round_to((lon_min + lon_max) / 2, 4))


def synthesise_grid(
    region: RegionSpec,
    layer: LayerSpec,
    period: str,
    *,
    grid_size: int = 8,
    seed: int = 7,
) -> PeriodGrid:
    """Deterministic, plausible grid used when live data is unavailable.

    Values are **simulated**: they follow the documented long-term trend of the
    layer but are not measurements. Responses always say so.
    """
    rng = seeded_rng(region.id, layer.id, period, seed)
    lon_min, lat_min, lon_max, lat_max = region.bbox
    start_year = int(period.split("-")[0])
    decades = (start_year - 2000) / 10.0
    trend_offset = layer.trend_per_decade * decades

    cells: list[GridCell] = []
    for row in range(grid_size):
        for col in range(grid_size):
            lat = lat_min + (lat_max - lat_min) * (row + 0.5) / grid_size
            lon = lon_min + (lon_max - lon_min) * (col + 0.5) / grid_size
            # Smooth spatial structure + local noise + long term trend.
            spatial = (
                math.sin((row + 1) / grid_size * math.pi) * math.cos((col + 1) / grid_size * math.pi)
            ) * layer.spread
            noise = rng.gauss(0, layer.spread * 0.35)
            gradient = (row - grid_size / 2) / grid_size * layer.spread * 0.6
            value = layer.baseline + trend_offset + spatial + noise + gradient
            if layer.unit == "index":
                value = clamp(value, -1.0, 1.0)
            cells.append(
                GridCell(row=row, col=col, lat=round_to(lat, 4), lon=round_to(lon, 4), value=round_to(value, 4))
            )

    return PeriodGrid(
        region_id=region.id,
        layer_id=layer.id,
        period=period,
        unit=layer.unit,
        cells=cells,
        provenance="simulated",
        source=f"Bundled educational model of {layer.nasa_product}",
    )


def grid_from_points(
    region: RegionSpec,
    layer: LayerSpec,
    period: str,
    values: list[float],
    *,
    provenance: str = "live",
    source: str = "",
) -> PeriodGrid:
    """Wrap a flat list of real observations into a 1-column grid."""
    lat, lon = region.centre
    cells = [
        GridCell(row=index, col=0, lat=lat, lon=lon, value=round_to(value, 4))
        for index, value in enumerate(values)
    ]
    return PeriodGrid(
        region_id=region.id,
        layer_id=layer.id,
        period=period,
        unit=layer.unit,
        cells=cells,
        provenance=provenance,
        source=source,
    )


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return {
        "mean": round_to(mean, 4),
        "min": round_to(min(values), 4),
        "max": round_to(max(values), 4),
        "std": round_to(math.sqrt(variance), 4),
    }


def compare_periods(
    before: PeriodGrid,
    after: PeriodGrid,
    layer: LayerSpec,
    *,
    hotspot_count: int = 5,
) -> dict[str, Any]:
    """Compare two grids cell-by-cell and summarise the change."""
    before_map = {(c.row, c.col): c for c in before.cells}
    deltas: list[dict[str, Any]] = []
    for cell in after.cells:
        previous = before_map.get((cell.row, cell.col))
        if previous is None:
            continue
        delta = cell.value - previous.value
        deltas.append(
            {
                "row": cell.row,
                "col": cell.col,
                "lat": cell.lat,
                "lon": cell.lon,
                "before": round_to(previous.value, 4),
                "after": round_to(cell.value, 4),
                "delta": round_to(delta, 4),
                "delta_pct": round_to(100 * delta / abs(previous.value), 2) if previous.value else 0.0,
            }
        )

    stats_before, stats_after = _stats(before.values()), _stats(after.values())
    mean_delta = round_to(stats_after["mean"] - stats_before["mean"], 4)
    mean_delta_pct = (
        round_to(100 * mean_delta / abs(stats_before["mean"]), 2) if stats_before["mean"] else 0.0
    )

    direction = "increase" if mean_delta > 0 else "decrease" if mean_delta < 0 else "stable"
    threshold = max(abs(layer.spread) * 0.15, 1e-6)
    if abs(mean_delta) < threshold:
        verdict = "stable"
    elif (mean_delta > 0) == layer.higher_is_better:
        verdict = "improving"
    else:
        verdict = "degrading"

    ranked = sorted(deltas, key=lambda d: abs(d["delta"]), reverse=True)
    improved = len([d for d in deltas if d["delta"] > threshold])
    degraded = len([d for d in deltas if d["delta"] < -threshold])

    return {
        "layer": layer.id,
        "unit": layer.unit,
        "period_before": before.period,
        "period_after": after.period,
        "provenance": after.provenance if after.provenance == before.provenance else "mixed",
        "sources": sorted({before.source, after.source} - {""}),
        "statistics": {"before": stats_before, "after": stats_after},
        "mean_delta": mean_delta,
        "mean_delta_pct": mean_delta_pct,
        "direction": direction,
        "verdict": verdict,
        "cells_changed": len(deltas),
        "cells_improved": improved,
        "cells_degraded": degraded,
        "hotspots": ranked[:hotspot_count],
        "grid_before": [c.model_dump(mode="json") for c in before.cells],
        "grid_after": [c.model_dump(mode="json") for c in after.cells],
        "nasa_product": layer.nasa_product,
        "nasa_url": layer.nasa_url,
    }


def narrate(region: RegionSpec, layer: LayerSpec, analysis: dict[str, Any]) -> dict[str, str]:
    delta = analysis["mean_delta"]
    pct = analysis["mean_delta_pct"]
    verdict = analysis["verdict"]
    en = (
        f"{region.name['en']}: {layer.name['en']} changed by {delta:+} {layer.unit} "
        f"({pct:+}%) between {analysis['period_before']} and {analysis['period_after']}. "
        f"Trend classified as '{verdict}'. {analysis['cells_degraded']} of "
        f"{analysis['cells_changed']} cells moved against the desirable direction."
    )
    ar = (
        f"{region.name.get('ar', region.name['en'])}: تغير {layer.name.get('ar', layer.name['en'])} "
        f"بمقدار {delta:+} {layer.unit} ({pct:+}%) بين {analysis['period_before']} و{analysis['period_after']}. "
        f"التصنيف: '{verdict}'. عدد الخلايا التي تحركت في الاتجاه غير المرغوب: "
        f"{analysis['cells_degraded']} من {analysis['cells_changed']}."
    )
    return {"en": en, "ar": ar}
