# Android Web 音声会話 API

レビュー反映日: 2026-10-24。Android WebView の同一オリジン画面から、スマートフォンのマイク音声を1発話ずつ送り、サーバーが STT → LLM → TTS を実行します。入力はターン単位のアップロードですが、認識後の返答テキストとTTS音声は逐次ストリームで受信できます。連続全二重マイク配信ではありません。

設定画面と同じ `NENGITSUNE_DASHBOARD_TOKEN` を Bearer token として使用します。音声モデルの稼働確認は初回の発話処理で行われ、`GET /api/v1/voice/status` はAPI経路の存在だけを示します。Android WebViewのマイク利用にはHTTPSのセキュアコンテキストが必要です。

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

## エンドポイント

### `GET /api/v1/voice/status`

音声APIの制限と現在のセッション数を返します。Whisper、LM Studio、Irodori-TTSの接続までは検査しません。

### `POST /api/v1/voice/sessions`

会話履歴を持つセッションを作成します。相づちがダッシュボードで有効な場合、サーバーは会話開始後に候補音声のキャッシュ生成をバックグラウンドで開始します。

```json
{
  "session_id": "UUID",
  "state": "active",
  "generation": 0,
  "expires_in_seconds": 900
}
```

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
- Android Webの逐次再生は、サーバーが選んだ音声を順番に再生します。再生開始・完了・失敗の個別通知APIは未実装です。
- Pi 4しっぽ制御もサーバー側の責務ですが、サーバー経由のクライアント操作APIとPi 4接続は未実装です。
- サーバー起動、モデル接続、HTTPS終端、Android実機での音声往復、無音・破損音声、停止競合、再接続は未確認です。
