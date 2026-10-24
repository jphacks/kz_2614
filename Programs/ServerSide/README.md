# ServerSide のフォルダー分け

自分たちが書いたプログラムと、外部のプログラム／モデルを分けて管理します。

必要な動作、未決事項、受け入れ条件、現行コードとの差分は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。MVPではPi 4が録音・再生・しっぽ駆動、サーバーがSTT・LLM・TTSとMCP判断、スマートフォンが操作画面を担当します。Pi 4の契約は [RASPBERRY_PI_API.md](RASPBERRY_PI_API.md) を参照してください。

```text
ServerSide/
├── app/                 # このプロジェクトの自作コードと実行設定
├── web/                 # サーバーから配信する設定ダッシュボード
├── third_party/         # 外部プロジェクトのソースコード
│   └── Irodori-TTS/     # 外部TTSソースを置く場所（現在は空）
└── whisper/             # faster-whisperモデルのキャッシュ先
```

設定ダッシュボードは [web/](web/) にあります。起動方法、設定API、認証と設定反映の範囲は [app/README.md](app/README.md) を参照してください。

- `app/voice_stt.py`、`app/voice_llm.py`、`app/voice_tts.py` を `app/voice_pipeline.py` がつなぎ、STT→LLM→TTSの一往復を処理します。`app/voice_acknowledgements.py` は相づち音声を事前生成し、LLM待ちが長い場合の再生コールバックに渡します。
- faster-whisperのPythonパッケージと `gradio_client` は `app/requirements.txt` から仮想環境へインストールし、Whisperモデル重みは既存の `whisper/` に保存します。外部パッケージのコードを `app/` にコピーしません。
- 外部リポジトリはライセンスと依存関係を確認してから `third_party/` に置きます。`AI_Secretary-main` は参考として確認しましたが、そのプログラム一式はコピーしていません。
- `Irodori-TTS/` は外部ソースを置くための場所です。現在は空で、`app/voice_tts.py` は別途起動した互換Gradioサーバーへ接続します。モデル取得・起動・音声生成・Pi 4再生は未確認です。

実装・設定の詳細は [app/README.md](app/README.md) を参照してください。
