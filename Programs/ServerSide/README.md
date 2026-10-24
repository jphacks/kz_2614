# ServerSide のフォルダー分け

自分たちが書いたプログラムと、外部のプログラム／モデルを分けて管理します。

必要な動作、未決事項、受け入れ条件、現行コードとの差分は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。MVPの録音・再生はスマホ、推論・音声生成／配信・MCP制御はサーバーの担当です。

```text
ServerSide/
├── app/                 # このプロジェクトの自作コードと実行設定
├── third_party/         # 外部プロジェクトのソースコード
│   └── Irodori-TTS/     # 外部TTSを置く場所
└── whisper/             # 現在あるWhisper用フォルダー（コードの参照先と要調整）
```

- 自作のWhisper連携処理は `app/voice_stt.py`、Whisper→LM Studioの接続は `app/voice_pipeline.py` です。
- faster-whisperのPythonパッケージは `app/requirements.txt` から仮想環境へインストールし、モデル重みは `models/whisper/` に保存します。外部パッケージのコードを `app/` にコピーしません。
- 現在あるフォルダーは `whisper/`、コードが参照する保存先は `models/whisper/` です。配置と設定の不一致は未解消で、モデルが配置済み・読み込み済みとは扱いません。
- 外部リポジトリはライセンスと依存関係を確認してから `third_party/` に置きます。`AI_Secretary-main` は参考として確認しましたが、そのプログラム一式はコピーしていません。
- `Irodori-TTS/` は外部ソースを置くための場所です。今は空のプレースホルダーで、音声合成の接続処理も未実装です。

実装・設定の詳細は [app/README.md](app/README.md) を参照してください。
