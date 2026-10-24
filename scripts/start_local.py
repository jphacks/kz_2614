"""Start the installed local models and the loopback dashboard on this PC."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_DIR / "server"


def api_models(base_url: str) -> set[str] | None:
    request = Request(f"{base_url}/v1/models")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            payload = json.load(response)
        return {item["id"] for item in payload.get("data", []) if isinstance(item, dict) and "id" in item}
    except (HTTPError, URLError, OSError, ValueError, KeyError):
        return None


def loaded_models(lms: Path) -> set[str] | None:
    try:
        result = subprocess.run(
            [str(lms), "ps", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
        models = json.loads(result.stdout)
        return {model.get("identifier", "") for model in models}
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def ensure_lm_studio() -> None:
    base_url = os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
    model = os.getenv("VOICE_LLM_MODEL", "google/gemma-4-26b-a4b-qat")
    context_length = int(os.getenv("VOICE_LLM_CONTEXT_LENGTH", "4096"))
    gpu_ratio = float(os.getenv("VOICE_LLM_GPU_RATIO", "0.05"))
    if context_length < 512 or not 0 <= gpu_ratio <= 1:
        raise RuntimeError("VOICE_LLM_CONTEXT_LENGTH と VOICE_LLM_GPU_RATIO の設定を確認してください。")

    lms = Path(os.getenv("LMS_BIN", str(Path.home() / ".lmstudio/bin/lms"))).expanduser()
    available = api_models(base_url)
    if not lms.is_file():
        if available is not None and model in available:
            return
        raise RuntimeError("LM Studio の CLI が見つかりません。LM Studio で指定モデルと API を起動してください。")

    models = loaded_models(lms)
    if models is None:
        launcher = Path(os.getenv(
            "LM_STUDIO_LAUNCHER",
            str(PROJECT_DIR.parent / "tools/lm-studio/open-lm-studio.sh"),
        )).expanduser()
        if not launcher.is_file():
            raise RuntimeError("LM Studio を起動してから、もう一度実行してください。")
        print("LM Studio を起動しています…", flush=True)
        subprocess.Popen(
            ["bash", str(launcher)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Let the GUI finish initialization before changing its server state.
        time.sleep(5)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            models = loaded_models(lms)
            if models is not None:
                break
            time.sleep(1)
        if models is None:
            raise RuntimeError("LM Studio の起動を確認できませんでした。画面で起動状態を確認してください。")

    if model not in models:
        print(f"会話モデルを読み込んでいます: {model}", flush=True)
        subprocess.run(
            [str(lms), "load", model, "--gpu", str(gpu_ratio),
             "--context-length", str(context_length), "--parallel", "1",
             "--identifier", model, "--yes"],
            check=True,
        )

    if available is None:
        address = urlsplit(base_url)
        if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("指定された LM Studio API を手動で起動してください。")
        subprocess.run(
            [str(lms), "server", "start", "--port", str(address.port or 1234), "--bind", "127.0.0.1"],
            check=True,
        )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        available = api_models(base_url)
        if available is not None and model in available:
            return
        time.sleep(1)
    raise RuntimeError("指定した会話モデルへの API 接続を確認できませんでした。")


def main() -> None:
    load_dotenv(SERVER_DIR / ".env", override=False)
    # CTranslate2 needs the CUDA libraries before the dashboard imports it.
    package_dir = Path(sysconfig.get_path("purelib"))
    library_dirs = sorted((package_dir / "nvidia").glob("*/lib"))
    torch_lib = package_dir / "torch" / "lib"
    if torch_lib.is_dir():
        library_dirs.append(torch_lib)
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        [*(str(path) for path in library_dirs), *([existing] if existing else [])]
    )
    if "--help" not in sys.argv[1:] and "-h" not in sys.argv[1:]:
        ensure_lm_studio()
    os.chdir(PROJECT_DIR)
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "server.api.server", *sys.argv[1:]],
        os.environ,
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"起動できませんでした: {exc}", file=sys.stderr)
        raise SystemExit(1)
