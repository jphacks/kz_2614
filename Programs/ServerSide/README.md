# ServerSideの構成

2026-10-24 最新指定: AndroidはWebView方式から、音声通話専用のネイティブアプリと直接TCP通信へ変更。[新しい接続・起動手順](VOICE_TCP.md)を優先する。以下のWeb構成説明は既存経路の記録として保持する。APKビルド・実機通話は未確認。

Android Web MVPではスマートフォンが録音・再生とユーザー操作を行います。サーバーがSTT・LLM・TTS、会話セッション、相づち処理、MCP経由のPi 4しっぽ制御を担当します。AndroidはPi 4デバイスAPIへ直接接続しません。ダッシュボードはブラウザーで使うWeb画面です。`dashboard/` にはWeb画面とその配信・HTTP受付を置き、音声処理と会話制御は `STTLLMTTS/` に分けます。依存関係と環境設定例は `ServerSide/` 直下に置きます。

必要な動作、未決事項、受け入れ条件、現行コードとの差分は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。音声API契約は [VOICE_API.md](VOICE_API.md)、Raspberry Pi 4のハードウェア契約は [ClientSide/Raspi4Ver/RASPBERRY_PI_API.md](../ClientSide/Raspi4Ver/RASPBERRY_PI_API.md) を参照してください。

```text
ServerSide/
├── USAGE.md               # Windows・Ubuntuの利用マニュアル
├── STTLLMTTS/             # STT・LLM・TTS・音声パイプライン・セッション
│   ├── stt/
│   ├── llm/
│   ├── tts/
│   ├── voice/
│   ├── tail_mcp_server.py
│   ├── mcp.json.example
│   ├── third_party/
│   │   ├── setup_windows.ps1
│   │   ├── setup_ubuntu.sh
│   │   └── README.md
│   └── README.md
├── start_server_windows.ps1 # Windows用ServerSide HTTPサーバー起動
├── start_server_ubuntu.sh # Ubuntu用ServerSide HTTPサーバー起動
├── dashboard/             # ブラウザー画面とWebアプリ用HTTPサーバー
│   ├── server.py
│   ├── settings_store.py   # 設定・参照WAV管理
│   ├── web/
│   ├── data/               # 設定JSON・参照音声（Git除外）
│   └── README.md
├── requirements.txt       # ServerSide共通のPython依存関係
├── .env.example           # ServerSide共通の環境設定例
└── whisper/               # faster-whisperモデルのキャッシュ先
```

## 開発領域

- `dashboard/web/` がダッシュボードのWeb画面です。ダッシュボード自体はブラウザー上のUIを指し、音声処理や会話セッションのロジックは持ちません。
- `dashboard/server.py` はWeb画面を配信し、設定・参照WAV・音声セッションに関するHTTPリクエストを受け付けます。音声処理とセッション制御は `STTLLMTTS/` に委譲します。
- `dashboard/settings_store.py` は設定JSONと参照音声を `dashboard/data/` に保存します。STT・TTSが必要とする設定値を `STTLLMTTS/` から参照します。`requirements.txt` と `.env.example` はServerSide共通です。
- `STTLLMTTS/tail_mcp_server.py` は後続フェーズ用の独立したサーバー側MCPプロセスです。会話制御から呼び出し、要求・設定・停止世代を検証してPi 4デバイスAPIへ接続する責務を持たせます。Pi 4側の状態をサーバー経由でクライアントへ返します。しっぽ制御は既定で無効にします。AndroidはPi 4 APIを直接呼び出しません。
- 外部リポジトリはライセンスと依存関係を確認してから `STTLLMTTS/third_party/` に置きます。`AI_Secretary-main` は参考として確認しましたが、そのプログラム一式はコピーしていません。
- `STTLLMTTS/third_party/Irodori-TTS/` は外部ソースの配置先です。TTSは互換Gradioサーバーへの接続と、`VOICE_TTS_BACKEND=local` による公式ランタイムの直接実行に対応します。ローカル設定と確認結果は [このPCの起動手順](../../LOCAL_SETUP.md) を参照してください。Android実機再生は別途確認が必要です。

Windows・Ubuntuでのセットアップからブラウザー画面・音声会話の利用までの手順は [ServerSide利用マニュアル](USAGE.md) を参照してください。起動スクリプトはHTTPサーバーを前面起動し、既定では `127.0.0.1:8765` で待ち受けます。画面の仕様は [dashboardのREADME](dashboard/README.md)、音声処理の詳細は [STTLLMTTSのREADME](STTLLMTTS/README.md) を参照してください。
