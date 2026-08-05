"""Bounded live HTTP primitives. This module is not wired to the product CLI."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import urljoin, urlsplit

from lottery_data.models import make_live_child_authorization_sha256

from .live_policy import LivePolicyError, source_index


_FROZEN_INITIAL_REQUESTS = {
    "live-ydniu-ssq-history": {"request_id": "live-ydniu-ssq-history", "sequence": 1, "source_id": "ydniu", "publisher_id": "ydniu-publisher", "game": "ssq", "method": "GET", "url": "https://www.ydniu.com/open/ssq-500/1.html", "request_kind": "history", "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0"},
    "live-swlc-ssq-history": {"request_id": "live-swlc-ssq-history", "sequence": 2, "source_id": "swlc", "publisher_id": "swlc-publisher", "game": "ssq", "method": "GET", "url": "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30", "request_kind": "history", "parser_id": "phase1-swlc-live-parser", "parser_version": "1.0.0"},
    "live-ydniu-dlt-history": {"request_id": "live-ydniu-dlt-history", "sequence": 3, "source_id": "ydniu", "publisher_id": "ydniu-publisher", "game": "dlt", "method": "GET", "url": "https://www.ydniu.com/open/dlt-500/1.html", "request_kind": "history", "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0"},
    "live-gdlottery-dlt-discovery": {"request_id": "live-gdlottery-dlt-discovery", "sequence": 4, "source_id": "gdlottery", "publisher_id": "gdlottery-publisher", "game": "dlt", "method": "GET", "url": "https://www.gdlottery.cn/html/dlt/index.html", "request_kind": "discovery", "parser_id": "phase1-gdlottery-live-parser", "parser_version": "2.0.0"},
}

_FROZEN_GD_CHILD_AUTHORIZATION = {
    "child_request_id": "live-gdlottery-dlt-announcement",
    "source_id": "gdlottery",
    "publisher_id": "gdlottery-publisher",
    "game": "dlt",
    "method": "GET",
    "request_kind": "announcement",
    "parser_id": "phase1-gdlottery-live-parser",
    "parser_version": "2.0.0",
    "same_origin": "https://www.gdlottery.cn",
    "path_pattern": r"^/f_html/kjgg/P085_[0-9]{5}\.html$",
    "max_children": 1,
}

# The v1.1 manifest contract carries this exact bounded child authorization on
# the discovery request. Runtime validation compares the complete request, so
# its frozen representation must be identical to the preflight representation.
_FROZEN_INITIAL_REQUESTS["live-gdlottery-dlt-discovery"] = {
    **_FROZEN_INITIAL_REQUESTS["live-gdlottery-dlt-discovery"],
    "child_authorization": dict(_FROZEN_GD_CHILD_AUTHORIZATION),
}

# Current v1.2 profile.  The legacy v1.1 4+1 constants above remain immutable
# so historical manifests and their focused tests keep their original meaning.
_FROZEN_V12_REQUESTS = {
    "live-ydniu-ssq-history": {
        **{key: value for key, value in _FROZEN_INITIAL_REQUESTS["live-ydniu-ssq-history"].items()},
        "response_profile": {"expected_media_type": "text/html", "max_response_bytes": 1048576},
    },
    "live-swlc-ssq-history": {
        **{key: value for key, value in _FROZEN_INITIAL_REQUESTS["live-swlc-ssq-history"].items()},
        "response_profile": {"expected_media_type": "text/html", "max_response_bytes": 1048576},
    },
    "live-ydniu-dlt-history": {
        **{key: value for key, value in _FROZEN_INITIAL_REQUESTS["live-ydniu-dlt-history"].items()},
        "response_profile": {"expected_media_type": "text/html", "max_response_bytes": 1048576},
    },
    "live-gdlottery-dlt-history": {
        "request_id": "live-gdlottery-dlt-history", "sequence": 4,
        "source_id": "gdlottery", "publisher_id": "gdlottery-publisher", "game": "dlt",
        "method": "GET", "url": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
        "request_kind": "history", "parser_id": "phase1-gdlottery-history-parser",
        "parser_version": "1.0.0",
        "response_profile": {"expected_media_type": "application/json", "max_response_bytes": 2097152},
    },
}


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int | None], maximum: int) -> None:
        self.origin = origin
        self.maximum = maximum
        self.count = 0

    def redirect_request(self, req: urllib.request.Request, fp: BinaryIO, code: int, msg: str, headers: Any, newurl: str) -> urllib.request.Request:
        try:
            parsed = urlsplit(newurl)
            candidate = (parsed.scheme, parsed.hostname or "", parsed.port)
        except (TypeError, ValueError) as exc:
            raise LivePolicyError("redirect_policy_violation", "live redirect URL is malformed", stage="runtime", exit_code=4) from exc
        if candidate != self.origin or parsed.scheme != "https" or self.count >= self.maximum:
            raise LivePolicyError("redirect_policy_violation", "live redirect violates same-origin policy", stage="runtime", exit_code=4)
        self.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HostThrottle:
    """A lock-file timestamp gate shared by processes using the same directory."""

    def __init__(self, root: Path, interval_seconds: float, *, clock: Callable[[], float] = time.time, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.root = root
        self.interval = interval_seconds
        self.clock = clock
        self.sleeper = sleeper

    def wait(self, host: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (hashlib.sha256(host.encode("ascii")).hexdigest() + ".lock")
        with path.open("a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"\n")
                stream.flush()
            _lock(stream)
            try:
                stream.seek(0)
                raw = stream.read().decode("ascii", errors="strict").strip()
                previous = float(raw) if raw else 0.0
                delay = self.interval - (self.clock() - previous)
                if delay > 0:
                    self.sleeper(delay)
                now = self.clock()
                stream.seek(0)
                stream.truncate()
                stream.write(f"{now:.9f}".encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                _unlock(stream)


def _lock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _safe_request_url(url: Any) -> Any:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live request URL is malformed") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or port is not None or parsed.fragment:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live request URL is unsafe")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live request URL targets a forbidden address")
    return parsed


def validate_live_request(request: Mapping[str, Any], policy: Mapping[str, Any], *, gd_discovery_body: bytes | None = None) -> dict[str, Any]:
    supplied = dict(request)
    _safe_request_url(supplied.get("url"))
    profile_version = policy.get("live_policy_schema_version")
    if profile_version in {"1.2.0", "1.3.0"}:
        if supplied.get("request_kind") == "announcement":
            raise LivePolicyError("endpoint_or_configuration_policy_violation", "current live profile forbids discovered child requests")
        expected = _FROZEN_V12_REQUESTS.get(supplied.get("request_id"))
    elif profile_version == "1.1.1":
        if supplied.get("request_kind") == "announcement":
            if gd_discovery_body is None:
                raise LivePolicyError("endpoint_or_configuration_policy_violation", "gdlottery announcement lacks current discovery evidence")
            expected = build_gd_announcement_request(policy, gd_discovery_body, sequence=supplied.get("sequence"))
        else:
            expected = _FROZEN_INITIAL_REQUESTS.get(supplied.get("request_id"))
    else:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "unknown live policy profile")
    if expected is None:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live request is absent from the frozen plan")
    if supplied != expected:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live request differs from its frozen authorization")
    return supplied


def fetch_to_raw(request: Mapping[str, Any], policy: Mapping[str, Any], raw_root: Path, throttle_root: Path, *, opener: Any = None, gd_discovery_body: bytes | None = None) -> dict[str, Any]:
    validated = validate_live_request(request, policy, gd_discovery_body=gd_discovery_body)
    url = validated["url"]
    parsed = _safe_request_url(url)
    network = policy["network_policy"]
    HostThrottle(throttle_root, float(network["cross_process_same_host_min_interval_seconds"])).wait(parsed.hostname)
    redirect = _SameOriginRedirect((parsed.scheme, parsed.hostname, parsed.port), int(network["max_redirects"]))
    client = opener or urllib.request.build_opener(redirect)
    is_static_history_request = policy.get("live_policy_schema_version") in {"1.2.0", "1.3.0"}
    response_profile = validated["response_profile"] if is_static_history_request else {
        "expected_media_type": "text/html", "max_response_bytes": 1048576,
    }
    expected_media_type = str(response_profile["expected_media_type"])
    maximum = int(response_profile["max_response_bytes"])
    http_request = urllib.request.Request(
        url, method="GET",
        headers={
            "Accept": expected_media_type, "Accept-Encoding": "identity",
            "User-Agent": "autoresearch-lotte/0.1 internal-research",
        },
    )
    try:
        response = client.open(http_request, timeout=int(network["request_timeout_seconds"]))
        with response:
            try:
                supplied_status = getattr(response, "status", None)
                status = int(response.getcode() if supplied_status is None else supplied_status)
                headers = response.headers
            except Exception as exc:
                raise LivePolicyError("http_non_success_or_response_too_large", "live response metadata is malformed", stage="runtime", exit_code=3) from exc
            if status != 200:
                raise LivePolicyError("http_non_success_or_response_too_large", f"live HTTP status {status}", stage="runtime", exit_code=3)
            try:
                declared_values = headers.get_all("Content-Length", []) if hasattr(headers, "get_all") else ([] if headers.get("Content-Length") is None else [headers.get("Content-Length")])
            except Exception as exc:
                raise LivePolicyError("http_non_success_or_response_too_large", "live Content-Length metadata is malformed", stage="runtime", exit_code=3) from exc
            if not isinstance(declared_values, (list, tuple)):
                raise LivePolicyError("http_non_success_or_response_too_large", "live Content-Length metadata is malformed", stage="runtime", exit_code=3)
            if len(declared_values) > 1 or any(
                not isinstance(value, str) or len(value) > 20 or not re.fullmatch(r"[0-9]+", value)
                for value in declared_values
            ):
                raise LivePolicyError("http_non_success_or_response_too_large", "live Content-Length metadata is malformed", stage="runtime", exit_code=3)
            if declared_values and int(declared_values[0]) > maximum:
                raise LivePolicyError("http_non_success_or_response_too_large", "live response exceeds byte limit", stage="runtime", exit_code=3)
            try:
                content_types = headers.get_all("Content-Type", []) if hasattr(headers, "get_all") else ([] if headers.get("Content-Type") is None else [headers.get("Content-Type")])
            except Exception as exc:
                raise LivePolicyError("content_type_encoding_or_parse_failure", "live Content-Type metadata is malformed", stage="runtime", exit_code=2) from exc
            if not isinstance(content_types, (list, tuple)) or len(content_types) != 1 or not isinstance(content_types[0], str):
                raise LivePolicyError("content_type_encoding_or_parse_failure", "live Content-Type metadata is malformed", stage="runtime", exit_code=2)
            content_type_parts = [part.strip() for part in content_types[0].split(";")]
            parameters = content_type_parts[1:]
            if (
                content_type_parts[0].lower() != expected_media_type
                or len(parameters) > 1
                or any(
                    not re.fullmatch(r"charset\s*=\s*(?:utf-8|\"utf-8\")", part, flags=re.I)
                    for part in parameters
                )
            ):
                raise LivePolicyError("content_type_encoding_or_parse_failure", "live response content type or encoding differs", stage="runtime", exit_code=2)
            try:
                content_encodings = headers.get_all("Content-Encoding", []) if hasattr(headers, "get_all") else ([] if headers.get("Content-Encoding") is None else [headers.get("Content-Encoding")])
            except Exception as exc:
                raise LivePolicyError("content_type_encoding_or_parse_failure", "live Content-Encoding metadata is malformed", stage="runtime", exit_code=2) from exc
            if (
                not isinstance(content_encodings, (list, tuple)) or len(content_encodings) > 1
                or any(not isinstance(value, str) or value.strip().lower() != "identity" for value in content_encodings)
            ):
                raise LivePolicyError("content_type_encoding_or_parse_failure", "live response encoding is not identity", stage="runtime", exit_code=2)
            try:
                final_url = response.geturl()
                final = _safe_request_url(final_url)
            except LivePolicyError as exc:
                raise LivePolicyError("redirect_policy_violation", "live response final URL is unsafe", stage="runtime", exit_code=4) from exc
            except Exception as exc:
                raise LivePolicyError("redirect_policy_violation", "live response final URL is malformed", stage="runtime", exit_code=4) from exc
            if is_static_history_request and final_url != url:
                raise LivePolicyError("redirect_policy_violation", "live response changed the frozen request URL", stage="runtime", exit_code=4)
            if (final.scheme, final.hostname, final.port) != (parsed.scheme, parsed.hostname, parsed.port):
                raise LivePolicyError("redirect_policy_violation", "live response changed origin", stage="runtime", exit_code=4)
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise LivePolicyError("http_non_success_or_response_too_large", "live response exceeds byte limit", stage="runtime", exit_code=3)
    except LivePolicyError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 407}:
            raise LivePolicyError("authentication_cookie_or_challenge_required", "live source requires authentication or challenge", stage="runtime", exit_code=3) from exc
        raise LivePolicyError("http_non_success_or_response_too_large", f"live HTTP status {exc.code}", stage="runtime", exit_code=3) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LivePolicyError(
            "dns_timeout_tls_or_required_source_unavailable",
            "live source unavailable",
            stage="runtime",
            exit_code=3,
            retryable=True,
        ) from exc
    safe_id = str(validated["request_id"])
    if not re.fullmatch(r"[a-z0-9-]+", safe_id):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "unsafe live request id")
    raw_path = raw_root / str(validated["source_id"]) / str(validated["game"]) / f"{safe_id}.raw"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    return {"raw_path": raw_path, "raw_sha256": digest, "content_length": len(body), "http_status": 200, "url": url, "final_url": final_url}


class _GdLink(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.btn_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        lower = tag.lower()
        if lower == "div" and (self.btn_depth or "btn" in set((values.get("class") or "").split())):
            self.btn_depth += 1
        href = values.get("href")
        if self.btn_depth and lower == "a" and isinstance(href, str):
            try:
                path = urlsplit(href).path
            except ValueError:
                path = ""
            if re.fullmatch(r"/f_html/kjgg/P085_[0-9]{5}\.html", path):
                # Do not deduplicate: the frozen discovery contract requires
                # exactly one matching element, not merely one distinct URL.
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "div" and self.btn_depth:
            self.btn_depth -= 1


def build_gd_announcement_request(policy: Mapping[str, Any], discovery_body: bytes, *, sequence: int) -> dict[str, Any]:
    if isinstance(sequence, bool) or sequence != 5:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "gdlottery announcement sequence must follow the frozen discovery plan")
    source = source_index(policy)["gdlottery"]
    frozen_identity = {
        "publisher_id": _FROZEN_GD_CHILD_AUTHORIZATION["publisher_id"],
        "parser_id": _FROZEN_GD_CHILD_AUTHORIZATION["parser_id"],
        "parser_version": _FROZEN_GD_CHILD_AUTHORIZATION["parser_version"],
        "discovery_endpoint": "https://www.gdlottery.cn/html/dlt/index.html",
        "announcement_origin": _FROZEN_GD_CHILD_AUTHORIZATION["same_origin"],
        "announcement_path_pattern": _FROZEN_GD_CHILD_AUTHORIZATION["path_pattern"],
    }
    if any(source.get(key) != value for key, value in frozen_identity.items()):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "gdlottery source identity differs from the frozen policy")
    parser = _GdLink()
    parser.feed(discovery_body.decode("utf-8"))
    if len(parser.links) != 1:
        raise LivePolicyError("missing_stale_or_mismatched_discovered_issue", "gdlottery announcement link is not unique", stage="runtime", exit_code=2)
    url = urljoin(source["discovery_endpoint"], parser.links[0])
    parsed = urlsplit(url)
    origin = urlsplit(source["announcement_origin"])
    if (parsed.scheme, parsed.hostname, parsed.port) != (origin.scheme, origin.hostname, origin.port) or parsed.query or parsed.fragment or not re.fullmatch(_FROZEN_GD_CHILD_AUTHORIZATION["path_pattern"], parsed.path):
        raise LivePolicyError("redirect_policy_violation", "unsafe gdlottery announcement link", stage="runtime", exit_code=4)
    issue_suffix = re.search(r"P085_(\d{5})\.html$", parsed.path).group(1)
    full_issue = "20" + issue_suffix
    if not re.fullmatch(r"20[0-9]{5}", full_issue):
        raise LivePolicyError(
            "missing_stale_or_mismatched_discovered_issue",
            "gdlottery announcement href issue cannot be normalized",
            stage="runtime",
            exit_code=2,
        )
    discovery_sha256 = hashlib.sha256(discovery_body).hexdigest()
    request = {
        "request_id": _FROZEN_GD_CHILD_AUTHORIZATION["child_request_id"], "sequence": sequence,
        "source_id": _FROZEN_GD_CHILD_AUTHORIZATION["source_id"],
        "publisher_id": _FROZEN_GD_CHILD_AUTHORIZATION["publisher_id"],
        "game": _FROZEN_GD_CHILD_AUTHORIZATION["game"],
        "method": _FROZEN_GD_CHILD_AUTHORIZATION["method"], "url": url,
        "request_kind": _FROZEN_GD_CHILD_AUTHORIZATION["request_kind"], "expected_raw_issue_id": full_issue,
        "parent_request_id": "live-gdlottery-dlt-discovery",
        "discovery_request_id": "live-gdlottery-dlt-discovery",
        "discovery_raw_ref": f"raw/gdlottery/dlt/sha256/{discovery_sha256}.raw",
        "discovery_raw_sha256": discovery_sha256,
        "parser_id": _FROZEN_GD_CHILD_AUTHORIZATION["parser_id"],
        "parser_version": _FROZEN_GD_CHILD_AUTHORIZATION["parser_version"],
    }
    request["authorization_sha256"] = make_live_child_authorization_sha256(request)
    return request


def validate_gd_announcement_result(request: Mapping[str, Any], parsed_records: list[Mapping[str, Any]]) -> None:
    expected = request.get("expected_raw_issue_id")
    path = urlsplit(str(request.get("url", ""))).path
    href_match = re.fullmatch(r"/f_html/kjgg/P085_([0-9]{5})\.html", path)
    if (
        request.get("request_kind") != "announcement"
        or request.get("request_id") != "live-gdlottery-dlt-announcement"
        or not isinstance(expected, str)
        or not re.fullmatch(r"20[0-9]{5}", expected)
        or href_match is None
        or "20" + href_match.group(1) != expected
        or len(parsed_records) != 1
    ):
        raise LivePolicyError("missing_stale_or_mismatched_discovered_issue", "gdlottery announcement result is not singular", stage="runtime", exit_code=2)
    record = parsed_records[0]
    if record.get("issue_id") != expected or "20" + str(record.get("raw_issue_id", "")) != expected:
        raise LivePolicyError("missing_stale_or_mismatched_discovered_issue", "gdlottery href, heading, and normalized issue disagree", stage="runtime", exit_code=2)
