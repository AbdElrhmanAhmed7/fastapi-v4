"""Mission side effects that need the database (science samples, Earth reports).

The mission engine stays pure: it validates, mutates the save document and
emits events. Anything that must also write a *queryable* row is handled here,
inside the same transaction, right after the action succeeded.

The mapping is explicit so designers can see exactly which action produces
which record.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.services.game_service import Transaction

logger = get_logger("nova.hooks")


class ActionHooks:
    def __init__(self, games: Any) -> None:
        self.games = games
        self._registry: dict[tuple[str, str], Callable[..., Awaitable[dict[str, Any]]]] = {
            ("martian_guide", "collect_sample"): self._collect_sample,
            ("martian_guide", "collect_extra_sample"): self._collect_sample,
            ("martian_guide", "analyse_sample"): self._analyse_sample,
            ("martian_guide", "log_findings"): self._log_findings,
            ("earth_trends", "compare_periods"): self._compare_periods,
            ("earth_trends", "save_report"): self._save_report,
        }

    async def after_action(
        self, tx: Transaction, mission_id: str, action: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        handler = self._registry.get((mission_id, action))
        if handler is None:
            return {}
        payload = tx.state.mission(mission_id).data.get(f"{action}_payload", {})
        try:
            return await handler(tx, mission_id, payload, result)
        except Exception:  # pragma: no cover - defensive: never lose a valid action
            logger.exception(
                "hook.failed", extra={"mission_id": mission_id, "action": action}
            )
            return {"warning": f"Side effect for '{action}' could not be completed."}

    # ------------------------------------------------------------------
    # Martian Guide
    # ------------------------------------------------------------------
    async def _collect_sample(
        self, tx: Transaction, mission_id: str, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        sample = await self.games.science.collect(
            tx.state, mission_id=mission_id, repository=tx.samples
        )
        progress = tx.state.mission(mission_id)
        progress.data["last_sample_id"] = sample["sample_id"]
        tx.state.world["geology.last_sample_id"] = sample["sample_id"]
        tx.recorder.emit("science.sample_collected", sample, mission_id=mission_id)
        result["message"] = (
            f"{result.get('message', '')} Sample {sample['sample_id']} sealed at {sample['site']}."
        ).strip()
        return {"sample": sample}

    async def _analyse_sample(
        self, tx: Transaction, mission_id: str, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        progress = tx.state.mission(mission_id)
        sample_id = progress.data.get("last_sample_id") or await self.games.science.latest_sample_id(
            tx.state.game_id
        )
        if not sample_id:
            return {"warning": "No sample in the chamber."}
        analysis = await self.games.science.analyse(tx.state, sample_id, repository=tx.samples)
        progress.data["sample_analysis"] = {
            key: analysis[key]
            for key in ("sample_id", "rock_type", "confidence_pct", "habitability", "provenance")
            if key in analysis
        }
        tx.state.world["geology.last_rock_type"] = analysis["rock_type"]
        tx.recorder.emit("science.sample_analysed", analysis, mission_id=mission_id)
        return {"analysis": analysis}

    async def _log_findings(
        self, tx: Transaction, mission_id: str, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        progress = tx.state.mission(mission_id)
        sample_id = progress.data.get("last_sample_id")
        if not sample_id:
            return {"warning": "Nothing to log."}
        entry = await self.games.science.record_finding(
            tx.state,
            sample_id,
            repository=tx.samples,
            note=str(payload.get("note", "")),
        )
        tx.state.world["geology.logged_sample_id"] = sample_id
        tx.recorder.emit("science.finding_logged", entry, mission_id=mission_id)
        return {"science_entry": entry}

    # ------------------------------------------------------------------
    # Earth Trends
    # ------------------------------------------------------------------
    async def _compare_periods(
        self, tx: Transaction, mission_id: str, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        world = tx.state.world
        region_id = str(world.get("earth.region") or "nile_delta")
        layer_id = str(world.get("earth.layer") or "ndvi")
        before = str(payload.get("before", "2001-2005"))
        after = str(payload.get("after", "2021-2025"))
        analysis = await self.games.earth.compare(
            region_id=region_id,
            layer_id=layer_id,
            period_before=before,
            period_after=after,
            live=bool(payload.get("live", False)),
            seed=tx.state.seed,
        )
        summary = self.games.earth.summarise(analysis)
        progress = tx.state.mission(mission_id)
        progress.data["earth_analysis"] = summary
        world["earth.verdict"] = analysis["verdict"]
        world["earth.mean_delta"] = analysis["mean_delta"]
        tx.recorder.emit("earth.analysis_ready", summary, mission_id=mission_id)
        return {"earth_analysis": summary}

    async def _save_report(
        self, tx: Transaction, mission_id: str, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        progress = tx.state.mission(mission_id)
        analysis = progress.data.get("earth_analysis")
        if not analysis:
            return {"warning": "Run a comparison before saving a report."}
        report = await self.games.earth.save_report(
            tx.state.game_id,
            analysis,
            repository=tx.earth,
            title=str(payload.get("title", "")),
        )
        tx.state.report_ids.append(report["report_id"])
        progress.data["earth_report_id"] = report["report_id"]
        tx.state.world["earth.report_id"] = report["report_id"]
        tx.recorder.emit("earth.report_saved", report, mission_id=mission_id)
        return {"earth_report": report}