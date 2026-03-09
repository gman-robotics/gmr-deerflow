"""
gmr_web_search — Tavily (primary) + Brave Search API (fallback) web search tool.

Falls back to Brave on ANY exception from Tavily (including HTTP 429 rate limits).
Every fallback is logged with a [gmr_web_search] prefix so frequency and causes
can be analysed via `make docker-logs` or the gateway container logs.

Configuration (config.yaml):
    tools:
      - name: web_search
        group: web
        use: src.community.gmr_web_search.tools:web_search_tool
        max_results: 5

Environment variables (loaded from .env via docker-compose):
    TAVILY_API_KEY   — Tavily API key (TavilyClient reads this automatically)
    BRAVE_API_KEY    — Brave Search API subscription token
"""

import json
import logging
import os

import requests
from langchain.tools import tool

from src.config import get_app_config

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 5
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def _get_max_results() -> int:
    """Read max_results from tool config, falling back to default."""
    try:
        config = get_app_config().get_tool_config("web_search")
        if config is not None and "max_results" in config.model_extra:
            return int(config.model_extra["max_results"])
    except Exception:
        pass
    return _DEFAULT_MAX_RESULTS


def _search_tavily(query: str, max_results: int) -> str:
    """Call Tavily Search API. Raises on any failure."""
    from tavily import TavilyClient

    # TavilyClient reads TAVILY_API_KEY from the environment automatically.
    client = TavilyClient()
    results = client.search(query, max_results=max_results)
    return json.dumps(results, ensure_ascii=False)


def _search_brave(query: str, max_results: int) -> str:
    """Call Brave Search API. Raises on any failure."""
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        raise ValueError(
            "[gmr_web_search] BRAVE_API_KEY is not set in environment — "
            "cannot execute Brave fallback search."
        )

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": query,
        "count": max_results,
        "text_decorations": False,
        "search_lang": "en",
    }
    response = requests.get(
        _BRAVE_SEARCH_URL,
        headers=headers,
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    results = [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "content": r.get("description", ""),
        }
        for r in data.get("web", {}).get("results", [])
    ]
    return json.dumps({"results": results}, ensure_ascii=False)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """Search the web for current information relevant to the query.

    Args:
        query: The search query string.
    """
    max_results = _get_max_results()

    try:
        return _search_tavily(query, max_results)
    except Exception as e:
        logger.warning(
            "[gmr_web_search] Tavily failed for query %r — falling back to Brave. "
            "Reason: %s: %s",
            query,
            type(e).__name__,
            e,
        )

    return _search_brave(query, max_results)
