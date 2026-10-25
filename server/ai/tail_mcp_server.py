"""PC頻度設定の互換MCPと、音声所有のv2機器MCP。

音声アプリはv2の有限tap・stop・stateだけを公開します。ESP32側の実装は別担当で、
機器の指令応答を物理動作の確認と扱いません。単体stdioは頻度設定だけです。
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


class VoiceTailMCP(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        # Validate before the SDK's coercion/default handling. The model cannot
        # smuggle a target or an identity through extra arguments.
        from .llm.tail_v2 import duration_ms
        if not isinstance(arguments, dict):
            raise ToolError("Arguments must be an object")
        if name == "tap_tail":
            if set(arguments) - {"duration_seconds"}:
                raise ToolError("Only duration_seconds is allowed")
            duration_ms(arguments.get("duration_seconds", 5.0))
        elif name not in {"stop_tail", "get_tail_status"} or arguments:
            raise ToolError("Unsupported tool or arguments")
        return await super().call_tool(name, arguments, context)


def create_tail_server(lease=None):
    """Bind trusted voice ownership outside the model-visible MCP schema.

    In-process MCP keeps the final HTTP guard in the voice process. Standalone
    stdio has no lease and cannot start hardware even with environment flags.
    """
    server = VoiceTailMCP("nen-gitsune-tail-v2")

    @server.tool()
    async def tap_tail(
        duration_seconds: Annotated[float, Field(ge=0.5, le=5.0, strict=True, allow_inf_nan=False)] = 5.0,
    ) -> dict[str, Any]:
        """Explicit current request only: finite tapping, 0.3Hz fixed, command feedback only."""
        if lease is None:
            raise ToolError("An active voice permit is required; standalone hardware control is disabled")
        try:
            return await lease.coordinator.tap(lease, duration_seconds)
        except Exception as exc:
            raise ToolError(f"Tail start rejected ({type(exc).__name__}); no physical success confirmed") from exc

    @server.tool()
    async def stop_tail() -> dict[str, Any]:
        """Close this voice session's device fence; report command stop separately from physical motion."""
        if lease is None:
            raise ToolError("An owning voice session is required")
        return await lease.stop()

    @server.tool()
    async def get_tail_status() -> dict[str, Any]:
        """Read the fixed v2 device's command state (not measured mechanical feedback)."""
        if lease is None:
            raise ToolError("An active voice permit is required")
        return await lease.coordinator.status(lease)

    return server


if __name__ == "__main__":
    # Configuration-only compatibility tools remain available over stdio.
    # Hardware tools require the app-owned in-process server and its live lease.
    mcp.run()
