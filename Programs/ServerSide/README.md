# PC音声サーバー

[利用手順](USAGE.md) / [このPCの環境](../../LOCAL_SETUP.md) / [全体構成](../../docs/architecture.md)

| 場所 | 役割 |
| --- | --- |
| `dashboard/web/` | Web設定画面、マイクPCM送信、返答PCM出力 |
| `dashboard/server.py` | HTTP受付、Web配信、音声・設定API |
| `dashboard/settings_store.py` | 設定と参照音声の保存・検証 |
| `STTLLMTTS/stt/` | faster-whisper |
| `STTLLMTTS/llm/` | LM Studio APIとMCPツール橋渡し |
| `STTLLMTTS/tts/` | Irodori-TTS、参照キャッシュ・取消 |
| `STTLLMTTS/voice/` | Silero VAD、会話セッション、割込み、PCM・TCP通信 |
| `STTLLMTTS/tail_mcp_server.py` | 現行会話のトントン頻度設定・取得 |

HTTPは既定で127.0.0.1:8765、LM Studioは127.0.0.1:1234を使います。直接TCPは8766を別途起動するコードがありますが、このPCでは未起動です。

[Web音声API](VOICE_API.md)、[TCP音声API](VOICE_TCP.md)、[頻度API](TAIL_FREQUENCY_API.md) を参照してください。ESP32の実機HTTP APIと頻度保存APIは別物です。[ESP32統合の引継ぎ確認](ESP32_MCP_HANDOFF_REVIEW.md) に残る接続・停止世代の課題を解決するまで、提案Pythonで現行MCPを丸ごと置換しないでください。

モデル・外部ランタイムはセットアップ時に各配布元から取得します。このリポジトリに本体は同梱しません。`dashboard/data/` の設定・参照音声・生成音声もGit対象外です。実機確認の未完了事項は [要件](REQUIREMENTS.md) と [作業履歴](../WORK_HISTORY.md) に記録しています。
