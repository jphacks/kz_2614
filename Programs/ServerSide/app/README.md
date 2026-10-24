# 自作ServerSideアプリ

このフォルダーには、自作の音声会話クライアントと、しっぽ制御用 MCP サーバーを置きます。外部ソースは親フォルダーの `third_party/`、Whisper モデルは `../whisper/` に分けます。

サーバーの要件と現行コードとの差分は [要件定義](../REQUIREMENTS.md) を参照してください。`VoicePipeline` は既存音声ファイルを STT → LLM → TTS で処理し、文字起こし・返答・再生用音声バイト列・各段階の所要時間を返します。遅延時の相づち音声キャッシュと通知も実装済みです。Pi 4向けの音声・デバイスAPI、録音音声の受信、再生完了連携、優先停止は未実装です。

## 設定ダッシュボード

HTML・CSS・JavaScript は親フォルダーの `../web/` にあります。リポジトリのルートで次を実行すると、既定ではこのPC内だけで `http://127.0.0.1:8765/` に設定画面を配信します。

```powershell
python -m Programs.ServerSide.app.dashboard_server
```

画面ではSTTモデル・言語・実行デバイス・計算形式・無音除外、TTSモデル・声の説明、話し方・返答の長さ・質問頻度・追加の話し方を設定できます。設定は `app/data/dashboard_settings.json` に保存され、Gitには含まれません。設定の反映先は新しい `VoicePipeline` インスタンスです。進行中の会話へは反映されません。相づちはMVP必須のため無効化できません。ダッシュボードからしっぽ制御の許可状態は変更できません。

参照音声はPCM WAVだけを受け付けます（16-bit、mono/stereo、16〜48kHz、1〜30秒、10MB以下）。アップロード先は固定の `app/data/reference_voice.wav` で、ファイル名やパスをクライアントから指定できません。新しいTTSインスタンスでIrodori-TTSの `ref_wav` に使います。インスタンス生成時に音声データをメモリへ読み込むため、ファイルを置換・削除しても既存インスタンスはその会話中に読み込み済みの音声を使い、新しいインスタンスは現在サーバーにあるWAVを使います。参照音声はTTS生成時に設定済みのIrodori-TTSサーバーへ送られます。

LAN上の端末から開く場合は、`.env` または環境変数で32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` を設定してから `--host 0.0.0.0` を指定してください。管理トークンのないLAN公開は起動時に拒否します。トークンはブラウザーの現在のタブ内でのみ保持します。LANアクセスは信頼できるネットワークに限定し、HTTPSリバースプロキシを利用してください。この設定画面は音声会話APIではなく、Pi 4との音声録音・配信・再生連携は別途実装が必要です。

## Whisper → LM Studio → Irodori-TTS

`voice_stt.py` は `faster-whisper` で音声ファイルを日本語文字起こしし、モデルを `../whisper/` に保存します。`voice_pipeline.py` は文字起こしを `voice_llm.py` に渡し、LLMの返答を `voice_tts.py` から Irodori-TTS へ送り、返却された音声をメモリ上のバイト列として返します。会話履歴は `VoicePipeline` インスタンス内に保持します。

`voice_tts.py` は `gradio_client` を使い、`IRODORI_URL` の Gradio API `/_run_generation` を呼び出します。取得した一時音声ファイルは音声バイト列へ読み込んだあと、OSの一時フォルダー内にある場合だけ削除します。Irodori-TTS のモデル一式はここには含めず、互換サーバーを別途起動してください。GPUのない環境では、`.env` でモデルとcodecのデバイスを `cpu`、精度を `fp32` に変更します。モデル、デバイス、速度、Pi 4での再生形式は実機確認が必要です。

`voice_acknowledgements.py` は「うん、聞いてるよ。」「そっか、話してくれてありがとう。」の音声を会話開始前に同じTTS設定で生成し、メモリにキャッシュします。LLMの返答が `VOICE_ACK_DELAY_SECONDS`（既定0.9秒）を超える場合だけ1つを交互に選びます。「おやすみ」や停止要求には相づちを出しません。本返答と重ねないため、相づち用コールバックはPi 4での再生完了まで待ってから戻る必要があります。

```text
音声ファイル → WhisperSTT → 文字起こし → VoiceLLMClient → LM Studio → IrodoriTTS
                                                           └─ 任意で尾端制御MCP
再生用音声バイト列 ←────────────────────────────────────────────────────┘
```

初回は `Systran/faster-whisper-small` をダウンロードします。モデルは `ServerSide/whisper/` に保存し、CPU / int8 を初期値にしています。変更する値は `.env` の `WHISPER_MODEL_ID`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE` です。入力ファイルは `WHISPER_MAX_AUDIO_BYTES`（既定25MB）で制限します。モデルの取得時はインターネット接続が必要です。

呼び出し側からは、リポジトリのルートで `from Programs.ServerSide.app.voice_pipeline import VoicePipeline` として使います。

```python
async def handle_recording():
    pipeline = VoicePipeline()

    async def deliver_acknowledgement(ack):
        # Pi 4デバイスAPI実装時に、再生完了確認まで待つ。
        await pi4_player.play_and_wait(ack.speech.audio_bytes, ack.speech.media_type)

    try:
        # 会話開始前に相づち音声を作っておくと、初回ターンの待ち時間を減らせる。
        await pipeline.prepare_acknowledgements()
        turn = await pipeline.process_audio_file(
            "path/to/recording.wav",
            on_acknowledgement=deliver_acknowledgement,
        )
        print(turn.transcription.text)
        print(turn.response.text)
        # HTTP 配信層は未実装。audio_bytes と media_type は後続実装で利用する。
        print(len(turn.speech.audio_bytes), turn.speech.media_type)
        print(turn.timings_seconds)
    finally:
        await pipeline.close()
```

`VoicePipeline.process_audio_file(path)` は呼び出し元が用意した既存ファイルを処理します。録音・アップロード・HTTP配信はまだ実装していません。認識結果が空の場合はLLMへ送らず `NoSpeechDetected` 例外を送出します。TTS生成に失敗した発話は履歴へ追加しません。履歴は `VoicePipeline` インスタンスのメモリ内だけに保持し、`clear_history()` で消去できます。

### 質問・依頼本文をLLMへ渡す場所

Whisperが認識した現在の発話は `turn.transcription.text` に入ります。`VoicePipeline` はこの本文を `VoiceLLMClient.generate_response()` へ渡し、`voice_llm.py` がLM Studioのリクエスト `input` に「今回のユーザー発話（質問・依頼）」として設定します。直近の会話履歴は「以前の会話」として別に含め、話し方の指定を置く `system_prompt` には質問本文を入れません。空の文字起こしはLLMへ送りません。

## AI_Secretary から活用した点

`C:\Users\ryuuz\Downloads\AI_Secretary-main\AI_Secretary-main` を確認し、次の設計を参考に自作コードへ反映しました。

- 音声会話用の短い日本語プロンプトと、会話履歴の上限
- LM Studio の native `POST /api/v1/chat`、`reasoning=off`、`store=false`
- STT → LLM → TTS を段階ごとに分け、処理時間を測る考え方
- LLMの返答を待つ間に重ならない短い相づちを使い、音声を事前生成・キャッシュする考え方
- 録音を既定でファイル保存しない方針
- TTS の先行チャンクを再生して待ち時間を抑える案（将来の改善項目）

Discord Gateway、DAVE、Opus デコード、Discord 専用音声再生はこのプロジェクトへ持ち込んでいません。元のIrodori-TTSのGradio呼び出し方式だけを参考にし、macOSのMPS固定値は使わず、デバイスや精度を環境設定から渡します。

## TTS設定

`app/.env.example` を `.env` にコピーして、LM Studio と Irodori-TTS の接続先を設定します。TTS側で `Aratako/Irodori-TTS-600M-v3-VoiceDesign` と Gradio API `/_run_generation` が利用できることが前提です。任意の参照音声を使う場合は `VOICE_TTS_REFERENCE_AUDIO` にサーバー側の音声ファイルを指定します。合成結果は上限サイズを確認してバイト列で返し、アプリ側で別の保存・削除処理は行いません。

## LLM クライアント

`voice_llm.py` は、テキストと直近の会話履歴を LM Studio に送り、短い返答を返します。応答本文に加え、呼び出されたツール名と生成統計を `VoiceLLMReply` で返します。会話本文をこのクライアントがログやファイルへ保存する処理はありません。`store=false` で要求し、LM Studio のAPI設定に従って処理します。

MCP を有効にすると、LM Studio へ登録した `mcp/nen-gitsune-tail` のうち、`tap_tail`、`stop_tail`、`get_tail_status` だけを許可します。既定では `TAIL_MCP_ENABLED=false` です。

## 相づちの音声設計

相づちはMVPの必須要件です。`voice_acknowledgements.py` が短い相づち音声を事前生成してメモリにキャッシュし、LLMの返答を設定時間だけ待ってから最大1回通知します。候補をターンごとに交互に使い、音声ファイルとして保存しません。

STT後のLLM待機中のみ通知し、本返答が先に届いた場合は省略します。「おやすみ」や停止要求には追加の相づちを出しません。相づちコールバックはPi 4の再生完了まで待つ約束で、本返答との再生重複を防ぎます。コールバックを実装するデバイスAPIはまだないため、実機再生・停止との統合は未確認です。待ち時間、声、音量は実機で調整します。

## しっぽ制御 MCP

`tail_mcp_server.py` は LM Studio が stdio で起動するローカル MCP サーバーです。MCP と Raspberry Pi 4 は別の接続区間です。

```text
LM Studio / voice_llm.py
        │ MCP (stdio; LM Studio がローカルサーバーを起動)
        ▼
tail_mcp_server.py
        │ HTTP (同一LAN上のRaspberry Pi 4を想定)
        ▼
Raspberry Pi 4 → SG90サーボ → 柔らかいしっぽ
```

公開するツールは3つです。

| MCP ツール | 用途 | 制限 |
| --- | --- | --- |
| `tap_tail` | しっぽをトントンさせる | 0.5〜5秒、毎秒0.3〜1.0回。角度は指定不可 |
| `stop_tail` | 動作を停止する | 起動許可が無効でも停止要求を送る |
| `get_tail_status` | Raspberry Pi 4の状態を確認する | 状態だけを取得 |

### Raspberry Pi 4 デバイス API

音声入出力とサーボ操作の契約は [Raspberry Pi 4 API仕様](../RASPBERRY_PI_API.md) にまとめています。HTTPS上で音声WAVとJSON制御を転送します。現行MCPコードは旧 `/api/tail/...` パスを呼び、契約に未対応です。

Pi 4デバイスサービス、セッション／停止世代との連携、遅着開始の拒否、機構の制限と手元停止は未実装・未確認です。停止制御と実機安全確認が終わるまでは `TAIL_CONTROL_ENABLED=false` のままにします。

## 設定と起動

Python 3.11 以上を使い、このフォルダーで仮想環境を作成して依存関係を入れます。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` にLM Studioの設定を記入します。しっぽを使う場合はPi 4デバイスサービスを用意し、実機確認後に `TAIL_DEVICE_URL` と `TAIL_CONTROL_ENABLED=true` を設定します。MCP単独での起動確認には次を使えます。

```powershell
.\.venv\Scripts\python.exe .\tail_mcp_server.py
```

stdioサーバーはMCPクライアントが起動して接続するため、このコマンドを手動実行した場合は入力待ちになります。

LM Studioの `mcp.json` に `mcp.json.example` のサーバー項目を登録し、LM Studioのサーバー設定で `mcp.json` のサーバー呼び出しとAPI認証を有効にします。API認証用に発行したトークンは `.env` の `LM_STUDIO_API_KEY` に設定してください。`voice_llm.py` 側の `TAIL_MCP_ENABLED=true` も設定すると、APIリクエストで `mcp/nen-gitsune-tail` を利用します。

## 現在の実装境界

- 実装済み: faster-whisper STT、LM Studio音声用クライアント、Irodori-TTS Gradioクライアント、既存音声ファイルから再生用音声バイト列を返すSTT→LLM→TTSパイプライン、相づち音声キャッシュと通知コールバック、限定ツール連携のリクエスト生成、MCPサーバー、旧パスを使う汎用HTTPクライアント。
- 文書化済み・未実装: Raspberry Pi 4向けバージョン付きHTTP API契約（[RASPBERRY_PI_API.md](../RASPBERRY_PI_API.md)）。現在のクライアントのパス・応答形式、セッション停止世代との連携は未整合。
- 未実装: Pi 4デバイスサービス、音声録音・アップロード、Pi 4スピーカー配信と再生完了通知、セッション管理・優先停止。
- 未確認: Whisper・LM Studio・Irodori-TTSの実モデル接続と性能、Pi 4での音声録音・再生、LM Studioと指定モデルでのツール呼び出し、Pi 4サーボの強さ・速度・接触位置・騒音・発熱。

MCP SDK は公式 Python SDK の v2 API (`MCPServer`, stdio transport) を使用しています。LM Studioの native MCP integration は LM Studio 0.4.0 以上が必要です。

## 参考資料

- [MCP Python SDK: サーバー作成とツール](https://github.com/modelcontextprotocol/python-sdk/tree/main/docs)
- [LM Studio: MCPをREST APIから利用する](https://lmstudio.ai/docs/developer/core/mcp)
- [LM Studio: Chat API](https://lmstudio.ai/docs/developer/rest/chat)
- [faster-whisper: 公式リポジトリとモデル保存先の設定](https://github.com/SYSTRAN/faster-whisper)
