"""Conservative public-GET acquisition primitives for P0-04."""

from __future__ import annotations

import email.utils
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping


FORBIDDEN_REQUEST_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
RESPONSE_HEADER_WHITELIST = (
    "Content-Type",
    "Content-Length",
    "Content-Encoding",
    "Date",
    "ETag",
    "Last-Modified",
    "Cache-Control",
    "Location",
)
ALLOWED_HOSTS = frozenset({"www.gdlottery.cn", "gdlottery.cn", "www.gdfc.org.cn", "gdfc.org.cn"})


class AcquisitionError(RuntimeError):
    """Raised when a public request would violate the acquisition contract."""


@dataclass(frozen=True)
class ClockCheck:
    checked_at_utc: str
    source: str
    offset_seconds: int
    maximum_offset_seconds: int
    passed: bool
    raw_result_sha256: str


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    redirect_chain: tuple[dict[str, object], ...]
    final_url: str
    retrieved_at: str
    raw_body: bytes
    response_headers: tuple[dict[str, str], ...]
    redacted_header_names: tuple[str, ...]

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_body).hexdigest()


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self, maximum_redirects: int) -> None:
        super().__init__()
        self.maximum_redirects = maximum_redirects
        self.hops: list[dict[str, object]] = []

    def reset(self) -> None:
        self.hops.clear()

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del fp, msg, headers
        self.hops.append({"url": req.full_url, "status": int(code)})
        if len(self.hops) > self.maximum_redirects:
            raise AcquisitionError("redirect limit exceeded")
        validate_source_url(newurl)
        validate_public_request_headers(dict(req.header_items()))
        redirected = super().redirect_request(req, None, code, "", {}, newurl)
        if redirected is not None:
            validate_public_request_headers(dict(redirected.header_items()))
        return redirected


class PublicHttpCollector:
    """HTTPS-only collector with no proxy and a fixed low-frequency rate."""

    def __init__(
        self,
        *,
        minimum_interval_seconds: float = 30.0,
        timeout_seconds: float = 20.0,
        maximum_redirects: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if minimum_interval_seconds < 30:
            raise AcquisitionError("minimum interval may not be lower than 30 seconds")
        self.minimum_interval_seconds = minimum_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self._redirects = _RedirectRecorder(maximum_redirects)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            self._redirects,
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(),
        )

    def fetch(self, url: str, *, clock_check: ClockCheck) -> FetchResult:
        validate_source_url(url)
        if not clock_check.passed:
            raise AcquisitionError("clock check failed; collection is prohibited")
        self._respect_rate_limit()
        headers = {"Accept": "text/html,application/xhtml+xml", "User-Agent": "autoresearch-lotte-phase0/1.0"}
        validate_public_request_headers(headers)
        request = urllib.request.Request(url, headers=headers, method="GET")
        self._redirects.reset()
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                final_url = response.geturl()
                validate_source_url(final_url)
                raw = response.read()
                status = int(getattr(response, "status", response.getcode()))
                allowed, redacted = whitelist_response_headers(response.headers)
        except urllib.error.HTTPError as exc:
            raise AcquisitionError(f"public GET failed with HTTP {exc.code}; no bypass attempted") from exc
        except urllib.error.URLError as exc:
            raise AcquisitionError(f"public GET failed: {exc.reason}") from exc
        self._last_request_at = self._monotonic()
        chain = [*self._redirects.hops, {"url": final_url, "status": status}]
        return FetchResult(
            requested_url=url,
            redirect_chain=tuple(chain),
            final_url=final_url,
            retrieved_at=_utc_now(),
            raw_body=raw,
            response_headers=tuple(allowed),
            redacted_header_names=tuple(redacted),
        )

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        remaining = self.minimum_interval_seconds - elapsed
        if remaining > 0:
            self._sleeper(remaining)


def run_windows_clock_check(maximum_offset_seconds: int = 5) -> ClockCheck:
    command = ["w32tm", "/stripchart", "/computer:time.windows.com", "/samples:3", "/dataonly"]
    completed = subprocess.run(command, capture_output=True, check=False)
    raw = completed.stdout + completed.stderr
    checked_at = _utc_now()
    if completed.returncode != 0:
        return ClockCheck(
            checked_at, "time.windows.com/w32tm", 0, maximum_offset_seconds, False,
            hashlib.sha256(raw).hexdigest(),
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = raw.decode("mbcs", errors="strict")
    offsets = parse_w32tm_offsets(text)
    maximum = max(abs(offset) for offset in offsets)
    rounded = int(round(maximum))
    return ClockCheck(
        checked_at,
        "time.windows.com/w32tm",
        rounded,
        maximum_offset_seconds,
        maximum <= maximum_offset_seconds,
        hashlib.sha256(raw).hexdigest(),
    )


def parse_w32tm_offsets(output: str) -> tuple[float, ...]:
    offsets = tuple(
        float(value.replace(",", "."))
        for value in re.findall(r"([+-]\d+(?:[.,]\d+)?)s\b", output, flags=re.I)
    )
    if not offsets:
        raise AcquisitionError("clock output contains no parseable offsets")
    return offsets


def validate_source_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise AcquisitionError("only HTTPS source URLs are allowed")
    if parsed.username or parsed.password:
        raise AcquisitionError("userinfo in source URL is forbidden")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise AcquisitionError(f"source host is not allowlisted: {host or '<missing>'}")


def validate_public_request_headers(headers: Mapping[str, str]) -> None:
    present = {name.lower() for name in headers}
    forbidden = sorted(present & FORBIDDEN_REQUEST_HEADERS)
    if forbidden:
        raise AcquisitionError(f"forbidden request header(s): {', '.join(forbidden)}")


def whitelist_response_headers(headers: Mapping[str, str] | object) -> tuple[list[dict[str, str]], list[str]]:
    items: Iterable[tuple[str, str]] = headers.items()  # type: ignore[union-attr]
    collected = list(items)
    by_lower = {name.lower(): (name, value) for name, value in collected}
    allowed: list[dict[str, str]] = []
    for canonical_name in RESPONSE_HEADER_WHITELIST:
        found = by_lower.get(canonical_name.lower())
        if found is not None:
            allowed.append({"name": canonical_name, "value": str(found[1])})
    whitelist_lower = {name.lower() for name in RESPONSE_HEADER_WHITELIST}
    redacted = sorted({name for name, _ in collected if name.lower() not in whitelist_lower}, key=str.lower)
    return allowed, redacted


def http_date_as_iso(headers: Iterable[dict[str, str]]) -> str | None:
    value = next((item["value"] for item in headers if item["name"] == "Date"), None)
    if value is None:
        return None
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def clock_check_to_json(check: ClockCheck) -> dict[str, object]:
    return {
        "checked_at_utc": check.checked_at_utc,
        "source": check.source,
        "offset_seconds": check.offset_seconds,
        "maximum_offset_seconds": check.maximum_offset_seconds,
        "passed": check.passed,
        "raw_result_sha256": check.raw_result_sha256,
    }


def clock_check_from_json(value: Mapping[str, object]) -> ClockCheck:
    return ClockCheck(
        checked_at_utc=str(value["checked_at_utc"]),
        source=str(value["source"]),
        offset_seconds=int(value["offset_seconds"]),
        maximum_offset_seconds=int(value["maximum_offset_seconds"]),
        passed=bool(value["passed"]),
        raw_result_sha256=str(value["raw_result_sha256"]),
    )


def dump_clock_check(check: ClockCheck) -> str:
    return json.dumps(clock_check_to_json(check), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
