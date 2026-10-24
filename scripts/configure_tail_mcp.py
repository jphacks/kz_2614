"""Optionally register the tail MCP server in the LM Studio GUI.

The application's own stdio MCP client does not require this registration.
Without --install this command only prints its proposed server entry.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_NAME = "nen-gitsune-tail"


class ConfigurationError(RuntimeError):
    """A configuration cannot be safely read or updated."""


@dataclass(frozen=True)
class ConfigurationResult:
    changed: bool
    installed: bool
    backup_path: Path | None = None


def absolute_path(path: str | Path) -> Path:
    # resolve() would follow a venv's python symlink to the base interpreter.
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def build_entry(project_root: Path, python_override: str | Path | None = None) -> dict[str, Any]:
    root = absolute_path(project_root)
    server_root = root / "Programs" / "ServerSide"
    if python_override is None:
        unix = server_root / ".venv" / "bin" / "python"
        windows = server_root / ".venv" / "Scripts" / "python.exe"
        candidates = [windows, unix] if sys.platform == "win32" else [unix, windows]
        python = next((candidate for candidate in candidates if candidate.is_file()), None)
        if python is None:
            raise ConfigurationError("ServerSide/.venv の Python がありません。--python で指定してください。")
    else:
        python = absolute_path(python_override)
        if not python.is_file():
            raise ConfigurationError("--python は既存の Python 実行ファイルを指定してください。")
    script = server_root / "STTLLMTTS" / "tail_mcp_server.py"
    if not script.is_file():
        raise ConfigurationError("このプロジェクトの tail_mcp_server.py が見つかりません。")
    # Secrets remain in ServerSide/.env, which the server loads itself.
    return {"command": str(python), "args": [str(script)]}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-JSON constant")


def read_configuration(path: Path) -> tuple[dict[str, Any], bytes | None]:
    try:
        original = path.read_bytes()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        raise ConfigurationError("設定ファイルを読み込めません。") from exc
    try:
        document = json.loads(
            original.decode("utf-8-sig"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise ConfigurationError("設定ファイルが正しいJSONではありません。上書きしません。") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("設定ファイルの最上位はJSONオブジェクトである必要があります。")
    if "mcpServers" in document and not isinstance(document["mcpServers"], dict):
        raise ConfigurationError("mcpServers はJSONオブジェクトである必要があります。")
    return document, original


def configure(path: Path, entry: dict[str, Any], *, install: bool = False) -> ConfigurationResult:
    path = absolute_path(path)
    document, original = read_configuration(path)
    servers = dict(document.get("mcpServers", {}))
    if servers.get(SERVER_NAME) == entry:
        return ConfigurationResult(changed=False, installed=False)
    if not install:
        return ConfigurationResult(changed=True, installed=False)

    servers[SERVER_NAME] = dict(entry)
    updated = dict(document)
    updated["mcpServers"] = servers
    encoded = (json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    backup_path = None
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            # Preserve the original bytes, including unknown configuration and secrets.
            # A filesystem-generated suffix guarantees backups cannot overwrite each other.
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f"{path.name}.backup-", dir=path.parent, delete=False) as backup:
                backup_path = Path(backup.name)
                backup.write(original)
                backup.flush()
                os.fsync(backup.fileno())
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{path.name}.tmp-", dir=path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            if original is not None and hasattr(os, "fchmod"):
                os.fchmod(temporary.fileno(), stat.S_IMODE(path.stat().st_mode))
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise ConfigurationError("設定を更新できませんでした。元の設定とバックアップを確認してください。") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return ConfigurationResult(changed=True, installed=True, backup_path=backup_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="任意のLM Studio GUI登録を保存する（既定はdry-run）")
    parser.add_argument("--config", type=Path, default=Path.home() / ".lmstudio" / "mcp.json", help="更新する設定ファイル")
    parser.add_argument("--python", dest="python_path", type=Path, help="既存のPython実行ファイルを指定する")
    args = parser.parse_args(argv)
    try:
        entry = build_entry(PROJECT_ROOT, args.python_path)
        result = configure(args.config, entry, install=args.install)
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"MCP設定を更新できません: {exc}", file=sys.stderr)
        return 1

    # Never dump the existing document or another server's environment values.
    print(json.dumps({"mcpServers": {SERVER_NAME: entry}}, ensure_ascii=False, indent=2))
    if not args.install:
        print("Dry-runです。保存は --install を明示した場合だけ行います。アプリ所有のMCP実行にはこのGUI登録は不要です。", file=sys.stderr)
    elif result.installed:
        print("任意のLM Studio GUI登録を保存しました。", file=sys.stderr)
        if result.backup_path:
            print(f"バックアップ: {result.backup_path}", file=sys.stderr)
    else:
        print("同じ登録があるため変更していません。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
