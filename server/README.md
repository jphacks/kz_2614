# AI・音声サーバー — kameyama

| フォルダー | 役割 |
| --- | --- |
| [ai/stt](ai/stt/) | faster-whisperで文字起こし |
| [ai/llm](ai/llm/) | LM Studio・MCPツール橋渡し |
| [ai/tts](ai/tts/) | Irodori-TTS、参照キャッシュ、取消 |
| [ai/voice](ai/voice/) | Silero VAD、会話状態、割込み、PCM・TCP |
| [api](api/README.md) | HTTP受付、Web配信、設定保存、稼働状態 |

Web自体のソースは [apps/web](../apps/web/README.md)。HTTPは既定8765、LM Studioは1234、直接TCPの設定は8766です。モデルは別途取得し、`.env.example`をローカルの`.env`へコピーして設定します。

[環境構築](../docs/setup/server.md) / [API仕様](../docs/api/README.md) / [全体構成](../docs/architecture.md)

MCPは現在、PC内のトントン頻度を保存・取得します。ESP32実制御は [引継ぎ課題](../docs/esp32/integration-review.md) の対応後に接続します。機器側のソースがmainにあることは、接続完了を意味しません。
