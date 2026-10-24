"""共有する音声処理ランタイム補助。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar


ResultT = TypeVar("ResultT")


class VoiceStageTimeout(TimeoutError):
    """音声処理の一段階が期限を超えた。"""

    def __init__(self, stage: str, timeout_seconds: float) -> None:
        super().__init__(f"{stage} stage exceeded {timeout_seconds:.1f} seconds")
        self.stage = stage
        self.timeout_seconds = timeout_seconds


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """タイムアウト後に完了したバックグラウンド処理の例外を回収する。"""
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def run_blocking_stage(
    stage: str,
    timeout_seconds: float,
    function: Callable[..., ResultT],
    *args: Any,
    **kwargs: Any,
) -> ResultT:
    """同期モデル処理を別スレッドで実行し、呼び出し側の待ち時間を制限する。

    Pythonスレッドやネイティブ推論処理は強制終了できない。期限切れや呼び出し元の
    キャンセル後もワーカーが動く場合があるため、完了時の例外を回収して結果を破棄する。
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    task = asyncio.create_task(
        asyncio.to_thread(function, *args, **kwargs),
        name=f"voice-stage-{stage}",
    )
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        task.add_done_callback(_consume_task_exception)
        raise

    if not done:
        task.add_done_callback(_consume_task_exception)
        raise VoiceStageTimeout(stage, timeout_seconds)
    return task.result()
