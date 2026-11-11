"""NASA open-data client.

Design goals
------------
* **Never break gameplay**: every call has a timeout, bounded retries, a TTL
  cache and a curated bundled fallback.
* **Always state provenance**: responses say ``live``, ``cache`` or
  ``bundled``/``simulated`` so the UI can label scientific honesty correctly.
* Respect DEMO_KEY limits by caching aggressively.

Endpoints used: APOD, Mars Rover Photos, DONKI, NEO feed, EPIC and NASA POWER
(the last one needs no API key and provides real monthly climate data).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.core.cache import TTLCache
from app.core.errors import ExternalServiceError
from app.core.logging import get_logger
from app.core.utils import iso, utcnow

logger = get_logger("nova.nasa")

POWER_BASE_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


class NasaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        fallback: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.fallback = fallback or {}
        self.cache: TTLCache[dict[str, Any]] = TTLCache(settings.nasa_cache_ttl_seconds)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.nasa_timeout_seconds),
                transport=self._transport,
                headers={"User-Agent": "NOVA-Game-Backend/1.0"},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    async def _fetch(
        self,
        url: str,
        params: dict[str, Any],
        *,
        cache_key: str,
        fallback_key: str,
        source_label: str,
    ) -> dict[str, Any]:
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return {**cached, "provenance": "cache"}

        if not self.settings.nasa_enabled:
            return self._bundled(fallback_key, source_label, reason="live data disabled")

        last_error: str = "unknown"
        for attempt in range(self.settings.nasa_max_retries + 1):
            try:
                client = await self._http()
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = {
                    "data": response.json(),
                    "provenance": "live",
                    "source": source_label,
                    "url": str(response.request.url).replace(self.settings.nasa_api_key, "***"),
                    "fetched_at": iso(utcnow()),
                }
                await self.cache.set(cache_key, payload)
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    "nasa.request_failed",
                    extra={"url": url, "attempt": attempt + 1, "error": last_error},
                )
                if attempt < self.settings.nasa_max_retries:
                    await asyncio.sleep(0.4 * (attempt + 1))

        stale = await self.cache.get(cache_key, allow_stale=True)
        if stale is not None:
            return {**stale, "provenance": "cache", "stale": True}
        return self._bundled(fallback_key, source_label, reason=last_error)

    def _bundled(self, key: str, source_label: str, *, reason: str) -> dict[str, Any]:
        data = self.fallback.get(key)
        if data is None:
            raise ExternalServiceError(
                f"NASA data unavailable and no bundled fallback for '{key}'.", reason=reason
            )
        return {
            "data": data,
            "provenance": "bundled",
            "source": f"{source_label} (bundled snapshot)",
            "fetched_at": iso(utcnow()),
            "note": "Live NASA data was unavailable; serving a curated offline snapshot.",
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------
    async def apod(self, date: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"api_key": self.settings.nasa_api_key, "thumbs": True}
        if date:
            params["date"] = date
        return await self._fetch(
            f"{self.settings.nasa_base_url}/planetary/apod",
            params,
            cache_key=f"apod:{date or 'today'}",
            fallback_key="apod",
            source_label="NASA APOD",
        )

    async def mars_photos(
        self, *, sol: int = 1000, camera: str | None = None, page: int = 1, rover: str = "curiosity"
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sol": sol,
            "page": page,
            "api_key": self.settings.nasa_api_key,
        }
        if camera:
            params["camera"] = camera
        return await self._fetch(
            f"{self.settings.nasa_base_url}/mars-photos/api/v1/rovers/{rover}/photos",
            params,
            cache_key=f"mars:{rover}:{sol}:{camera}:{page}",
            fallback_key="mars_photos",
            source_label="NASA Mars Rover Photos",
        )

    async def donki(self, *, start_date: str, end_date: str, kind: str = "GST") -> dict[str, Any]:
        return await self._fetch(
            f"{self.settings.nasa_base_url}/DONKI/{kind}",
            {
                "startDate": start_date,
                "endDate": end_date,
                "api_key": self.settings.nasa_api_key,
            },
            cache_key=f"donki:{kind}:{start_date}:{end_date}",
            fallback_key="donki",
            source_label="NASA DONKI (space weather)",
        )

    async def neo_feed(self, *, start_date: str, end_date: str) -> dict[str, Any]:
        return await self._fetch(
            f"{self.settings.nasa_base_url}/neo/rest/v1/feed",
            {
                "start_date": start_date,
                "end_date": end_date,
                "api_key": self.settings.nasa_api_key,
            },
            cache_key=f"neo:{start_date}:{end_date}",
            fallback_key="neo",
            source_label="NASA NEO Web Service",
        )

    async def epic(self) -> dict[str, Any]:
        return await self._fetch(
            f"{self.settings.nasa_base_url}/EPIC/api/natural",
            {"api_key": self.settings.nasa_api_key},
            cache_key="epic:natural",
            fallback_key="epic",
            source_label="NASA EPIC (DSCOVR Earth imagery)",
        )

    async def power_monthly(
        self,
        *,
        latitude: float,
        longitude: float,
        parameters: list[str],
        start_year: int,
        end_year: int,
    ) -> dict[str, Any]:
        """Real monthly climate data (NASA POWER, no API key required)."""
        return await self._fetch(
            POWER_BASE_URL,
            {
                "parameters": ",".join(parameters),
                "community": "RE",
                "longitude": longitude,
                "latitude": latitude,
                "start": start_year,
                "end": end_year,
                "format": "JSON",
            },
            cache_key=f"power:{latitude}:{longitude}:{','.join(parameters)}:{start_year}:{end_year}",
            fallback_key="power",
            source_label="NASA POWER (MERRA-2 / CERES)",
        )

    def cache_stats(self) -> dict[str, Any]:
        return self.cache.stats()
