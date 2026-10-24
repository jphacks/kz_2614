# ServerSide のフォルダー分け

自分たちが書いたプログラムと、外部のプログラム／モデルを分けて管理します。

必要な動作、未決事項、受け入れ条件、現行コードとの差分は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。Android Web MVPではスマートフォンが録音・再生、サーバーがSTT・LLM・TTSとセッション管理を担当します。API契約は [VOICE_API.md](VOICE_API.md)、Raspberry Pi 4のハードウェア契約は [RASPBERRY_PI_API.md](RASPBERRY_PI_API.md) を参照してください。

```text
ServerSide/
├── app/
│   ├── dashboard/       # HTTPサーバー
│   │   └── server.py
│   ├── voice/           # STT・LLM・TTSと音声セッション
│   │   ├── stt.py
│   │   ├── llm.py
│   │   ├── tts.py
│   │   ├── pipeline.py
│   │   ├── sessions.py
│   │   └── acknowledgements.py
│   ├── settings_store.py # dashboard と voice が共有する設定・参照音声管理
│   ├── tail_mcp_server.py
│   ├── requirements.txt
│   └── README.md
├── web/                 # サーバーから配信する設定・音声会話画面
├── third_party/         # 外部プロジェクトのソースコード
│   └── Irodori-TTS/     # 外部TTSソースを置く場所（現在は空）
└── whisper/             # faster-whisperモデルのキャッシュ先
```

## 役割の分離

- `app/voice/` は音声処理専用です。`stt.py`、`llm.py`、`tts.py` は各モデル接続を担当し、`pipeline.py` がSTT→LLM→TTSの一往復をつなぎます。`sessions.py` はAndroid Web音声会話のセッションを管理し、`acknowledgements.py` は相づち音声を扱います。
- `app/dashboard/` はHTTP入口です。`server.py` は設定APIと音声セッションAPIを受け、音声処理そのものは `app/voice/` に委譲します。ブラウザー画面は役割ごとに分けた静的資産として `web/` に置きます。
- `app/settings_store.py` はダッシュボードで編集した設定を音声処理も読むための共有層です。MCPしっぽ制御は `app/tail_mcp_server.py` に独立させています。
- faster-whisperのPythonパッケージと `gradio_client` は `app/requirements.txt` から仮想環境へインストールし、Whisperモデル重みは既存の `whisper/` に保存します。外部パッケージのコードを `app/` にコピーしません。
- 外部リポジトリはライセンスと依存関係を確認してから `third_party/` に置きます。`AI_Secretary-main` は参考として確認しましたが、そのプログラム一式はコピーしていません。
- `Irodori-TTS/` は外部ソースを置くための場所です。現在は空で、`app/voice/tts.py` は別途起動した互換Gradioサーバーへ接続します。モデル取得・起動・音声生成・Android実機再生は未確認です。

起動方法、API、設定、未確認事項は [app/README.md](app/README.md) を参照してください。
