"""SSRF-enforcing egress proxy for the crawler browser.

The crawler container has no route to the internet; its only way out is this proxy, which runs in its own
container. For every connection (page, redirect, sub-resource, websocket) it:

1. accepts only ``CONNECT host:port`` (HTTPS / WSS) or absolute-form ``http://`` requests,
2. normalises the hostname (IP literals, special-use names, bad ports are refused),
3. resolves the name *now* and refuses if ANY answer is non-public (DNS-rebinding defence),
4. connects to the address it just checked (pinning — no second lookup by anyone else),
5. enforces connect/idle/total time limits and a byte cap per connection.

Plain-HTTP requests are forwarded one per connection (``Connection: close``), so a client cannot smuggle a
second request for another host through an already-approved connection.

Run: ``python -m crawler.egress_proxy --host 0.0.0.0 --port 3128``
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import get_settings
from common.domains import DomainError, normalize_domain
from common.logging import configure_logging

from .ssrf import ALLOWED_PORTS, BlockedError, Resolver, check_url, is_public, resolve_public, system_resolve

log = logging.getLogger("category.egress")

MAX_HEAD = 16 * 1024
_HOP_HEADERS = {"proxy-connection", "proxy-authorization", "connection", "keep-alive", "te", "trailer", "upgrade"}


@dataclass
class ProxyConfig:
    resolver: Resolver = system_resolve
    allow: Callable[[str], bool] = is_public
    allowed_ports: Iterable[int] = ALLOWED_PORTS
    connect_timeout: float = 10.0
    idle_timeout: float = 30.0
    max_connection_seconds: float = 120.0
    max_bytes: int = 50 * 1024 * 1024
    max_connections: int = 256
    # Shared secret added to every proxy-generated reply, so the crawler can tell a real proxy refusal from a
    # website that forges an X-Category-Blocked header to look unreachable.
    token: str | None = None
    # Test hook only: map a checked (address, port) to where the test server really listens. Never set in production.
    connect_override: Callable[[str, int], tuple[str, int]] | None = None


@dataclass
class ProxyStats:
    allowed: int = 0
    blocked: dict[str, int] = field(default_factory=dict)


class EgressProxy:
    def __init__(self, config: ProxyConfig | None = None) -> None:
        self.config = config or ProxyConfig()
        self.stats = ProxyStats()
        self._slots = asyncio.Semaphore(self.config.max_connections)
        self.server: asyncio.base_events.Server | None = None
        self._active: set[asyncio.Task] = set()

    async def start(self, host: str, port: int) -> int:
        self.server = await asyncio.start_server(self._handle, host, port, limit=MAX_HEAD)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        """Stop accepting connections and cancel the ones in flight."""
        if self.server:
            self.server.close()
        for task in list(self._active):
            task.cancel()
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    # ------------------------------------------------------------------ request handling

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._slots.locked():
            await self._reply(writer, 503, "too_many_connections")
            return
        task = asyncio.current_task()
        self._active.add(task)
        async with self._slots:
            try:
                await asyncio.wait_for(self._serve(reader, writer), self.config.max_connection_seconds)
            except TimeoutError:
                log.info("connection time limit reached")
            except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                pass
            finally:
                writer.close()
                self._active.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.config.idle_timeout)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError):
            await self._reply(writer, 400, "bad_request")
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ")
        except ValueError:
            await self._reply(writer, 400, "bad_request")
            return
        headers = [line for line in lines[1:] if line]

        try:
            if method.upper() == "CONNECT":
                host, port = self._parse_authority(target)
            else:
                checked = check_url(target, allowed_ports=self.config.allowed_ports)
                if checked.scheme != "http":
                    raise BlockedError("scheme", "use CONNECT for https")
                host, port = checked.host, checked.port
            loop = asyncio.get_running_loop()
            addresses = await loop.run_in_executor(
                None, lambda: resolve_public(host, port, resolver=self.config.resolver, allow=self.config.allow))
        except BlockedError as exc:
            self._count_block(exc.code)
            log.warning("blocked", extra={"target": target[:200], "reason": exc.code})
            await self._reply(writer, 403, exc.code)
            return

        upstream = await self._connect(addresses, port)
        if upstream is None:
            self._count_block("connect_failed")
            await self._reply(writer, 502, "connect_failed")
            return
        up_reader, up_writer = upstream
        self.stats.allowed += 1
        try:
            if method.upper() == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._tunnel(reader, writer, up_reader, up_writer)
            else:
                await self._forward_http(method, target, version, headers, reader, writer, up_reader, up_writer)
        finally:
            up_writer.close()

    def _parse_authority(self, target: str) -> tuple[str, int]:
        host, sep, port_s = target.rpartition(":")
        if not sep or not port_s.isdigit():
            raise BlockedError("invalid_target", target[:100])
        port = int(port_s)
        if port not in set(self.config.allowed_ports):
            raise BlockedError("port", str(port))
        try:
            return normalize_domain(host.strip("[]")).ascii, port
        except DomainError as exc:
            raise BlockedError(exc.code, str(exc)) from exc

    async def _connect(self, addresses: list[str], port: int):
        for address in addresses:  # every address was already checked; connect to the checked one (pinning)
            host, real_port = (self.config.connect_override(address, port) if self.config.connect_override
                               else (address, port))
            try:
                return await asyncio.wait_for(asyncio.open_connection(host, real_port), self.config.connect_timeout)
            except (OSError, TimeoutError):
                continue
        return None

    # ------------------------------------------------------------------ relaying

    async def _pipe(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, budget: list[int]) -> None:
        while True:
            chunk = await asyncio.wait_for(src.read(65536), self.config.idle_timeout)
            if not chunk:
                break
            budget[0] -= len(chunk)
            if budget[0] < 0:
                self._count_block("byte_limit")
                raise ConnectionError("byte limit exceeded")
            dst.write(chunk)
            await dst.drain()
        if dst.can_write_eof():
            dst.write_eof()

    async def _tunnel(self, reader, writer, up_reader, up_writer) -> None:
        budget = [self.config.max_bytes]
        tasks = [asyncio.create_task(self._pipe(reader, up_writer, budget)),
                 asyncio.create_task(self._pipe(up_reader, writer, budget))]
        try:
            await asyncio.gather(*tasks)
        except (ConnectionError, TimeoutError, OSError):
            pass
        finally:
            for t in tasks:
                t.cancel()

    async def _forward_http(self, method, target, version, headers, reader, writer, up_reader, up_writer) -> None:
        from urllib.parse import urlsplit

        parts = urlsplit(target)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        length = 0
        out = [f"{method} {path} {version}"]
        for line in headers:
            name, _, value = line.partition(":")
            lname = name.strip().lower()
            if lname in _HOP_HEADERS:
                continue
            if lname == "transfer-encoding":
                await self._reply(writer, 411, "chunked_request_body_not_supported")
                return
            if lname == "content-length":
                length = int(value.strip()) if value.strip().isdigit() else 0
            out.append(line)
        out.append("Connection: close")
        up_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
        if length:
            if length > self.config.max_bytes:
                await self._reply(writer, 413, "request_too_large")
                return
            up_writer.write(await reader.readexactly(length))
        await up_writer.drain()
        # Only the response flows back; the client connection is closed afterwards (no request smuggling).
        try:
            await self._pipe(up_reader, writer, [self.config.max_bytes])
        except (ConnectionError, TimeoutError, OSError):
            pass

    # ------------------------------------------------------------------ helpers

    def _count_block(self, code: str) -> None:
        self.stats.blocked[code] = self.stats.blocked.get(code, 0) + 1

    async def _reply(self, writer: asyncio.StreamWriter, status: int, code: str) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 411: "Length Required", 413: "Payload Too Large",
                  502: "Bad Gateway", 503: "Service Unavailable"}.get(status, "Error")
        body = f"blocked by Cat-egory egress proxy: {code}\n".encode()
        token = f"X-Category-Proxy-Token: {self.config.token}\r\n" if self.config.token else ""
        writer.write(f"HTTP/1.1 {status} {reason}\r\nX-Category-Blocked: {code}\r\n{token}Content-Type: text/plain\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await writer.drain()
        except ConnectionError:
            pass


def proxy_block_code(headers, token: str | None) -> str | None:
    """The refusal code if this response really came from our proxy, else None (header absent or forged)."""
    code = headers.get("x-category-blocked")
    if not code:
        return None
    if token and not hmac.compare_digest(headers.get("x-category-proxy-token", ""), token):
        return None
    return code


async def _main(host: str, port: int) -> None:
    settings = get_settings()
    token = settings.egress_proxy_token.get_secret_value() if settings.egress_proxy_token else None
    if not token:
        log.warning("CATEGORY_EGRESS_PROXY_TOKEN not set; websites could imitate proxy refusals")
    proxy = EgressProxy(ProxyConfig(token=token))
    bound = await proxy.start(host, port)
    log.info("egress proxy listening", extra={"host": host, "port": bound})
    started = time.monotonic()
    while True:
        await asyncio.sleep(300)
        log.info("egress proxy stats", extra={"allowed": proxy.stats.allowed, "blocked": proxy.stats.blocked,
                                              "uptime_s": int(time.monotonic() - started)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Cat-egory SSRF egress proxy")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal network only
    parser.add_argument("--port", type=int, default=3128)
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_main(args.host, args.port))


if __name__ == "__main__":
    main()
