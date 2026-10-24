# 自作ServerSideアプリ

このフォルダーには、自作の短い音声会話 LLM クライアントと、しっぽ制御用 MCP サーバーを置きます。外部ソースとモデル重みは親フォルダーの `third_party/` と `models/` に分けます。

サーバーの要件と現行コードとの差分は [要件定義](../REQUIREMENTS.md) を参照してください。MVPはスマホ録音→サーバーのSTT・LLM・TTS→スマホ再生です。以下のSTT→LLMは部品としての実装であり、TTS・相づち・音声配信・優先停止を含む統合は未実装です。

## Whisper → LM Studio

`voice_stt.py` は `faster-whisper` で音声ファイルを日本語文字起こしし、モデルを `../models/whisper/` に保存します。`voice_pipeline.py` は文字起こしを既存の `voice_llm.py` に渡し、会話履歴と処理時間をメモリ上で扱います。

配置確認の未解消事項として、現物のWhisper用フォルダーは `../whisper/`、コードの保存先は `../models/whisper/` です。実行前に配置と参照先を揃える必要があります。

```text
音声ファイル → WhisperSTT → 文字起こし → VoiceLLMClient → LM Studio
                                           └─ 任意で尾端制御MCP
```

初回は `Systran/faster-whisper-small` をダウンロードします。モデルのダウンロード保存先は `download_root` で指定し、CPU / int8 を初期値にしています。変更する値は `.env` の `WHISPER_MODEL_ID`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE` です。モデルの取得時はインターネット接続が必要です。

呼び出し側からは、リポジトリのルートで `from Programs.ServerSide.app.voice_pipeline import VoicePipeline` として使います。

```python
async def handle_recording():
    pipeline = VoicePipeline()
    try:
        turn = await pipeline.process_audio_file("path/to/recording.wav")
        print(turn.transcription.text)
        print(turn.response.text)
        print(turn.timings_seconds)
    finally:
        await pipeline.close()
```

`VoicePipeline.process_audio_file(path)` は呼び出し元が用意した既存ファイルを処理します。録音・アップロード・一時ファイルの削除はまだ実装していません。認識結果が空の場合はLM Studioへ送らず `NoSpeechDetected` 例外を送出します。会話履歴は `VoicePipeline` インスタンスのメモリ内だけに保持し、`clear_history()` で消去できます。

## AI_Secretary から活用した点

`C:\Users\ryuuz\Downloads\AI_Secretary-main\AI_Secretary-main` を確認し、次の設計を参考に自作コードへ反映しました。

- 音声会話用の短い日本語プロンプトと、会話履歴の上限
- LM Studio の native `POST /api/v1/chat`、`reasoning=off`、`store=false`
- STT → LLM → TTS を段階ごとに分け、処理時間を測る考え方
- LLMの返答を待つ間に重ならない短い相づちを使い、音声を事前生成・キャッシュする考え方
- 録音を既定でファイル保存しない方針
- TTS の先行チャンクを再生して待ち時間を抑える案（将来の改善項目）

Discord Gateway、DAVE、Opus デコード、Discord 専用音声再生はこのプロジェクトへ持ち込んでいません。元のIrodori-TTSクライアントは macOS の MPS 固定値を含むため、そのままでは使わず、実行環境を確認してから接続します。

## LLM クライアント

`voice_llm.py` は、テキストと直近の会話履歴を LM Studio に送り、短い返答を返します。応答本文に加え、呼び出されたツール名と生成統計を `VoiceLLMReply` で返します。会話本文をこのクライアントがログやファイルへ保存する処理はありません。`store=false` で要求し、LM Studio のAPI設定に従って処理します。

MCP を有効にすると、LM Studio へ登録した `mcp/nen-gitsune-tail` のうち、`tap_tail`、`stop_tail`、`get_tail_status` だけを許可します。既定では `TAIL_MCP_ENABLED=false` です。

## 相づちの音声設計

相づちはMVPの必須要件です。AI_Secretaryには、短い相づちを事前生成してキャッシュし、LLMの最初の文節を待つ間に再生する実装があります。この作品でも、文字起こし後、LLMの返答がすぐに届かない場合に「うん」「そっか、聞いてるよ」などの短い音声を一度だけ再生します。事前生成・キャッシュは実現方法の候補です。

相づちはユーザーが話している間には流しません。LLMの返答音声、別の相づち、停止操作の後にも重ねません。本返答が先に準備できた場合は相づちを省略し、停止操作で再生中・待機中の相づちを停止します。参考実装の約0.9秒は目安であり、実際の待ち時間、言葉の種類、声と音量、連続して同じ相づちを使わない方法は未決定です。STTラッパーは追加済みですが、TTS・再生・相づち制御は未実装です。

## しっぽ制御 MCP

`tail_mcp_server.py` は LM Studio が stdio で起動するローカル MCP サーバーです。MCP と ESP32 は別の接続区間です。

```text
LM Studio / voice_llm.py
        │ MCP (stdio; LM Studio がローカルサーバーを起動)
        ▼
tail_mcp_server.py
        │ HTTP (同一LAN上のESP32を想定)
        ▼
ESP32 → SG90サーボ → 柔らかいしっぽ
```

公開するツールは3つです。

| MCP ツール | 用途 | 制限 |
| --- | --- | --- |
| `tap_tail` | しっぽをトントンさせる | 0.5〜5秒、毎秒0.3〜1.0回。角度は指定不可 |
| `stop_tail` | 動作を停止する | 起動許可が無効でも停止要求を送る |
| `get_tail_status` | ESP32の状態を確認する | 状態だけを取得 |

### ESP32 HTTP API（暫定契約）

ファームウェア側には次のAPIを実装する想定です。現時点では契約案であり、ESP32側の実装・実機動作は未確認です。

- `POST /api/tail/tap` — `{"duration_ms": 2000, "rate_hz": 0.6}` を受け、許容範囲内で動作を開始する
- `POST /api/tail/stop` — 即時停止する
- `GET /api/tail/status` — `{"state": "idle"}` または `{"state": "tapping"}` を返す
- 全APIで `Authorization: Bearer <token>` を検証する。MCPサーバーはローカル／プライベートIPまたは `.local` の機器だけを許可する

MCPサーバーは最大5秒という追加制限を設けます。これは機構の安全を保証するものではありません。ESP32側でも角度・速度・連続動作時間を制限し、通信が切れたら停止するウォッチドッグと、電源を切れる手元の停止手段を実装・確認してください。寝具や首・顔の近くでの実機安全確認が終わるまでは `TAIL_CONTROL_ENABLED=false` のままにします。

## 設定と起動

Python 3.11 以上を使い、このフォルダーで仮想環境を作成して依存関係を入れます。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` にLM Studioの設定を記入します。しっぽを使う場合はESP32ファームウェアのHTTP APIを用意し、実機確認後に `TAIL_DEVICE_URL` と `TAIL_CONTROL_ENABLED=true` を設定します。MCP単独での起動確認には次を使えます。

```powershell
.\.venv\Scripts\python.exe .\tail_mcp_server.py
```

stdioサーバーはMCPクライアントが起動して接続するため、このコマンドを手動実行した場合は入力待ちになります。

LM Studioの `mcp.json` に `mcp.json.example` のサーバー項目を登録し、LM Studioのサーバー設定で `mcp.json` のサーバー呼び出しとAPI認証を有効にします。API認証用に発行したトークンは `.env` の `LM_STUDIO_API_KEY` に設定してください。`voice_llm.py` 側の `TAIL_MCP_ENABLED=true` も設定すると、APIリクエストで `mcp/nen-gitsune-tail` を利用します。

## 現在の実装境界

- 実装済み: faster-whisper STTラッパー、STT→LM Studioの音声会話パイプライン、限定ツール連携のリクエスト生成、MCPサーバー、ESP32向けHTTPクライアントとAPI契約案。
- 未実装: TTS、Web画面、音声録音・アップロード、一時音声ファイルの管理、ESP32ファームウェア、HTTP APIの実機接続、会話セッションからの自動開始・停止。
- 未確認: LM Studioと指定モデルでのツール呼び出し、ESP32との通信、しっぽの強さ・速度・接触位置・騒音・発熱。

MCP SDK は公式 Python SDK の v2 API (`MCPServer`, stdio transport) を使用しています。LM Studioの native MCP integration は LM Studio 0.4.0 以上が必要です。

## 参考資料

- [MCP Python SDK: サーバー作成とツール](https://github.com/modelcontextprotocol/python-sdk/tree/main/docs)
- [LM Studio: MCPをREST APIから利用する](https://lmstudio.ai/docs/developer/core/mcp)
- [LM Studio: Chat API](https://lmstudio.ai/docs/developer/rest/chat)
- [faster-whisper: 公式リポジトリとモデル保存先の設定](https://github.com/SYSTRAN/faster-whisper)
