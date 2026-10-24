"""PCのトントン頻度設定と、互換用の旧機器操作を提供するMCPサーバー。

音声アプリは頻度の保存・取得だけを公開します。ESP32側の実装は別担当で、
PC側の保存成功を実機反映と扱いません。旧Pi 4デバイスAPIは別契約です。
"""

from __future__ import annotations

import logging
import ipaddress
import math
import os
from numbers import Real
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
logging.basicConfig(level=os.getenv("TAIL_MCP_LOG_LEVEL", "INFO"))
logger = logging.getLogger("nen_gitsune.tail_mcp")

DEVICE_TIMEOUT = httpx.Timeout(3.0, connect=1.0)
mcp = MCPServer(
    "nen-gitsune-tail",
    instructions=(
        "Provides bounded, gentle tail tapping controls. Never use tapping unless the user "
        "clearly asks for it. Stop immediately when requested. No arbitrary servo angles are exposed. "
        "Frequency tools only read or save the PC's desired frequency; they never start movement "
        "or confirm that an ESP32 applied the setting."
    ),
)


def _dashboard_base_url() -> str:
    value = os.getenv("TAIL_DASHBOARD_URL", "http://127.0.0.1:8765").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("TAIL_DASHBOARD_URL must be an http(s) loopback URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("TAIL_DASHBOARD_URL must not contain credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("TAIL_DASHBOARD_URL must point to this PC's loopback address") from exc
    return value.rstrip("/") + "/"


def _frequency_result(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("Invalid frequency response")
    rate = body.get("rate_hz")
    if isinstance(rate, bool) or not isinstance(rate, Real):
        raise ValueError("Invalid frequency response")
    rate = float(rate)
    if not math.isfinite(rate) or not 0.3 <= rate <= 1.0:
        raise ValueError("Invalid frequency response")
    if body.get("device_confirmed") is not False or body.get("applies_to") != "configuration":
        raise ValueError("Frequency response must describe configuration only")
    return {"rate_hz": rate, "interval_ms": int(round(1000 / rate)),
            "device_confirmed": False, "applies_to": "configuration"}


async def _frequency_request(method: str, *, rate_hz: float | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=_dashboard_base_url(), timeout=DEVICE_TIMEOUT,
            trust_env=False, follow_redirects=False,
        ) as client:
            response = await client.request(
                method, "api/v1/tail/frequency",
                **({"json": {"rate_hz": rate_hz}} if method == "PUT" else {}),
            )
            response.raise_for_status()
            return _frequency_result(response.json())
    except Exception as exc:
        logger.warning("tail_frequency_api_failed error_type=%s", type(exc).__name__)
        raise ToolError("PC側の頻度設定APIへ接続できませんでした。設定は実機で確認されていません。") from exc


@mcp.tool()
async def set_tail_frequency(
    rate_hz: Annotated[float, Field(ge=0.3, le=1.0, strict=True, allow_inf_nan=False)],
) -> dict[str, Any]:
    """Save the PC's desired tapping frequency in Hz (0.3–1.0), without moving hardware.

    This changes configuration only. device_confirmed=false means ESP32 application is unconfirmed.
    """
    if isinstance(rate_hz, bool) or not isinstance(rate_hz, Real):
        raise ToolError("頻度は0.3〜1.0Hzの数値で指定してください。")
    try:
        rate = float(rate_hz)
    except (ValueError, OverflowError) as exc:
        raise ToolError("頻度は0.3〜1.0Hzの数値で指定してください。") from exc
    if not math.isfinite(rate) or not 0.3 <= rate <= 1.0:
        raise ToolError("頻度は0.3〜1.0Hzの数値で指定してください。")
    return await _frequency_request("PUT", rate_hz=rate)


@mcp.tool()
async def get_tail_frequency() -> dict[str, Any]:
    """Read the PC's desired frequency; this does not query or confirm ESP32 hardware."""
    return await _frequency_request("GET")


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


def _require_enabled() -> None:
    enabled = os.getenv("TAIL_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        raise RuntimeError("Tail control is disabled; set TAIL_CONTROL_ENABLED=true after hardware checks")


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=_device_base_url(),
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
