"""LM Studioから呼ばれる、上限付きのRaspberry Pi 4しっぽ制御MCPサーバー。

Pi 4側のデバイスAPIは別途定義する契約です。デバイスサービスが
このAPIを実装するまでは、動作確認済みの機構として扱わないでください。
"""

from __future__ import annotations

import logging
import ipaddress
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import Field


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
logging.basicConfig(level=os.getenv("TAIL_MCP_LOG_LEVEL", "INFO"))
logger = logging.getLogger("nen_gitsune.tail_mcp")

DEVICE_TIMEOUT = httpx.Timeout(3.0, connect=1.0)
mcp = MCPServer(
    "nen-gitsune-tail",
    instructions=(
        "Provides bounded, gentle tail tapping controls. Never use tapping unless the user "
        "clearly asks for it. Stop immediately when requested. No arbitrary servo angles are exposed."
    ),
)


def _device_base_url() -> str:
    value = os.getenv("TAIL_DEVICE_URL", "").strip()
    if not value:
        raise RuntimeError("TAIL_DEVICE_URL is not configured")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("TAIL_DEVICE_URL must be an http(s) base URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("TAIL_DEVICE_URL must not contain credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname not in {"localhost"} and not hostname.endswith(".local"):
            raise RuntimeError("TAIL_DEVICE_URL must point to a private IP or .local host")
    else:
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise RuntimeError("TAIL_DEVICE_URL must point to a private or link-local IP")
    return value.rstrip("/") + "/"


def _device_headers() -> dict[str, str]:
    token = os.getenv("TAIL_DEVICE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TAIL_DEVICE_TOKEN is required")
    return {"Authorization": f"Bearer {token}"}


def _require_enabled() -> None:
    enabled = os.getenv("TAIL_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        raise RuntimeError("Tail control is disabled; set TAIL_CONTROL_ENABLED=true after hardware checks")


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=_device_base_url(),
        headers=_device_headers(),
        timeout=DEVICE_TIMEOUT,
        trust_env=False,
    ) as client:
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}


@mcp.tool()
async def tap_tail(
    duration_seconds: Annotated[float, Field(ge=0.5, le=5.0)] = 2.0,
    taps_per_second: Annotated[float, Field(ge=0.3, le=1.0)] = 0.6,
) -> str:
    """Start gentle tail tapping for at most five seconds at a bounded rate.

    Use only when the user clearly requests tapping. The Raspberry Pi service must enforce its own
    movement-angle limits and watchdog; this MCP limit is an additional guard.
    """
    try:
        _require_enabled()
        result = await _request(
            "POST",
            "api/tail/tap",
            json={
                "duration_ms": round(duration_seconds * 1000),
                "rate_hz": taps_per_second,
            },
        )
        return f"tap_tail sent; device_state={result.get('state', 'unknown')}"
    except Exception as exc:
        logger.warning("tap_tail failed: %s", type(exc).__name__)
        return f"tap_tail failed: {type(exc).__name__}"


@mcp.tool()
async def stop_tail() -> str:
    """Stop tail movement immediately. This tool remains available when tapping is disabled."""
    try:
        result = await _request("POST", "api/tail/stop")
        return f"stop_tail sent; device_state={result.get('state', 'unknown')}"
    except Exception as exc:
        logger.warning("stop_tail failed: %s", type(exc).__name__)
        return f"stop_tail failed: {type(exc).__name__}"


@mcp.tool()
async def get_tail_status() -> str:
    """Read whether the Raspberry Pi service reports that the tail is moving."""
    try:
        result = await _request("GET", "api/tail/status")
        return f"tail_status={result.get('state', 'unknown')}"
    except Exception as exc:
        logger.warning("get_tail_status failed: %s", type(exc).__name__)
        return f"get_tail_status failed: {type(exc).__name__}"


if __name__ == "__main__":
    mcp.run()
