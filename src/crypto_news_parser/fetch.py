from __future__ import annotations

import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin, urlparse

import httpx


class FetchError(Exception):
    pass


class FetchBlockedError(FetchError):
    pass


class FetchTooLargeError(FetchError):
    pass


class FetchTimeoutError(FetchError):
    pass


class FetchUnsupportedContentTypeError(FetchError):
    pass


@dataclass(frozen=True)
class FetchResult:
    url: str
    content_type: str
    text: str


_MAX_REDIRECTS: Final[int] = 3
_MAX_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MiB


_NOISE_LINE_PATTERNS: Final[list[re.Pattern[str]]] = [
    # Common publisher boilerplate / UI chrome
    re.compile(
        r"(?i)\b(read more|advertisement|sponsored|subscribe|newsletter"
        r"|sign up|terms\b|privacy policy|cookie)\b"
    ),
    re.compile(r"(?i)\b(story continues below|enter your email|by signing up|make us preferred)\b"),
    re.compile(r"(?i)\b(twitter|linkedin|facebook|email)\b"),
    re.compile(r"(?i)\b(ui roboto|helvetica|arial|emoji|seg[o0]e)\b"),
    re.compile(r"(?i)\b(search news|video prices|research consensus|data indices)\b"),
    re.compile(r"(?i)\b(market wrap|breaking news)\b"),
    # Image credits and similar
    re.compile(r"(?i)\b(unsplash|shutterstock|getty images|photo|modified by)\b"),
]


def _looks_like_ticker_menu(line: str) -> bool:
    # Drop lines that are mostly an uppercase ticker list (common in site headers/sidebars).
    if re.fullmatch(r"(?:\$?[A-Z]{2,6})(?:\s+(?:\$?[A-Z]{2,6})){4,}", line.strip()):
        return True
    return False


def _cleanup_extracted_text(text: str) -> str:
    # Remove common boilerplate lines and excessive duplication.
    lines_in = [ln.strip() for ln in text.splitlines()]
    lines_out: list[str] = []
    seen: set[str] = set()

    for ln in lines_in:
        if not ln:
            continue
        # Filter extremely long lines (often minified CSS/JS or font stacks that slipped through).
        if len(ln) > 400:
            continue
        low = ln.lower()
        if low in seen:
            continue
        if _looks_like_ticker_menu(ln):
            continue
        if any(p.search(ln) for p in _NOISE_LINE_PATTERNS):
            continue
        # Drop lines that look like nav breadcrumbs / section labels.
        if re.fullmatch(
            r"(?i)(markets|policy|business|tech|prices|research|consensus|data|indices)", ln
        ):
            continue

        lines_out.append(ln)
        seen.add(low)

    cleaned = "\n".join(lines_out).strip()
    # If filtering is too aggressive (site-specific layouts), fall back to the raw extracted text.
    if not cleaned:
        return text[:20000]
    return cleaned[:20000]


def _is_ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    ips: list[ipaddress._BaseAddress] = []
    try:
        info = socket.getaddrinfo(host, None)
    except OSError as e:
        raise FetchError(f"DNS resolution failed: {e}") from e
    for fam, _, _, _, sockaddr in info:
        try:
            if fam == socket.AF_INET:
                ips.append(ipaddress.ip_address(sockaddr[0]))
            elif fam == socket.AF_INET6:
                ips.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    return ips


def validate_url_for_fetch(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        raise FetchBlockedError("Only http(s) URLs are allowed")
    if not p.hostname:
        raise FetchBlockedError("URL must include a hostname")

    # If hostname is an IP literal, validate it directly.
    try:
        ip = ipaddress.ip_address(p.hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if _is_ip_blocked(ip):
            raise FetchBlockedError("Blocked IP address")
        return

    for ip in _resolve_host_ips(p.hostname):
        if _is_ip_blocked(ip):
            raise FetchBlockedError("Blocked destination (private/loopback/link-local IP)")


def _html_to_text(html_bytes: bytes, encoding: str | None) -> str:
    raw = html_bytes.decode(encoding or "utf-8", errors="replace")
    # Remove script/style blocks.
    raw = re.sub(r"(?is)<(script|style)\\b.*?>.*?</\\1>", " ", raw)
    # Remove HTML comments.
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)

    # Prefer the main article content if present to avoid nav/footer boilerplate.
    m = re.search(r"(?is)<article\\b[^>]*>(.*?)</article>", raw)
    if m is not None:
        raw = m.group(1)

    # Turn common block separators into newlines.
    raw = re.sub(r"(?i)<\\s*br\\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</\\s*(p|div|li|h[1-6]|article|section)\\s*>", "\n", raw)
    # Strip remaining tags.
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    # Normalize whitespace.
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    # Avoid pathological lengths.
    return _cleanup_extracted_text(text)


async def fetch_url_text(url: str) -> FetchResult:
    validate_url_for_fetch(url)

    current = url
    redirects = 0

    timeout = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        while True:
            try:
                resp = await client.get(
                    current,
                    headers={
                        "User-Agent": "crypto-news-parser/0.1 (+https://github.com/Timaroc13/Crypto_News)",
                        "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                    },
                )
            except httpx.TimeoutException as e:
                raise FetchTimeoutError("Fetch timed out") from e
            except httpx.HTTPError as e:
                raise FetchError(f"Fetch failed: {e}") from e

            if resp.status_code in {301, 302, 303, 307, 308}:
                loc = resp.headers.get("location")
                if not loc:
                    raise FetchError("Redirect response missing Location header")
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    raise FetchError("Too many redirects")
                current = urljoin(current, loc)
                validate_url_for_fetch(current)
                continue

            if resp.status_code >= 400:
                raise FetchError(f"Upstream returned HTTP {resp.status_code}")

            content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "text/plain"}:
                    raise FetchUnsupportedContentTypeError(
                        f"Unsupported content-type: {content_type or 'unknown'}"
                    )

            # Read with a hard cap.
            content = b""
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                content += chunk
                if len(content) > _MAX_BYTES:
                    raise FetchTooLargeError("Fetched document exceeded max size")

            if content_type == "text/plain":
                text = content.decode(resp.encoding or "utf-8", errors="replace")
                return FetchResult(url=current, content_type=content_type, text=text[:20000])

            extracted = _html_to_text(content, resp.encoding)
            return FetchResult(url=current, content_type=content_type, text=extracted)
