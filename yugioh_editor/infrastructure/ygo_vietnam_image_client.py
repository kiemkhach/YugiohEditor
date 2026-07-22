from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from PIL import Image

from yugioh_editor.common.card_errors import (
    CardImageNotFoundError,
    CardImageParserError,
    CardImageTransportError,
)
from yugioh_editor.common.card_passwords import (
    MISSING_CARD_PASSWORD,
    normalize_card_password,
)

_PAGE_BASE = "https://ygovietnam.com/card/"
_PASSWORD_IMAGE_BASE = "https://cdn.ygovietnam.com/storage/Card/"
_PAGE_HOSTS = frozenset({"ygovietnam.com", "www.ygovietnam.com"})
_IMAGE_HOSTS = frozenset(
    {
        "cdn.ygovietnam.com",
        "ygovietnamcdn.azureedge.net",
        *_PAGE_HOSTS,
    }
)
_IMAGE_PATH_PREFIX = "/storage/card/"
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_ACCEPT_HEADERS = {"html": "text/html", "image": "image/*"}
_REJECTED_CONTEXT_MARKERS = frozenset(
    {"avatar", "icon", "logo", "placeholder", "related", "thumbnail", "thumb"}
)
_REJECTED_URL_MARKERS = (
    "default-card",
    "default_card",
    "loading",
    "logo",
    "no-image",
    "no_image",
    "placeholder",
    "thumbnail",
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class YgoVietnamImageClient:
    def __init__(self, *, timeout_seconds: float = 15.0, max_retries: int = 2) -> None:
        if isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be a finite positive number.")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer.")
        self.timeout_seconds = timeout
        self.max_retries = max_retries

    def fetch_card_image(self, english_name: str) -> bytes:
        if not isinstance(english_name, str) or not english_name.strip():
            raise ValueError("english_name must be a non-empty string.")
        encoded_name = quote(unquote(english_name.strip()), safe="")
        page_url = _PAGE_BASE + encoded_name
        page = self._request(page_url, max_bytes=_MAX_HTML_BYTES, expected="html")
        parser = _ImageUrlParser()
        try:
            parser.feed(page.payload.decode("utf-8", errors="replace"))
            parser.close()
        except Exception as error:
            raise CardImageParserError(
                f"YGO Vietnam card page HTML could not be parsed: {error}."
            ) from error
        image_url = parser.select_image_url(page.final_url)
        image_response = self._request(
            image_url,
            max_bytes=_MAX_IMAGE_BYTES,
            expected="image",
        )
        return self._validate_image_payload(image_response.payload)

    def fetch_card_image_by_password(self, password: str) -> bytes:
        normalized = normalize_card_password(password)
        if normalized == MISSING_CARD_PASSWORD:
            raise ValueError("The missing-password sentinel has no direct image URL.")
        url_password = normalized.lstrip("0") or "0"
        image_url = f"{_PASSWORD_IMAGE_BASE}{url_password}.jpg"
        response = self._request(
            image_url,
            max_bytes=_MAX_IMAGE_BYTES,
            expected="image",
        )
        return self._validate_image_payload(response.payload)

    @staticmethod
    def _validate_image_payload(payload: bytes) -> bytes:
        try:
            with Image.open(BytesIO(payload)) as image:
                image.verify()
        except Exception as error:
            raise CardImageParserError(
                f"Downloaded card image is invalid: {error}"
            ) from error
        return payload

    def _request(self, url: str, *, max_bytes: int, expected: str) -> _HttpPayload:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": _ACCEPT_HEADERS[expected],
                "User-Agent": "YGOEditor/1.0",
            },
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if status is None:
                        getcode = getattr(response, "getcode", None)
                        status = getcode() if callable(getcode) else 200
                    if not 200 <= int(status) < 300:
                        self._raise_http_status(int(status), url)
                    geturl = getattr(response, "geturl", None)
                    final_url = geturl() if callable(geturl) else url
                    self._validate_final_url(final_url, expected)
                    headers = getattr(response, "headers", None) or {}
                    content_type = str(headers.get("Content-Type", ""))
                    if content_type and not _content_type_matches(
                        content_type, expected
                    ):
                        raise CardImageParserError(
                            "YGO Vietnam returned unsupported content type "
                            f"{content_type!r}."
                        )
                    payload = response.read(max_bytes + 1)
                    if len(payload) > max_bytes:
                        raise CardImageParserError(
                            "YGO Vietnam response exceeded the size limit."
                        )
                    return _HttpPayload(payload, final_url)
            except HTTPError as error:
                if error.code in {404, 410}:
                    raise CardImageNotFoundError(
                        f"YGO Vietnam resource was not found: HTTP {error.code}."
                    ) from error
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt >= self.max_retries:
                    raise CardImageTransportError(
                        f"YGO Vietnam request failed: HTTP {error.code}."
                    ) from error
            except (TimeoutError, URLError, OSError) as error:
                if attempt >= self.max_retries:
                    raise CardImageTransportError(
                        f"YGO Vietnam request failed: {error}."
                    ) from error
            time.sleep(min(0.25 * (2**attempt), self.timeout_seconds))
        raise AssertionError("Image HTTP retry loop terminated unexpectedly.")

    @staticmethod
    def _raise_http_status(status: int, url: str) -> None:
        if status in {404, 410}:
            raise CardImageNotFoundError(
                f"YGO Vietnam resource was not found: HTTP {status}."
            )
        raise CardImageTransportError(
            f"YGO Vietnam request to {url!r} failed: HTTP {status}."
        )

    @staticmethod
    def _validate_final_url(url: str, expected: str) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        allowed_hosts = _PAGE_HOSTS if expected == "html" else _IMAGE_HOSTS
        approved = (
            parsed.scheme.casefold() in {"http", "https"}
            and hostname in allowed_hosts
            and (expected == "html" or _is_approved_image_url(url))
        )
        if not approved:
            raise CardImageTransportError(
                f"YGO Vietnam redirected to an unapproved {expected} URL {url!r}."
            )


@dataclass(frozen=True, slots=True)
class _HttpPayload:
    payload: bytes
    final_url: str


class _ImageUrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._elements: list[tuple[str, frozenset[str], bool]] = []
        self._scoped_candidates: list[str] = []
        self._json_ld_candidates: list[str] = []
        self._open_graph_candidates: list[str] = []
        self._json_ld_parts: list[str] | None = None
        self._malformed_json_ld = False

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized_tag = tag.casefold()
        values = {str(key).casefold(): value for key, value in attrs}
        markers = _attribute_markers(values)
        rejected = any(item[2] for item in self._elements) or bool(
            markers & _REJECTED_CONTEXT_MARKERS
        )

        if normalized_tag == "script" and _is_json_ld(values.get("type")):
            self._json_ld_parts = []

        if normalized_tag == "meta" and _is_open_graph_image(values):
            candidate = values.get("content")
            if candidate:
                self._open_graph_candidates.append(candidate)

        if normalized_tag in {"img", "source"} and not rejected:
            if self._inside_main_image_scope(markers):
                self._scoped_candidates.extend(_element_image_candidates(values))

        if normalized_tag not in _VOID_TAGS:
            self._elements.append((normalized_tag, markers, rejected))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "script" and self._json_ld_parts is not None:
            self._consume_json_ld("".join(self._json_ld_parts))
            self._json_ld_parts = None
        for position in range(len(self._elements) - 1, -1, -1):
            if self._elements[position][0] == normalized_tag:
                del self._elements[position:]
                return

    def select_image_url(self, page_url: str) -> str:
        rejected: list[str] = []
        for candidates in (
            self._scoped_candidates,
            self._json_ld_candidates,
            self._open_graph_candidates,
        ):
            for candidate in candidates:
                resolved = urljoin(page_url, candidate.strip())
                if _is_approved_image_url(resolved):
                    return resolved
                rejected.append(resolved)
        if rejected or self._malformed_json_ld:
            raise CardImageParserError(
                "YGO Vietnam card page contained image metadata, but no approved "
                "main card image URL."
            )
        raise CardImageNotFoundError(
            "YGO Vietnam page did not contain a main card image."
        )

    def _inside_main_image_scope(self, own_markers: frozenset[str]) -> bool:
        if own_markers & {"card", "cardart", "cardimage", "main"}:
            return True
        return any(
            tag == "main" or bool(markers & {"carddetail", "cardimage", "maincard"})
            for tag, markers, _rejected in self._elements
        )

    def _consume_json_ld(self, source: str) -> None:
        try:
            value = json.loads(source)
        except (TypeError, ValueError):
            self._malformed_json_ld = True
            return
        objects = value if isinstance(value, list) else [value]
        for item in objects:
            if not isinstance(item, dict) or not _is_creative_work(item.get("@type")):
                continue
            candidates = _json_image_candidates(item.get("image"))
            if candidates:
                self._json_ld_candidates.extend(candidates)


def _content_type_matches(content_type: str, expected: str) -> bool:
    media_type = content_type.partition(";")[0].strip().casefold()
    if expected == "html":
        return media_type in {"text/html", "application/xhtml+xml"}
    return media_type.startswith("image/")


def _attribute_markers(values: dict[str, str | None]) -> frozenset[str]:
    markers: set[str] = set()
    for name in ("class", "id", "role"):
        for item in str(values.get(name) or "").casefold().split():
            parts = re.findall(r"[a-z0-9]+", item)
            markers.update(parts)
            if parts:
                markers.add("".join(parts))
    return frozenset(markers)


def _is_json_ld(value: str | None) -> bool:
    return bool(
        value and value.partition(";")[0].strip().casefold() == "application/ld+json"
    )


def _is_open_graph_image(values: dict[str, str | None]) -> bool:
    key = str(values.get("property") or values.get("name") or "").casefold()
    return key in {"og:image", "og:image:url", "og:image:secure_url"}


def _element_image_candidates(values: dict[str, str | None]) -> tuple[str, ...]:
    srcset = values.get("srcset") or values.get("data-srcset")
    candidates: list[str] = []
    if srcset:
        parsed_srcset = []
        for position, item in enumerate(srcset.split(",")):
            parts = item.strip().rsplit(maxsplit=1)
            if not parts:
                continue
            url = parts[0]
            descriptor = parts[1] if len(parts) == 2 else ""
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(w|x)", descriptor.casefold())
            rank = float(match.group(1)) if match else 0.0
            parsed_srcset.append((rank, -position, url))
        candidates.extend(item[2] for item in sorted(parsed_srcset, reverse=True))
    for name in ("src", "data-src", "data-lazy-src"):
        candidate = values.get(name)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _is_creative_work(value: object) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(
        isinstance(item, str) and item.rsplit("/", 1)[-1].casefold() == "creativework"
        for item in values
    )


def _json_image_candidates(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    candidates: list[str] = []
    for item in values:
        if isinstance(item, str):
            candidates.append(item)
        elif isinstance(item, dict):
            for key in ("contentUrl", "url"):
                candidate = item.get(key)
                if isinstance(candidate, str):
                    candidates.append(candidate)
                    break
    return tuple(candidates)


def _is_approved_image_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    path = unquote(parsed.path).casefold()
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and hostname in _IMAGE_HOSTS
        and path.startswith(_IMAGE_PATH_PREFIX)
        and not any(marker in path for marker in _REJECTED_URL_MARKERS)
    )
