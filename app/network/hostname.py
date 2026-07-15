"""Hostname resolution.
​
Resolves a device's hostname from its IP address using reverse DNS
(`socket.gethostbyaddr`). Resolution is wrapped with a hard timeout so a
slow or unreachable DNS server can never stall a network scan.
​
This module is GUI-agnostic and uses only the Python standard library.
Blocking calls should still be executed from a background thread (QThread,
asyncio, etc.). The timeout implemented here is only a safety mechanism.
​
Example
-------
>>> from app.network.hostname import resolve_hostname
>>> resolve_hostname("192.168.1.1")
'router.local'
>>> resolve_hostname("192.168.1.250")
None
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket

__all__ = [
    "resolve_hostname",
    "resolve_hostnames",
]

logger = logging.getLogger(__name__)

# Default timeout (seconds) for reverse DNS lookups.
DEFAULT_TIMEOUT: float = 2.0

# Shared thread pool used to enforce lookup timeouts.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="hostname-resolver",
)


def _is_valid_ip(ip: str) -> bool:
    """
    Check whether a string is a valid IPv4 or IPv6 address.
    """
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _reverse_lookup(ip: str) -> str | None:
    """
    Perform a blocking reverse-DNS lookup.

    Returns:
        Hostname if available, otherwise None.
    """
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror):
        logger.debug("No reverse-DNS record for %s", ip)
        return None
    except OSError as exc:
        logger.warning("Reverse-DNS lookup failed for %s: %s", ip, exc)
        return None

    hostname = hostname.strip()
    return hostname if hostname else None


def resolve_hostname(
    ip: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """
    Resolve a hostname from a single IP address.

    Args:
        ip:
            IPv4 or IPv6 address.
        timeout:
            Maximum number of seconds to wait.

    Returns:
        Hostname if found, otherwise None.
    """
    if not ip or not _is_valid_ip(ip):
        logger.debug("Skipping hostname resolution for invalid IP: %r", ip)
        return None

    future = _EXECUTOR.submit(_reverse_lookup, ip)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        logger.debug(
            "Reverse-DNS lookup timed out for %s (>%.1fs)",
            ip,
            timeout,
        )
        return None


def resolve_hostnames(
    ips: list[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, str | None]:
    """
    Resolve hostnames for multiple IP addresses concurrently.

    Args:
        ips:
            List of IPv4/IPv6 addresses.
        timeout:
            Timeout for each lookup.

    Returns:
        Dictionary mapping IP addresses to hostnames (or None).
    """
    unique_ips = list(dict.fromkeys(ips))
    results: dict[str, str | None] = {
        ip: None
        for ip in unique_ips
    }

    futures = {
        _EXECUTOR.submit(_reverse_lookup, ip): ip
        for ip in unique_ips
        if _is_valid_ip(ip)
    }

    for future in concurrent.futures.as_completed(futures):
        ip = futures[future]
        try:
            results[ip] = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            logger.debug("Reverse-DNS lookup timed out for %s", ip)
        except Exception as exc:
            logger.warning(
                "Unexpected error resolving %s: %s",
                ip,
                exc,
            )

    return results
