from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from yugioh_editor.common.card_errors import CardSuggestionError

_API_URL = "https://yugipedia.com/api.php"
_MAX_JSON_BYTES = 1024 * 1024


class YugipediaAliasClient:
    """Resolve explicit English-title redirects through the MediaWiki API."""

    def __init__(self, *, timeout_seconds: float = 15.0, max_retries: int = 2) -> None:
        timeout = float(timeout_seconds)
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer.")
        self.timeout_seconds = timeout
        self.max_retries = max_retries

    def resolve_alias(self, title: str) -> str | None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string.")
        payload = self._request(
            {
                "action": "query",
                "titles": title,
                "redirects": "1",
                "prop": "info",
                "format": "json",
                "formatversion": "2",
            },
            title,
        )
        query = payload.get("query")
        if not isinstance(query, dict):
            raise CardSuggestionError("Yugipedia response is missing query data.")
        pages = query.get("pages")
        if not isinstance(pages, list) or len(pages) != 1:
            raise CardSuggestionError("Yugipedia response has invalid page data.")
        page = pages[0]
        if not isinstance(page, dict):
            raise CardSuggestionError("Yugipedia response contains an invalid page.")
        if page.get("missing") is True:
            return None
        canonical = page.get("title")
        if not isinstance(canonical, str) or not canonical.strip():
            raise CardSuggestionError("Yugipedia page is missing a canonical title.")
        redirects = query.get("redirects", [])
        if redirects:
            if not isinstance(redirects, list) or not all(
                isinstance(item, dict) for item in redirects
            ):
                raise CardSuggestionError("Yugipedia redirect data is invalid.")
            return canonical
        return (
            canonical
            if _normalized_title(canonical) == _normalized_title(title)
            else None
        )

    def _request(self, parameters: dict[str, str], context: str) -> dict[str, object]:
        url = f"{_API_URL}?{urlencode(parameters)}"
        request = Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "YGOEditor/1.0"},
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read(_MAX_JSON_BYTES + 1)
                    if len(payload) > _MAX_JSON_BYTES:
                        raise CardSuggestionError("Yugipedia response is too large.")
                    value = json.loads(payload.decode("utf-8"))
                    if not isinstance(value, dict):
                        raise CardSuggestionError(
                            "Yugipedia returned invalid JSON data."
                        )
                    return value
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt >= self.max_retries:
                    raise CardSuggestionError(
                        f"Yugipedia lookup failed for {context!r}: HTTP {error.code}."
                    ) from error
            except (TimeoutError, URLError) as error:
                if attempt >= self.max_retries:
                    raise CardSuggestionError(
                        f"Yugipedia lookup failed for {context!r}: {error}."
                    ) from error
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CardSuggestionError(
                    "Yugipedia returned malformed JSON."
                ) from error
            time.sleep(min(0.25 * (2**attempt), self.timeout_seconds))
        raise AssertionError("Yugipedia HTTP retry loop terminated unexpectedly.")


def _normalized_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", normalized)
