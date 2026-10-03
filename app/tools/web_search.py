"""Tavily web search wrapper: basic depth by default, dev cache, and credit counting.

Deliberately exposes search only. Never call Tavily's Research endpoint in loops.
"""

from __future__ import annotations

from typing import Any, Literal

from app.cache import DiskCache, cache_key
from app.config import Settings

Depth = Literal["basic", "advanced"]
_CREDITS = {"basic": 1, "advanced": 2}


class SearchError(RuntimeError):
    """A web search failed. The message is safe to show to the user."""


class WebSearch:
    def __init__(self, settings: Settings, tavily_client: Any | None = None):
        if tavily_client is None:
            settings.require("tavily_api_key")
            from tavily import TavilyClient

            tavily_client = TavilyClient(api_key=settings.tavily_api_key)
        self._client = tavily_client
        self._cache = DiskCache(settings.cache_dir, "tavily", enabled=settings.cache_enabled)
        self.credits_used = 0
        self.live_searches = 0
        self.cached_searches = 0

    def search(
        self,
        query: str,
        *,
        depth: Depth = "basic",
        max_results: int = 5,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """Return a list of results, each with at least title, url and content."""
        key = cache_key({"query": query, "depth": depth, "max_results": max_results})
        if use_cache and (hit := self._cache.get(key)) is not None:
            self.cached_searches += 1
            return hit

        try:
            resp = self._client.search(
                query, search_depth=depth, max_results=max_results, include_usage=True
            )
        except Exception as e:  # tavily raises several unrelated exception types
            raise SearchError(f"Tavily search failed for {query!r}: {e}") from e

        usage = resp.get("usage") or {}
        self.credits_used += int(usage.get("credits", _CREDITS[depth]))
        self.live_searches += 1

        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in resp.get("results", [])
        ]
        if use_cache:
            self._cache.set(key, results)
        return results
