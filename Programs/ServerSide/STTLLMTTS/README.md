# STT → LLM → TTS

このパッケージは音声処理と会話制御を担当します。`stt/`、`llm/`、`tts/` はモデル接続、`voice/` はパイプライン・セッション・相づちを担当します。設定は兄弟の `../dashboard/settings_store.py` から読み、Whisperモデルキャッシュは `../whisper/` に保存します。ブラウザー画面とWebアプリ用HTTP受付は兄弟の [`dashboard/`](../dashboard/README.md) にあります。外部ソースは `third_party/` に置きます。

サーバーの要件と現行コードとの差分は [要件定義](../REQUIREMENTS.md) を参照してください。`VoicePipeline` は入力音声を STT → LLM → TTS で処理し、文字起こし・返答・再生用音声バイト列・各段階の所要時間を返します。Android Web向けのセッション処理もこのパッケージにあります。遅延時の相づち音声キャッシュと通知コールバックはありますが、ブラウザー再生経路にはまだつながっていません。

Web画面サーバーの起動方法、Web APIの認証、データ保存先は [dashboardのREADME](../dashboard/README.md) を参照してください。モデル・HTTPS環境・Android実機での動作は未確認です。

## Whisper → LM Studio → Irodori-TTS

`stt/whisper.py` は `faster-whisper` で音声ファイルを日本語文字起こしし、モデルを `../whisper/` に保存します。同じモデルID・デバイス・精度のモデルはプロセス内で共有し、推論を直列化します。設定変更で別モデルをロードした場合、古いモデルのGPUメモリを解放するにはサーバーを再起動してください。`voice/pipeline.py` は文字起こしを `llm/lm_studio.py` に渡し、LLMの返答を `tts/irodori.py` から Irodori-TTS へ送り、返却された音声をメモリ上のバイト列として返します。`voice/sessions.py` は会話セッションを管理し、会話履歴は `VoicePipeline` インスタンス内に保持します。

`tts/irodori.py` は `gradio_client` を使い、`IRODORI_URL` の Gradio API `/_run_generation` を呼び出します。同じIrodori-TTSサーバーへの生成要求は直列化します。取得した一時音声ファイルは音声バイト列へ読み込んだあと、OSの一時フォルダー内にある場合だけ削除します。Irodori-TTS のモデル一式はここには含めず、互換サーバーを別途起動してください。GPUのない環境では、`.env` でモデルとcodecのデバイスを `cpu`、精度を `fp32` に変更します。モデル、デバイス、速度、Android WebViewでの再生形式は実機確認が必要です。

`voice/acknowledgements.py` は「うん、聞いてるよ。」「そっか、話してくれてありがとう。」の音声を会話開始前に同じTTS設定で生成し、メモリにキャッシュします。LLMの返答が `VOICE_ACK_DELAY_SECONDS`（既定0.9秒）を超える場合だけ1つを交互に選びます。「おやすみ」や停止要求には相づちを出しません。本返答と重ねないため、Android Webの再生完了通知を相づち配信経路へ接続する必要がありますが、現在は未実装です。

```text
音声ファイル → WhisperSTT → 文字起こし → VoiceLLMClient → LM Studio → IrodoriTTS
                                                           └─ 任意でしっぽ制御MCP
再生用音声バイト列 ←────────────────────────────────────────────────────┘
```

初回は `Systran/faster-whisper-small` をダウンロードします。モデルは `Programs/ServerSide/whisper/` に保存します。既定はCUDA / float16で、NVIDIAドライバーとCUDA 12のcuBLAS、cuDNN 9が必要です。CUDAランタイムまたはGPUが見つからない場合、STTは理由を示して停止します。CPUで動かす場合はダッシュボードでCPU / int8を選択するか、`.env` で `WHISPER_DEVICE=cpu`、`WHISPER_COMPUTE_TYPE=int8` を指定してください。変更できる値は `WHISPER_MODEL_ID`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE` です。入力ファイルは `WHISPER_MAX_AUDIO_BYTES`（既定25MB）で制限し、STTは `WHISPER_TIMEOUT_SECONDS`（既定90秒）で待ち時間を制限します。モデルの取得時はインターネット接続が必要です。

CUDAランタイムの要件とWindows用DLLの配置方法は、[faster-whisperのGPU要件](https://github.com/SYSTRAN/faster-whisper#gpu) と [CTranslate2のインストール説明](https://opennmt.net/CTranslate2/installation.html) を参照してください。WindowsではCUDAライブラリDLLが `PATH` から見つかる必要があります。

ダッシュボードに保存した設定は新しい会話から反映され、保存後は `.env` の同じ項目より優先されます。設定保存前は `.env` の値が使われ、未指定項目は上記既定値になります。既に動作中の会話には反映されません。

呼び出し側からは、リポジトリのルートで `from Programs.ServerSide.STTLLMTTS.voice.pipeline import VoicePipeline` として使います。

```python
async def handle_recording():
    pipeline = VoicePipeline()
    try:
        # Android Webへの相づち配信は未実装のため、この例では本返答だけを処理する。
        turn = await pipeline.process_audio_file("path/to/recording.wav")
        print(turn.transcription.text)
        print(turn.response.text)
        # dashboard/server.py がWeb API経由で返答音声を配信します。
        print(len(turn.speech.audio_bytes), turn.speech.media_type)
        print(turn.timings_seconds)
    finally:
        await pipeline.close()
```

`VoicePipeline.process_audio_file(path)` は呼び出し元が用意した既存ファイルを処理します。Android Webの録音アップロードとHTTP配信は兄弟の `dashboard/server.py` が受け付け、音声処理はこのパッケージへ委譲します。認識結果が空の場合はLLMへ送らず `NoSpeechDetected` 例外を送出します。TTS生成に失敗した発話は履歴へ追加しません。履歴は `VoicePipeline` インスタンスのメモリ内だけに保持し、`clear_history()` で消去できます。

### 質問・依頼本文をLLMへ渡す場所

Whisperが認識した現在の発話は `turn.transcription.text` に入ります。`VoicePipeline` はこの本文を `VoiceLLMClient.generate_response()` へ渡し、`llm/lm_studio.py` がLM Studioのリクエスト `input` に「今回のユーザー発話（質問・依頼）」として設定します。直近の会話履歴は「以前の会話」として別に含め、話し方の指定を置く `system_prompt` には質問本文を入れません。空の文字起こしはLLMへ送りません。

## AI_Secretary から活用した点

参考プロジェクト `AI_Secretary-main` を確認し、次の設計を自作コードへ反映しました。

- 音声会話用の短い日本語プロンプトと、会話履歴の上限
- LM Studio の native `POST /api/v1/chat`、`reasoning=off`、`store=false`
- STT → LLM → TTS を段階ごとに分け、処理時間を測る考え方
- faster-whisperモデルの遅延ロードと共有推論ロック、同期モデル処理に対する期限管理の考え方
- LLMの返答を待つ間に重ならない短い相づちを使い、音声を事前生成・キャッシュする考え方
- 録音を既定でファイル保存しない方針
- TTS の先行チャンクを再生して待ち時間を抑える案（将来の改善項目）

Discord Gateway、DAVE、Opus デコード、Discord 専用音声再生はこのプロジェクトへ持ち込んでいません。元のIrodori-TTSのGradio呼び出し方式だけを参考にし、macOSのMPS固定値は使わず、デバイスや精度を環境設定から渡します。

## TTS設定

`Programs/ServerSide/.env.example` を `.env` にコピーして、LM Studio と Irodori-TTS の接続先を設定します。TTS側で `Aratako/Irodori-TTS-600M-v3-VoiceDesign` と Gradio API `/_run_generation` が利用できることが前提です。任意の参照音声を使う場合は `VOICE_TTS_REFERENCE_AUDIO` にサーバー側の音声ファイルを指定します。合成結果は上限サイズを確認してバイト列で返し、アプリ側で別の保存・削除処理は行いません。

## LLM クライアント

`llm/lm_studio.py` は、テキストと直近の会話履歴を LM Studio に送り、短い返答を返します。応答本文に加え、呼び出されたツール名と生成統計を `VoiceLLMReply` で返します。会話本文をこのクライアントがログやファイルへ保存する処理はありません。`store=false` で要求し、LM Studio のAPI設定に従って処理します。

MCP を有効にすると、LM Studio へ登録した `mcp/nen-gitsune-tail` のうち、`tap_tail`、`stop_tail`、`get_tail_status` だけを許可します。既定では `TAIL_MCP_ENABLED=false` です。

## 相づちの音声設計

相づちはMVPの必須要件です。`voice/acknowledgements.py` が短い相づち音声を事前生成してメモリにキャッシュし、LLMの返答を設定時間だけ待ってから最大1回通知します。候補をターンごとに交互に使い、音声ファイルとして保存しません。

STT後のLLM待機中のみ通知し、本返答が先に届いた場合は省略します。「おやすみ」や停止要求には追加の相づちを出しません。Android Webへの相づち配信と再生完了通知は未実装のため、実機再生・停止との統合は未確認です。待ち時間、声、音量は実機で調整します。

## しっぽ制御 MCP

`tail_mcp_server.py` は LM Studio が stdio で起動するローカル MCP サーバーです。MCP と Raspberry Pi 4 は別の接続区間です。

```text
LM Studio / STTLLMTTS.llm.lm_studio
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

Pi 4デバイスAPI案は [Raspberry Pi 4 API仕様](../../ClientSide/Raspi4Ver/RASPBERRY_PI_API.md) にまとめています。文書には音声WAVとJSON制御の契約を含みますが、Android Web音声経路ではPi 4の音声APIを使いません。現行MCPコードは旧 `/api/tail/...` パスを呼び、しっぽAPI契約に未対応です。

Pi 4デバイスサービス、セッション／停止世代との連携、遅着開始の拒否、機構の制限と手元停止は未実装・未確認です。停止制御と実機安全確認が終わるまでは `TAIL_CONTROL_ENABLED=false` のままにします。

## 設定と起動

Python 3.11〜3.13を使います。WindowsとUbuntu向けのGit・Python導入および依存関係インストール用スクリプトは [`third_party/README.md`](third_party/README.md) を参照してください。スクリプトはServerSide共通の `.venv/` を作成し、`requirements.txt` をインストールします。Windowsで手動セットアップする場合は、リポジトリルートから次を実行します。

```powershell
py -3.11 -m venv Programs\ServerSide\.venv
.\Programs\ServerSide\.venv\Scripts\python.exe -m pip install -r Programs\ServerSide\requirements.txt
Copy-Item Programs\ServerSide\.env.example Programs\ServerSide\.env
```

`.env` にLM Studioの設定を記入します。しっぽを使う場合はPi 4デバイスサービスを用意し、実機確認後に `TAIL_DEVICE_URL` と `TAIL_CONTROL_ENABLED=true` を設定します。MCP単独での起動確認には次を使えます。

```powershell
.\Programs\ServerSide\.venv\Scripts\python.exe Programs\ServerSide\STTLLMTTS\tail_mcp_server.py
```

stdioサーバーはMCPクライアントが起動して接続するため、このコマンドを手動実行した場合は入力待ちになります。

LM Studioの `mcp.json` に `Programs/ServerSide/STTLLMTTS/mcp.json.example` のサーバー項目を登録し、LM Studioのサーバー設定で `mcp.json` のサーバー呼び出しとAPI認証を有効にします。この設定例の `command` と `args` には作成元PCの絶対パスが入っているため、別のPCでは各自の仮想環境のPython実行ファイルと `tail_mcp_server.py` の絶対パスに置き換えてください。API認証用に発行したトークンは `Programs/ServerSide/.env` の `LM_STUDIO_API_KEY` に設定してください。`STTLLMTTS/llm/lm_studio.py` 側の `TAIL_MCP_ENABLED=true` も設定すると、APIリクエストで `mcp/nen-gitsune-tail` を利用します。

## 現在の実装境界

- 実装済み: faster-whisper STT（同一設定モデルのプロセス内共有と推論直列化）、LM Studio音声用クライアント、Irodori-TTS Gradioクライアント（サーバーごとの生成直列化）、既存音声ファイルから再生用音声バイト列を返すSTT→LLM→TTSパイプライン、段階ごとの成功・失敗時間ログ（発話本文を記録しない）、段階別タイムアウトと期限切れセッションの破棄、相づち音声キャッシュと通知コールバック、限定ツール連携のリクエスト生成、MCPサーバー、旧パスを使う汎用HTTPクライアント。
- 同期STT・TTSの既定タイムアウトは各90秒、LLMは45秒、音声ターン全体は最大180秒です。タイムアウト後にネイティブ推論スレッドが動作を続ける場合がありますが、そのセッションは破棄し、後着結果を配信しません。実際に処理を強制中断できるとは扱いません。
- 文書化済み・未実装: Raspberry Pi 4向けバージョン付きHTTP API契約（[RASPBERRY_PI_API.md](../../ClientSide/Raspi4Ver/RASPBERRY_PI_API.md)）。現在のクライアントのパス・応答形式、セッション停止世代との連携は未整合。
- 未実装: Android Webへの相づち配信と再生完了通知、Pi 4しっぽデバイスサービス、MCPとセッション停止世代の連携。
- 未確認: Android実機の録音・再生、Whisper・LM Studio・Irodori-TTSの実モデル接続と性能、LM Studioと指定モデルでのツール呼び出し、Pi 4サーボの強さ・速度・接触位置・騒音・発熱。

MCP SDK は公式 Python SDK の v2 API (`MCPServer`, stdio transport) を使用しています。LM Studioの native MCP integration は LM Studio 0.4.0 以上が必要です。

## 参考資料

- [MCP Python SDK: サーバー作成とツール](https://github.com/modelcontextprotocol/python-sdk/tree/main/docs)
- [LM Studio: MCPをREST APIから利用する](https://lmstudio.ai/docs/developer/core/mcp)
- [LM Studio: Chat API](https://lmstudio.ai/docs/developer/rest/chat)
- [faster-whisper: 公式リポジトリとモデル保存先の設定](https://github.com/SYSTRAN/faster-whisper)
