# Android Web 音声会話 API

レビュー反映日: 2026-10-24。継続PCM入力とサーバーVADによる途中認識を追加し、従来の1発話ファイル送信・NDJSON逐次返答も保持します。PCM経路でも発話確定後にLLMの差分と文節TTSを逐次配信します。音声入力POSTと返答受信GETは独立した接続で並行し、返答生成・再生中も無音を含むPCM送信を継続します。

設定画面と同じ `NENGITSUNE_DASHBOARD_TOKEN` をBearer tokenに使います。別端末からはHTTPS、同一PCのlocalhost開発ではHTTPを使用できます。`GET /api/v1/voice/status` はAPIの存在と制限を示し、モデルの接続は保証しません。稼働状況と実環境での確認範囲は `LOCAL_SETUP.md` と作業履歴に記録します。入力中の会話を止める更新は行いません。

## 音声条件

| 項目 | 値 |
| --- | --- |
| 入力形式 | `audio/webm`、`audio/ogg`、`audio/wav`、`audio/mp4` |
| 最大サイズ | 25,000,000 bytes |
| 最大長 | 60 seconds。サーバーで音声を復号して長さを検査する |
| 同時会話 | 最大4セッション。各セッション1発話ずつ |
| セッション期限 | 操作が15分ないセッション |
| 録音ファイル | Whisper処理中だけOS一時フォルダーに置き、処理後に削除 |

ブラウザーが `MediaRecorder` で対応する形式を選び、`Content-Type` に記載します。サーバーは形式とコンテナ署名を照合し、任意のサーバーファイルパスを受け取りません。音声はPyAVで16kHzモノラルへ復号し、約0.7秒以上の低音量区間で分割します。0.25秒未満の短い区間は除外し、各区間を最大15秒に制限してWhisperへ渡します。WhisperのVADも有効設定時は各区間で実行します。無音上の誤認識を抑えるため、文字起こし信頼度と無音確率の両方が閾値を超えた結果は破棄します。

STTは音声の基本周波数を約70〜400Hzの範囲で推定し、声の高さ、抑揚、声量の目安を算出します。これらは感情の断定に使わず、文字起こしと会話履歴を補う参考情報としてLLMの `input` に含めます。

現行のWebクライアントは下記PCM経路を使います。無音も継続送信し、サーバーが発話開始・途中認識・終端を判定します。マイクは返答中も保持し、サーバーが処理・再生・クールダウン中の音声を認識から除外します。入力マイクを変更・切断した場合は手動再開を案内します。

## エンドポイント

### `GET /api/v1/dashboard/settings` / `PUT /api/v1/dashboard/settings`

既存のSTT・TTS・会話設定に `vad` セクションを追加します。保存は新しい会話から反映し、開始済みの会話のSilero設定は変更しません。

```json
{"vad":{"threshold":0.65,"end_threshold":0.35,"min_speech_ms":384,"silence_ms":896,"preroll_ms":640,"max_speech_seconds":20}}
```

開始・終了しきい値は `0 < end_threshold < threshold < 1`、最小発話時間は128〜1000ms、無音による終端待ちは320〜2000ms、先頭保持は `min_speech_ms + 128` 以上2000ms以下、最大発話長は1〜30秒です。NaN・無限大・真偽値・不正な型や範囲は拒否します。`stt.vad_filter` はWhisper用で、この連続PCM入力のSilero設定とは独立です。保存ファイルや旧クライアントの要求で `vad` が欠けている場合は、それまでの有効なVAD設定を保持します。

### `GET /api/v1/dashboard/runtime`

ダッシュボードと同じBearer認証を要求します。`checked_at`、`active_sessions`、`components.stt/llm/tts`、`vad` を返します。各部品の `configured` は次の会話に使う設定、`active` は進行中の会話インスタンスの観測値です。複数の会話で異なる設定を使用している場合も各値を保持します。

状態は未確認、読み込み中、読み込み済み、処理中、最終成功、失敗を区別し、実処理で確認したモデル・デバイス・精度と最終成功時刻を返します。要求したデバイスと実測できたデバイスは区別し、外部サービスから確認できないGPU配置は未確認にします。最終成功は常時接続の保証ではありません。`vad.configured` は保存・環境値、`vad.active` は既存PCMストリームの設定です。

このAPIはセッション作成、期限切れ処理、VADリセット、推論やモデルロード、外部サービスへの接続試験を行いません。発話本文・音声・APIキー・ローカルパスを返しません。現在値を取得できない場合は秘密情報を含まない `503 runtime_unavailable` を返します。

### `GET /api/v1/voice/status`

音声APIの制限と現在のセッション数を返します。Whisper、LM Studio、Irodori-TTSの接続までは検査しません。

### `POST /api/v1/voice/sessions`

会話履歴を持つセッションを作成します。相づち候補の事前生成はNDJSON逐次返答を要求した時だけ開始し、PCM入力開始時には実行しません。

```json
{
  "session_id": "UUID",
  "state": "active",
  "generation": 0,
  "expires_in_seconds": 900
}
```

### `POST /api/v1/voice/sessions/{session_id}/audio-stream`

ブラウザーと将来のESP32で共通のPCM入口です。VADをクライアントへ実装する必要はありません。

| 項目 | 契約 |
| --- | --- |
| Content-Type | `audio/pcm;rate=16000;channels=1;format=s16le` |
| 音声 | リトルエンディアン符号付き16-bit、16kHzモノラル。無音も送信 |
| フレーム | 1〜32,000 bytes、偶数バイト。Web既定は4096 samples（256ms） |
| 世代 | `X-Voice-Generation` に開始時の世代 |
| 連番 | `X-Audio-Sequence` を0から1ずつ増加。同一フレームの再送は同じ番号 |
| 入力音量 | `X-Audio-Input-Gain` に0.25〜2.0（省略時1.0）。サーバーがPCMに適用して16-bit範囲へクリップ |

```json
{"session_id":"UUID","generation":0,"sequence":0,"next_sequence":1,"stage":"listening","turn_id":null,"transcript":""}
```

ブラウザーはPCM自体を加工せず、音量倍率をフレームと一緒に送ります。会話中も音量を変更でき、同一番号の再送では元の音声と倍率を維持します。VAD・途中認識・確定STTはサーバーが倍率を適用した音声で行います。不正な倍率は400 `invalid_input_gain` で拒否します。

重複番号は再認識せずACKを返し、番号が飛ぶ場合は409 `audio_sequence_gap` と `expected_sequence` を返します。処理中・返答準備済み・再生中・クールダウン中の音声はACKして認識から除外します。途中文字は約1秒間隔のサーバー側STTで更新され、確定後に修正されることがあります。

サーバーのSilero VADは32msフレームで状態を保持します。既定は開始確率0.65、終端確率0.35、継続発話384ms、終端無音896ms、先行音声640ms、最大発話20秒です。`VOICE_VAD_*` の環境変数で変更します。受信バッファー、途中推論と確定推論の並行数には上限があります。PCMはメモリと処理用一時WAVだけで扱い、処理後に削除します。

### `GET /api/v1/voice/sessions/{session_id}/audio-stream`

`X-Voice-Generation` を指定し、送信時と同じ現在状態を取得します。`stage` は `listening`、`speaking`、`stt`、`llm`、`tts`、`reply_ready`、`playing`、`cooldown`、`failed`。返答完了時は `response_text` と期限付き `speech.url` を返します。`output_streaming: true` の場合、下記eventsから文節音声を再生します。`turn_complete` はサーバー側の生成完了、`event_cursor` は最新イベント番号です。`speech.url` があっても、逐次再生した返答を重ねて再生しません。

### `GET /api/v1/voice/sessions/{session_id}/audio-stream/events?after={cursor}`

PCM入力とは独立した、返答用の長期NDJSON接続です。`X-Voice-Generation` を付け、初回は `after=0`、再接続は最後に受け取ったcursorを指定します。Bearer認証はPCM入力と同じです。

`transcript`、`text_delta`、`acknowledgement`、`audio_chunk`、`complete`、`error` は従来の逐次応答と同じ内容に、`cursor`、`session_id`、`generation`、`turn_id` を付けます。cursorはセッション全体で1から増えます。音声は `media_type` と `audio_base64` で返し、Webは先頭の音声から受信順に再生します。返答全文と全音声の完成は待ちません。

接続は最大25秒で正常終了し、同じcursorから再接続します。無送信時は最大10秒ごとに `heartbeat` を返します。heartbeatのcursorは受信位置の確認だけで増加しません。再接続で重複したデータは再生しません。停止・世代変更後のデータも破棄します。

サーバーの再送用イベントは各セッション256件／16MiB以内に制限します。受信位置が保持範囲より古い場合は409 `events_expired`、先行した場合は409 `invalid_event_cursor` です。黙って音声を欠落させず、クライアントは会話の再開を案内します。長期受信接続は全体8本までで、超過時は503 `event_readers_busy` を返します。

逐次応答開始前の失敗は通常のJSONです。開始後は `type: error`、`turn_id: null` の制御イベントを送って閉じます。この制御イベントはheartbeatと同様にcursorを増やさないため、クライアントは重複判定より先に処理します。出力接続の切断だけでは、マイク入力やサーバーの生成処理は停止しません。

### `POST /api/v1/voice/sessions/{session_id}/audio-stream/playback`

`Content-Type: application/json` で `generation`、`turn_id`、`event`（`started` / `ended` / `failed`）を送ります。最初の文節の実再生で `started` を通知します。`ended` は `complete` を受信し、かつ全音声の再生キューが空になった時だけ送ります。生成途中の再生終了通知は拒否します。完了後は600msの `cooldown` を経て待受へ戻ります。音声送信そのものは全段階で継続し、認識を待つかどうかはサーバーが決めます。失敗は手動再生待ちとし、自動で待受へ戻しません。古い世代・発話・停止済み会話の通知は拒否します。完了通知の再送は同じ発話で冪等です。

### `POST /api/v1/voice/sessions/{session_id}/transcription`

録音中の途中認識です。`Content-Type` とraw audio本文に加え、`X-Voice-Generation` にセッション開始時の世代を指定します。Whisperだけを実行し、LLM・TTSは呼びません。無音は空文字を返します。

```json
{ "session_id": "UUID", "generation": 0, "transcript": "途中まで聞き取った文字" }
```

旧WebMクライアントは発話の検知後、約1.2秒ごとに最新の録音prefixを送り、返却文字で表示を置き換えます。各要求はコンテナの先頭ヘッダーから現在までの音声を含み、単独の後続Blobだけは送信しません。要求は1件ずつ処理し、古い音声をキューに積みません。単一セッションで認識中、または確定turn処理中の追加previewは409で拒否します。録音を止めたら途中要求を中止し、遅着した途中結果は使いません。途中の文字は確定時に修正される場合があります。

WebSocket接続ではなく、録音中の短い間隔のHTTP送信と途中認識を組み合わせます。更新には送信間隔と推論時間の遅延があります。MediaRecorderの途中Blobの再生可能性は形式・ブラウザーに依存します。[録画仕様](https://www.w3.org/TR/mediastream-recording/)が保証するのは完了した録音の全Blobを連結した結果です。このPCのChromium系ブラウザーが使うWebM/Opusを優先し、途中データが読めない場合は次のデータで再試行して画面にも示します。Android・MP4での途中認識は別途確認が必要です。

### `GET /api/v1/voice/sessions/{session_id}/turns/{turn_id}/status`

確定音声を送信した後の進捗です。`stage` は `stt`、`llm`、`tts`、`complete`、`failed`。STT完了時点から `transcript` を返し、LLM/TTS完了を待たず表示できます。認証は音声APIと同じです。

```json
{ "session_id": "UUID", "turn_id": "UUID", "generation": 0, "stage": "llm", "transcript": "確定した発話" }
```

現在の発話1件だけをメモリに保持します。次の発話、停止・世代変更・期限切れ後は404。進捗取得自体はセッションの期限を延ばしません。音声や認識本文はログに記録しません。

### `POST /api/v1/voice/sessions/{session_id}/turns/{turn_id}`

Raw audio bytes を本文として送ります。`Content-Type` は上記の対応形式です。

#### 逐次応答

`Accept: application/x-ndjson` を付けると、HTTP/1.1 chunked responseでUTF-8のNDJSONイベントを返します。各行はJSONオブジェクト1つです。

クライアントは録音終了後に音声ファイルを1回アップロードします。サーバーは受信完了後に音声を分割してSTTし、認識後はLLMの返答テキストと文節ごとのTTS音声を順次配信します。録音中のリアルタイムSTTや全二重通話ではありません。

| `type` | 内容 |
| --- | --- |
| `transcript` | 認識テキスト、音響特徴の要約、Whisperの信頼度・区間数 |
| `text_delta` | LM Studioから届いた返答テキストの差分 |
| `acknowledgement` | 遅延時に最大1回送る相づち音声 |
| `audio_chunk` | 句読点や長さで分けて生成した返答TTS音声 |
| `complete` | 最終文字起こし、返答全文、段階時間 |
| `error` | 処理途中の失敗。`error`、`message`、`retryable` を含む |

`acknowledgement` と `audio_chunk` は `media_type` と `audio_base64` を含みます。Android Webは受信順の再生キューで一つずつ再生し、音声を重ねません。相づちは候補音声を事前生成して使い、既定0.9秒以内に最初の返答音声を用意できた場合は省略します。停止時はクライアントがHTTP読取とローカル再生を中断し、セッション停止でサーバーが現在世代を無効化します。

例:

```json
{"type":"transcript","session_id":"UUID","turn_id":"UUID","generation":0,"text":"今日は少し寂しい","analysis":{"pitch":"中くらい（中央値約160Hz）","delivery":"声量の目安は控えめ、抑揚は穏やか"},"quality":{"speech_segment_count":1}}
{"type":"text_delta","session_id":"UUID","turn_id":"UUID","generation":0,"text":"そばにいるよ。"}
{"type":"audio_chunk","session_id":"UUID","turn_id":"UUID","generation":0,"index":0,"text":"そばにいるよ。","media_type":"audio/wav","audio_base64":"..."}
{"type":"complete","session_id":"UUID","turn_id":"UUID","generation":0,"transcript":"今日は少し寂しい","response_text":"そばにいるよ。","timings_seconds":{"stt":0.0,"llm":0.0,"tts":0.0,"total":0.0}}
```

`Accept` に逐次応答を指定しない既存クライアントには、文字起こし・返答テキスト・処理時間を含むJSONを返し、合成音声を期限付きの `speech.url` から取得できます。この互換経路は全文応答後の一括TTSです。

同じセッションの重複・並行発話は `409`、サイズ超過は `413`、無音は `422`、処理期限超過は `504` で拒否します。段階期限はSTT 90秒、LLM 45秒、TTS 90秒、全体期限は180秒です。逐次応答開始前のエラーは通常のJSON、開始後のエラーはNDJSON `error` イベントです。段階期限超過ではセッションを無効化し、後着の音声イベントを配信しません。同期モデル処理のスレッドは期限超過後も動く場合があり、強制終了は保証しません。CUDA設定時にNVIDIA GPUまたはCUDAランタイムが利用できない場合は `503 cuda_unavailable` です。

### `GET /api/v1/voice/sessions/{session_id}/turns/{turn_id}/audio`

逐次応答を使わない旧クライアント向けです。期限内かつ現在の停止世代に属するTTS音声をバイナリで返します。

### `DELETE /api/v1/voice/sessions/{session_id}`

セッションを停止し、停止世代を進め、処理中タスクへキャンセルを通知します。応答の `audio_device_stop_confirmed` は、このAPIがスマートフォン内のAndroid音声機器を直接制御していないため `false` です。Web UIは端末側の録音・再生も停止します。

## データと確認範囲

- 録音音声はログへ記録せず、処理用の一時ファイルを削除します。会話履歴と相づちキャッシュはセッションのメモリ内だけで保持します。
- PCM経路はNDJSONイベントで返答を受信し、文節ごとに再生します。従来のファイル送信NDJSONも保持します。PCM経路の再生通知は上記を使い、従来APIの個別通知は未実装です。
- Pi 4しっぽ制御もサーバー側の責務ですが、サーバー経由のクライアント操作APIとPi 4接続は未実装です。
- このPCの既存サービスではSTT → 指定LLM → ローカルTTS、無音・破損音声、ブラウザー再生を確認済み。新PCM経路・NDJSON統合後の実モデル試験、HTTPS、Android／ESP32実機での受け入れは未実施です。
