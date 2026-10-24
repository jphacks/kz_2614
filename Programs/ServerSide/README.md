# ServerSideの構成

Android Web MVPではスマートフォンが録音・再生を行い、サーバーがSTT・LLM・TTSと会話セッションを担当します。ダッシュボードはブラウザーで使うWeb画面です。`dashboard/` にはWeb画面とその配信・HTTP受付を置き、音声処理と会話制御は `STTLLMTTS/` に分けます。依存関係と環境設定例は `ServerSide/` 直下に置きます。

必要な動作、未決事項、受け入れ条件、現行コードとの差分は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。音声API契約は [VOICE_API.md](VOICE_API.md)、Raspberry Pi 4のハードウェア契約は [ClientSide/Raspi4Ver/RASPBERRY_PI_API.md](../ClientSide/Raspi4Ver/RASPBERRY_PI_API.md) を参照してください。

```text
ServerSide/
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
- `STTLLMTTS/tail_mcp_server.py` は後続フェーズ用の独立したMCPプロセスです。しっぽ制御は既定で無効にします。
- 外部リポジトリはライセンスと依存関係を確認してから `STTLLMTTS/third_party/` に置きます。`AI_Secretary-main` は参考として確認しましたが、そのプログラム一式はコピーしていません。
- `STTLLMTTS/third_party/Irodori-TTS/` は外部ソースの配置先です。音声処理コードは別途起動した互換Gradioサーバーへ接続します。モデル取得・起動・音声生成・Android実機再生は未確認です。

Web画面サーバーの起動・認証・保存先は [dashboardのREADME](dashboard/README.md)、音声処理の詳細は [STTLLMTTSのREADME](STTLLMTTS/README.md)、Windows・Ubuntuの環境セットアップは [third_partyのREADME](STTLLMTTS/third_party/README.md) を参照してください。
