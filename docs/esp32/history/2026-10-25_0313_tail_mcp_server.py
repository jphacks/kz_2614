"""ESP32 Follower（SG90×3）用の、LM Studio向けstdio MCPサーバー提案版。

導入:
    このファイルを Programs/ServerSide/STTLLMTTS/tail_mcp_server.py に配置する。
    依存: Python 3.10+、mcp>=2,<3、httpx>=0.25,<1、python-dotenv>=1,<2。
    MCP登録名 nen-gitsune-tail と既存の3ツール名を維持する。
    LLMへ渡るのはツールの説明・引数定義。下記の導入説明全体は渡らない。

ServerSide/.env（プロセスの環境変数を優先）:
    TAIL_DEVICE_URL=http://192.168.100.111
    TAIL_CONTROL_ENABLED=false
    TAIL_MCP_ENABLED=false
    TAIL_MCP_PLUGIN_ID=mcp/nen-gitsune-tail
    実機の準備と会話側の許可・停止処理を接続してから2つのフラグを有効化。
    TAIL_MCP_ENABLEDは会話サーバーが読む設定。このファイルだけでは接続されない。
    PythonとESP32の間のトークン設定・認証ヘッダーは不要。

操作:
    tap_tail(duration_seconds=5.0): 0.5～5秒だけトントンする。
    stop_tail(): 最後の指令位置で停止・保持する。脱力や中立復帰ではない。
    get_tail_status(): ESP32の指令状態・モード・開始可否を見る。物理位置や接触は測れない。
    速度は実機確認済みの0.3回/秒に固定。振幅はFollower側の0°～180°設定。
    片端到着を1回と数えるので片道は約3.3秒、2秒では全域に届かない。
    各SG90の軸を0°～180°動かす指示ではなく、3関節へ分配する先端指令。
    60秒当たり合計10秒の上限はESP32側が強制する。自動再試行しない。

運用上の境界:
    Followerがneutral/relaxの間は開始を拒否する。自動解除しない。
    操作者が周囲を確認してFollowerのシリアルで t を送り準備する。
    t はReaderのつまみへの追従も再開するため、つまみ位置を先に確認する。
    このファイルは実機へ接続しても、起動しただけでは動作・停止指令を送らない。
    同一MCPプロセス内で停止と重なった開始には再度停止を送る。
    会話のsession_id/stop_generationは現行ESP32 APIに存在しないため、
    未到着の古いLLM要求を拒否する処理と停止ボタンの独立したHTTP停止は
    会話サーバー側で接続する必要がある。プロンプトだけでの許可判定は不十分。
    開始タイムアウト時は停止を試みるが、通信断時の物理停止は保証できない。
    ESP32自身の最大5秒の期限が最終的な動作時間制限になる。

GitHubとの再照合（2026-10-25 JST）:
    codex/raspberry-pi-implementation の最新コミット
    862820c6755a20cc8d08dda9906298edd5f0f2bb のMCP本体・呼び出し元・
    requirements.txt・mcp.json.exampleと照合。既存allowed_toolsの3名称に一致する。
    get_tail_statusは /api/tail/status と /api/status を読み、modeとready_to_tapを返す。
    idleでもneutral/relax中や動作範囲未設定ならready_to_tap=false。自動解除は行わない。
    ready_to_tapは設定とモード上の開始可否。残り動作時間の予算は開始時にESP32が判定する。
    HTTPXの区間ごとのタイムアウトに加え、1要求全体にも3秒の期限を設ける。
    LM StudioのMCP登録先は配置後のtail_mcp_server.py。ファイルを置き換えた後は
    MCPプロセスを再読み込みし、会話側のTAIL_MCP_ENABLEDも有効にして接続する。

検証記録（2026-10-25 JST）:
    Python 3.13.9 / MCP SDK 2.3.0で構文、実際のstdio初期化・tools/list、
    無効時のisError、模擬HTTPを使う引数・トークン不要の送信・停止競合・取消・通信異常を
    含む22テストが成功。ローカル模擬ESP32への実HTTPと実MCP通信でも3操作を確認。
    提案版から実機や稼働中のLLMへ接続した試験は未実施。
"""

# Adapted from jphacks/kz_2614/Programs/ServerSide/STTLLMTTS/tail_mcp_server.py
# Copyright (c) 2026 JPHACKS
# SPDX-License-Identifier: MIT
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
from typing import Annotated, Any, Callable
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
logging.basicConfig(level=logging.WARNING)  # stdioのstdoutはMCP専用。
logger = logging.getLogger("nen_gitsune.tail_mcp")
DEVICE_TIMEOUT = httpx.Timeout(3.0, connect=1.0)
DEVICE_REQUEST_DEADLINE_SECONDS = 3.0
TAP_RATE_HZ = 0.3
_stop_generation = 0
_tap_in_flight = False

mcp = MCPServer(
    "nen-gitsune-tail",
    instructions=(
        "しっぽを動かす・止める・状態を見る。トントンを明確に頼まれた時だけ開始し、"
        "停止要求には直ちにstop_tailを使う。成功時はESP32への指令が受理されたことを伝える。"
        "失敗した開始は自動で繰り返さない。"
    ),
)


class DeviceError(Exception):
    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


def _control_enabled() -> bool:
    return os.getenv("TAIL_CONTROL_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _device_base_url() -> str:
    value = os.getenv("TAIL_DEVICE_URL", "").strip()
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port  # 不正なポート番号もここで拒否する。
    except ValueError:
        raise DeviceError("CONFIGURATION_ERROR: TAIL_DEVICE_URLが不正です。") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise DeviceError("CONFIGURATION_ERROR: TAIL_DEVICE_URLには機器のhttp(s)起点URLを指定してください。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname != "localhost" and not hostname.endswith(".local"):
            raise DeviceError("CONFIGURATION_ERROR: 接続先はプライベートIPか.local名に限定します。") from None
    else:
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise DeviceError("CONFIGURATION_ERROR: 公開IPへは接続できません。")
    return value.rstrip("/") + "/"


_HTTP_ERRORS = {
    400: "INVALID_TAP_REQUEST: ESP32が動作条件を拒否しました。",
    401: "DEVICE_API_MISMATCH: 接続先に新しいESP32ファームウェアが書き込まれているか確認してください。",
    403: "TAIL_CONTROL_DISABLED: ESP32側で開始が無効になっています。",
    409: "TAIL_NOT_READY: 動作中、または中立・脱力中です。自動再試行しないでください。",
    429: "TAIL_BUDGET_EXCEEDED: 60秒当たりの動作時間上限に達しました。",
    503: "DEVICE_NOT_READY: ESP32のPWM・動作範囲を確認してください。",
}


def _validate_state(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise ValueError("missing success flag")
    state, angle, remaining = body.get("state"), body.get("angle"), body.get("remaining_ms")
    if (
        state not in {"idle", "tapping", "moving"}
        or body.get("feedback") != "command_only"
        or type(body.get("enabled")) is not bool
        or type(angle) is not int or not 0 <= angle <= 180
        or (state == "tapping" and (type(remaining) is not int or not 0 <= remaining <= 5000))
        or (state != "tapping" and remaining is not None)
    ):
        raise ValueError("invalid device state")
    return body


def _validate_mode(body: Any) -> dict[str, Any]:
    if (
        not isinstance(body, dict)
        or body.get("mode") not in {"teleop", "api", "konkon", "neutral", "relax"}
        or type(body.get("pwm_enabled")) is not bool
    ):
        raise ValueError("invalid operating mode")
    return body


async def _request(
    method: str, path: str, *,
    validate: Callable[[Any], dict[str, Any]] = _validate_state,
    **kwargs: Any,
) -> dict[str, Any]:
    base_url = _device_base_url()
    try:
        # LAN内のESP32へ直接接続する。
        async with httpx.AsyncClient(
            base_url=base_url, timeout=DEVICE_TIMEOUT,
            trust_env=False, follow_redirects=False,
        ) as client:
            response = await asyncio.wait_for(
                client.request(method, path, **kwargs),
                timeout=DEVICE_REQUEST_DEADLINE_SECONDS,
            )
    except (httpx.RequestError, asyncio.TimeoutError):
        raise DeviceError(
            "DEVICE_UNREACHABLE: ESP32の応答を確認できません。",
            uncertain=method == "POST",
        ) from None
    if response.status_code != 200:
        raise DeviceError(
            _HTTP_ERRORS.get(response.status_code, "DEVICE_HTTP_ERROR: ESP32の要求受付を確認できません。"),
            uncertain=method == "POST" and response.status_code not in _HTTP_ERRORS,
        )
    try:
        if len(response.content) > 4096:
            raise ValueError("oversized response")
        return validate(response.json())
    except (ValueError, TypeError):
        raise DeviceError(
            "INVALID_DEVICE_RESPONSE: 正しい機器状態を確認できません。",
            uncertain=method == "POST",
        ) from None


async def _best_effort_stop() -> bool:
    try:
        result = await _request("POST", "api/tail/stop")
        return result["state"] == "idle"
    except DeviceError:
        return False


def _summary(body: dict[str, Any]) -> str:
    # 角度は指令値。LLMへ大きなAPI応答をそのまま返さない。
    return json.dumps({
        "ok": True, "state": body["state"], "commanded_angle": body["angle"],
        "remaining_ms": body.get("remaining_ms"), "feedback": "command_only",
    }, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
async def tap_tail(
    duration_seconds: Annotated[float, Field(ge=0.5, le=5.0, allow_inf_nan=False, strict=True)] = 5.0,
) -> str:
    """明確に頼まれた時だけ、しっぽを0.5～5秒トントンする。速度固定。戻り値は開始の受付。"""
    global _tap_in_flight
    if not _control_enabled():
        raise ToolError("TAIL_CONTROL_DISABLED: 会話サーバー側で動作が許可されていません。")
    # MCPを介さない直接呼出しでも範囲を検査する。
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or not 0.5 <= duration_seconds <= 5.0:
        raise ToolError("INVALID_DURATION: duration_secondsは0.5～5.0秒です。")
    if _tap_in_flight:
        raise ToolError("TAIL_BUSY: 開始処理中です。自動再試行しないでください。")
    generation = _stop_generation
    _tap_in_flight = True
    try:
        result = await _request("POST", "api/tail/tap", json={
            "duration_ms": round(duration_seconds * 1000), "rate_hz": TAP_RATE_HZ,
        })
        if generation != _stop_generation:
            raise DeviceError("START_CANCELLED_BY_STOP: 開始処理中に停止要求を受けました。", uncertain=True)
        if result["state"] != "tapping":
            raise DeviceError("START_NOT_CONFIRMED: 開始状態を確認できません。", uncertain=True)
        return _summary(result)
    except asyncio.CancelledError:
        await asyncio.shield(_best_effort_stop())
        raise
    except DeviceError as exc:
        message = str(exc)
        if exc.uncertain:
            stopped = await _best_effort_stop()
            message += " 停止応答を受信しました。" if stopped else " 停止も確認できません。動作状態は不明です。"
        logger.warning("tap_tail: %s", message.split(":", 1)[0])
        raise ToolError(message) from None
    finally:
        _tap_in_flight = False


@mcp.tool()
async def stop_tail() -> str:
    """しっぽを直ちに停止し、最後の指令位置で保持する。開始が無効でも使用できる。"""
    global _stop_generation
    _stop_generation += 1  # 開始処理の完了を待たずに停止要求を送る。
    try:
        result = await _request("POST", "api/tail/stop")
        if result["state"] != "idle":
            raise DeviceError("STOP_NOT_CONFIRMED: 停止状態を確認できません。")
        try:
            result = await _request("GET", "api/tail/status")
        except DeviceError:
            raise DeviceError("STOP_ACKNOWLEDGED_STATUS_UNKNOWN: 停止応答は受信しましたが、その後の状態は確認できません。") from None
        if result["state"] != "idle":
            raise DeviceError("STOP_NOT_CONFIRMED: 停止後に動作状態が報告されました。")
        return _summary(result)
    except DeviceError as exc:
        logger.warning("stop_tail: %s", str(exc).split(":", 1)[0])
        raise ToolError(str(exc)) from None


@mcp.tool()
async def get_tail_status() -> str:
    """しっぽの指令状態・モード・開始可否を確認する。ready_to_tapが開始可否。位置は指令値。"""
    try:
        result = await _request("GET", "api/tail/status")
        operating_state = await _request("GET", "api/status", validate=_validate_mode)
        summary = json.loads(_summary(result))
        summary["device_enabled"] = result["enabled"]
        summary["control_enabled"] = _control_enabled()
        summary["mode"] = operating_state["mode"]
        start, end, max_rate = result.get("start_angle"), result.get("end_angle"), result.get("max_rate_hz")
        range_ready = type(start) is int and type(end) is int and 0 <= start < end <= 180
        rate_ready = type(max_rate) in {int, float} and math.isfinite(max_rate) and TAP_RATE_HZ <= max_rate <= 1.0
        summary["ready_to_tap"] = (
            result["enabled"] and _control_enabled() and result["state"] == "idle"
            and range_ready and rate_ready
            and operating_state["pwm_enabled"]
            and operating_state["mode"] in {"teleop", "api"}
        )
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    except DeviceError as exc:
        logger.warning("get_tail_status: %s", str(exc).split(":", 1)[0])
        raise ToolError(str(exc)) from None


if __name__ == "__main__":
    mcp.run(transport="stdio")
