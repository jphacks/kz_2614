# STT → LLM → TTS

このパッケージは音声処理と会話制御を担当します。`stt/`、`llm/`、`tts/` はモデル接続、`voice/` はパイプライン・セッション・相づちを担当します。設定は兄弟の `../dashboard/settings_store.py` から読み、Whisperモデルキャッシュは `../whisper/` に保存します。ブラウザー画面とWebアプリ用HTTP受付は兄弟の [`dashboard/`](../dashboard/README.md) にあります。外部ソースは `third_party/` に置きます。

サーバーの要件と現行コードとの差分は [要件定義](../REQUIREMENTS.md) を参照してください。`VoicePipeline` は音声を無音区間で分割し、Whisperの信頼度と声の高さ・抑揚・声量を分析してから、音響要約と文字起こしをLM Studioへ渡します。Android Web向けの逐次経路ではLM Studio SSEを読みながら文節ごとにTTSを生成し、NDJSONイベントで音声チャンクを配信します。Android Webは受信順に相づちと返答音声を再生します。相づち音声は会話開始時にバックグラウンドで事前生成します。モデル・実機での動作は未確認です。

Web画面サーバーの起動方法、Web API、データ保存先は [dashboardのREADME](../dashboard/README.md) を参照してください。このPCのモデル接続と実測は [LOCAL_SETUP.md](../../../LOCAL_SETUP.md) に記録します。HTTPS環境・Android実機での動作は未確認です。

## Whisper → LM Studio → Irodori-TTS

`stt/whisper.py` はPyAVで対応音声を16kHzモノラルへ復号し、約0.7秒の無音で発話を分割して `faster-whisper` へ渡します。各発話は0.25秒以上、最大15秒です。信頼度・無音確率を用いて無音誤認識を抑え、基本周波数、抑揚、声量の目安を推定します。同じモデルID・デバイス・精度のモデルはプロセス内で共有し、推論を直列化します。設定変更で別モデルをロードした場合、古いモデルのGPUメモリを解放するにはサーバーを再起動してください。`voice/pipeline.py` は文字起こしと音響特徴の要約を `llm/lm_studio.py` へ渡し、返答本文をLM Studio SSEから受け取ります。句点・疑問符・読点や文節長で区切ったテキストを `tts/irodori.py` に送り、生成ごとにAndroid Webへ配信します。`voice/sessions.py` は会話セッション・停止世代と配信イベントを管理し、会話履歴は `VoicePipeline` インスタンス内に保持します。

`tts/irodori.py` は `gradio_client` を使い、`IRODORI_URL` の Gradio API `/_run_generation` を呼び出します。同じIrodori-TTSサーバーへの生成要求は直列化します。取得した一時音声ファイルは音声バイト列へ読み込んだあと、OSの一時フォルダー内にある場合だけ削除します。Irodori-TTS のモデル一式はここには含めず、互換サーバーを別途起動してください。GPUのない環境では、`.env` でモデルとcodecのデバイスを `cpu`、精度を `fp32` に変更します。モデル、デバイス、速度、Android WebViewでの再生形式は実機確認が必要です。

`voice/acknowledgements.py` は「うん、聞いてるよ。」「そっか、話してくれてありがとう。」の音声を会話開始時にバックグラウンドで同じTTS設定で生成し、メモリにキャッシュします。最初の返答TTS音声が `VOICE_ACK_DELAY_SECONDS`（既定0.9秒）以内に準備できない場合だけ1つを交互に選び、逐次ストリームへ載せます。「おやすみ」や停止要求には相づちを出しません。Android WebがNDJSON受信順に再生キューへ入れるため、本返答と音声を重ねません。再生開始・完了・失敗の通知APIと実機再生は未確認です。

```text
音声アップロード → PyAV復号 → 無音区間分割 → WhisperSTT → 文字起こし＋音響特徴
                                                        ↓
Android再生キュー ← NDJSON音声チャンク ← IrodoriTTS ← LM Studio SSE
                       ↑ 相づちキャッシュ ───────────────┘
```

初回は `Systran/faster-whisper-small` をダウンロードします。モデルは `Programs/ServerSide/whisper/` に保存します。既定はCUDA / float16で、NVIDIAドライバーとCUDA 12のcuBLAS、cuDNN 9が必要です。CUDAランタイムまたはGPUが見つからない場合、STTは理由を示して停止します。CPUで動かす場合はダッシュボードでCPU / int8を選択するか、`.env` で `WHISPER_DEVICE=cpu`、`WHISPER_COMPUTE_TYPE=int8` を指定してください。変更できる値は `WHISPER_MODEL_ID`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE` です。入力ファイルは `WHISPER_MAX_AUDIO_BYTES`（既定25MB）で制限し、STTは `WHISPER_TIMEOUT_SECONDS`（既定90秒）で待ち時間を制限します。モデルの取得時はインターネット接続が必要です。

CUDAランタイムの要件とWindows用DLLの配置方法は、[faster-whisperのGPU要件](https://github.com/SYSTRAN/faster-whisper#gpu) と [CTranslate2のインストール説明](https://opennmt.net/CTranslate2/installation.html) を参照してください。WindowsではCUDAライブラリDLLが `PATH` から見つかる必要があります。

ダッシュボードに保存した設定は新しい会話から反映され、保存後は `.env` の同じ項目より優先されます。設定保存前は `.env` の値が使われ、未指定項目は上記既定値になります。既に動作中の会話には反映されません。

呼び出し側からは、リポジトリのルートで `from Programs.ServerSide.STTLLMTTS.voice.pipeline import VoicePipeline` として使います。

```python
async def handle_recording():
    pipeline = VoicePipeline()
    try:
        # 旧ファイル処理APIの例。Android Webはprocess_audio_file_streamを使います。
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

Whisperが認識した現在の発話は `Transcription.text` に入ります。`VoicePipeline` は文字起こしと、声の高さ・話し方の音響要約を `VoiceLLMClient.generate_response()` または `stream_response()` へ渡し、`llm/lm_studio.py` が今回の発話・音響要約・履歴をLM Studioのリクエスト `input` にまとめます。音響特徴は感情の断定に使わず、話し方をくみ取る参考情報と明記します。空の文字起こしはLLMへ送りません。

## AI_Secretary から活用した点

参考プロジェクト `AI_Secretary-main` を確認し、次の設計を自作コードへ反映しました。

- 音声会話用の短い日本語プロンプトと、会話履歴の上限
- LM Studio の native `POST /api/v1/chat`、`reasoning=off`、`store=false`
- STT → LLM → TTS を段階ごとに分け、処理時間を測る考え方
- faster-whisperモデルの遅延ロードと共有推論ロック、同期モデル処理に対する期限管理の考え方
- LLMの返答を待つ間に重ならない短い相づちを使い、音声を事前生成・キャッシュする考え方
- 録音を既定でファイル保存しない方針
- LM StudioのSSE差分を句読点・文節で区切り、先行TTSチャンクを逐次配信する

Discord Gateway、DAVE、Opus デコード、Discord 専用音声再生はこのプロジェクトへ持ち込んでいません。元のIrodori-TTSのGradio呼び出し方式だけを参考にし、macOSのMPS固定値は使わず、デバイスや精度を環境設定から渡します。

## TTS設定

同じ参照音声の前処理を文節ごとに繰り返さないよう、公式の`ref_latent`入力を利用します。参照内容・codec・正規化・参照長などの設定を区別して、未patchのCPU latentをメモリに最大2件／合計16MiB保持します。元の登録WAVは変更しません。推論へ渡す一時latentは終了・失敗・取消後に削除します。実際の高速化率は実測で確認し、キャッシュ追加だけで数値を推定しません。

割込みや期限切れのTTSは取消flagを持ち、共有推論lockを待つ未開始処理を省きます。すでに動いているnative処理を強制終了する機能ではなく、実処理の完了まで参照ファイルとlockを保持して旧結果を破棄します。時間計測はlock待ち・参照前処理・生成・復号の数値だけを記録し、音声本文・参照内容・hashを記録しません。

`Programs/ServerSide/.env.example` を `.env` にコピーして、LM Studio と Irodori-TTS の接続先を設定します。TTS側で `Aratako/Irodori-TTS-600M-v3-VoiceDesign` と Gradio API `/_run_generation` が利用できることが前提です。任意の参照音声を使う場合は `VOICE_TTS_REFERENCE_AUDIO` にサーバー側の音声ファイルを指定します。合成結果は上限サイズを確認してバイト列で返し、アプリ側で別の保存・削除処理は行いません。

`VOICE_TTS_BACKEND=local` を選ぶと、別Gradioサーバーを使わず公式Pythonランタイムで合成します。追加依存は `../requirements-local-tts.txt`、外部ソースは `IRODORI_SOURCE_DIR`、モデルは `VOICE_TTS_CHECKPOINT`、コーデックは `IRODORI_CODEC_REPO`、キャッシュは `HF_HUB_CACHE` / `HF_HOME` で指定します。取得済みのv4.1-SmallではモデルCUDA / bf16、コーデックCUDA / fp32を使用しています。CPUでは両方cpu / fp32にします。既定の `IRODORI_LOCAL_FILES_ONLY=true` ではモデルとtokenizerを事前に用意してください。同じ設定のモデルは会話間で共有し、推論を直列化します。返答WAVはメモリで生成し、参照音声の処理用コピーは合成後に削除します。

## LLM クライアント

`llm/lm_studio.py` は、テキストと直近の会話履歴を LM Studio に送り、短い返答を返します。応答本文に加え、呼び出されたツール名と生成統計を `VoiceLLMReply` で返します。会話本文をこのクライアントがログやファイルへ保存する処理はありません。`store=false` で要求し、LM Studio のAPI設定に従って処理します。

`TAIL_MCP_ENABLED=true` にすると、アプリがstdio MCPクライアントを起動し、頻度の保存・取得だけをLM Studioへ公開します。モデルとの通信は `/v1/chat/completions` のtool calling、ツールの実行は `llm/tail_tools.py` が担当します。LM StudioのMCP登録や認証設定の変更は不要です。無効時は従来のnative Chat APIを使用します。既定はfalseです。

## 相づちの音声設計

相づちはMVPの必須要件です。`voice/acknowledgements.py` が短い相づち音声を事前生成してメモリにキャッシュし、LLMの返答を設定時間だけ待ってから最大1回通知します。候補をターンごとに交互に使い、音声ファイルとして保存しません。

STT後のLLM待機中のみ通知し、最初の返答音声が先に準備できた場合は省略します。遅延時には会話ごとに最大1回の音声をNDJSONストリームへ送り、Android Webが返答TTSと順番に再生します。「おやすみ」や停止要求には追加の相づちを出しません。再生完了通知APIと実機再生・停止は未確認です。待ち時間、声、音量は実機で調整します。

## トントン頻度のMCP

今回のESP32接続では頻度設定だけをPCに保存します。ESP32の実装は別担当です。[ESP32担当者向けAPI仕様](../TAIL_FREQUENCY_API.md) に接続契約をまとめています。

```text
音声サーバー ←→ LM Studio (互換Chat API / tool calling)
        │ MCP (stdio; アプリがローカルサーバーを起動)
        ▼
tail_mcp_server.py
        │ HTTP (PC内のloopback)
        ▼
dashboard /api/v1/tail/frequency → 希望頻度のJSON保存
        ▲
        └─ ESP32担当側からGETで取得可能（端末実装は別担当）
```

会話に公開するツールは2つです。MCPサーバーから取得した定義をこの2つに制限し、動作用ツールはモデルへ渡しません。ツール呼出し後の結果をモデルに戻して返答を作り、通常会話の文字差分配信も保持します。1応答につき最大4ラウンド・8回の呼出しと全体期限を設けます。

| MCP ツール | 用途 | 制限 |
| --- | --- | --- |
| `set_tail_frequency` | PCに希望頻度を保存する | 毎秒0.3〜1.0回。角度・動作開始は指定不可 |
| `get_tail_frequency` | PCに保存した希望頻度を取得する | 実機の状態確認とは区別 |

頻度変更の明確な依頼で設定ツールを利用し、PCでの保存成功をESP32の実機反映と伝えないよう指示します。保存処理自体は動作を開始しません。MCPセッションは応答ごとに作成し、完了・失敗・取消時に同じタスク内で閉じます。

### 旧機器操作と将来の接続

互換用の `tap_tail`・`stop_tail`・`get_tail_status` はMCPサーバーに残っていますが、現在の会話へは公開しません。旧 `/api/tail/...` を呼ぶこれらの操作と、今回の頻度設定APIは別機能です。以前のPi 4デバイスAPI案は [Raspberry Pi 4 API仕様](../../../docs/archive/raspberry-pi/RASPBERRY_PI_API.md) に保持しています。

Pi 4デバイスサービス、セッション／停止世代との連携、遅着開始の拒否、機構の制限と手元停止は未実装・未確認です。停止制御と実機安全確認が終わるまでは `TAIL_CONTROL_ENABLED=false` のままにします。

## 設定と起動

Python 3.11〜3.13を使います。WindowsとUbuntu向けのGit・Python導入および依存関係インストール用スクリプトは [`third_party/README.md`](third_party/README.md) を参照してください。スクリプトはServerSide共通の `.venv/` を作成し、`requirements.txt` をインストールします。Windowsで手動セットアップする場合は、リポジトリルートから次を実行します。

```powershell
py -3.11 -m venv Programs\ServerSide\.venv
.\Programs\ServerSide\.venv\Scripts\python.exe -m pip install -r Programs\ServerSide\requirements.txt
Copy-Item Programs\ServerSide\.env.example Programs\ServerSide\.env
```

`.env` にLM Studioの設定を記入します。今回の頻度設定を有効にする場合は `TAIL_MCP_ENABLED=true` と、PC内のダッシュボードURL `TAIL_DASHBOARD_URL=http://127.0.0.1:8765` を設定して音声サーバーを起動します。`TAIL_CONTROL_ENABLED=false` は維持し、`TAIL_DEVICE_URL` は不要です。MCP単独の起動コマンドは次です。

```powershell
.\Programs\ServerSide\.venv\Scripts\python.exe Programs\ServerSide\STTLLMTTS\tail_mcp_server.py
```

stdioサーバーはMCPクライアントが起動して接続するため、このコマンドを手動実行した場合は入力待ちになります。

LM StudioのGUIでもMCPを使う場合に限り、リポジトリルートから `python scripts/configure_tail_mcp.py` で登録内容をプレビューできます。`--install` を付けると該当項目だけを更新し、変更前のバックアップを作ります。`--config` と `--python` で保存先・実行環境を指定できます。この任意登録は音声アプリからのMCP通信には使いません。実際のLM Studio設定ファイルは今回変更していません。

## 現在の実装境界

- 実装済み: PyAV復号と無音区間分割、faster-whisper STT・認識品質・声の高さ・話し方の分析、LM Studio同期応答とSSE差分取得、分析要約を含むLLM入力、Irodori-TTS文節合成、NDJSONイベント配信、Android Webの逐次再生キュー、相づちの事前生成・配信判断、同一設定モデルのプロセス内共有・推論直列化、段階時間と本文を記録しない失敗ログ、段階別期限・停止世代の破棄、限定ツール連携のリクエスト生成、MCPサーバー、旧クライアント向けJSON応答。
- 同期STT・TTSの既定タイムアウトは各90秒、LLMは45秒、音声ターン全体は最大180秒です。タイムアウト後にネイティブ推論スレッドが動作を続ける場合がありますが、そのセッションは破棄し、後着結果を配信しません。実際に処理を強制中断できるとは扱いません。
- 文書化済み・未実装: Raspberry Pi 4向けバージョン付きHTTP API契約（[RASPBERRY_PI_API.md](../../../docs/archive/raspberry-pi/RASPBERRY_PI_API.md)）。現在のクライアントのパス・応答形式、セッション停止世代との連携は未整合。
- 出力開始・完了・失敗通知APIはPCM経路に実装し、自動テストと実モデルHTTPで確認済みです。従来turn APIの個別通知は未実装です。Pi 4しっぽデバイスサービス、MCPとセッション停止世代の連携も未実装です。
- 未確認: Android／ESP32端末での受け入れ、実サーボの強さ・速度・接触位置・騒音・発熱。PC上での実モデル確認はリポジトリルートの `LOCAL_SETUP.md` と `Programs/WORK_HISTORY.md` を参照してください。

MCP SDK は公式 Python SDK の v2 API (`MCPServer`, stdio transport) を使用しています。音声アプリのMCP経路はLM Studioの互換tool callingを利用します。

## 参考資料

- [MCP Python SDK: サーバー作成とツール](https://github.com/modelcontextprotocol/python-sdk/tree/main/docs)
- [LM Studio: 互換APIのtool calling](https://lmstudio.ai/docs/developer/openai-compat/tools)
- [LM Studio: Chat API](https://lmstudio.ai/docs/developer/rest/chat)
- [faster-whisper: 公式リポジトリとモデル保存先の設定](https://github.com/SYSTRAN/faster-whisper)
