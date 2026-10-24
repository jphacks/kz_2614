# Android Web 音声会話 API

レビュー反映日: 2026-10-24。Android WebView が読み込む同一オリジンの画面から、スマートフォンのマイク音声を1発話ずつ送り、サーバーが STT → LLM → TTS を実行して音声を返す最小契約です。これはターン制の音声会話で、連続全二重通話ではありません。

設定画面と同じ `NENGITSUNE_DASHBOARD_TOKEN` を Bearer token として使用します。音声会話ではHTTPSを使います。音声モデルの稼働確認は初回の発話処理で行われ、`GET /status` はAPI経路の存在だけを示します。

## 音声条件

| 項目 | 値 |
| --- | --- |
| 入力形式 | `audio/webm`、`audio/ogg`、`audio/wav`、`audio/mp4` |
| 最大サイズ | 25,000,000 bytes |
| 最大長 | 60 seconds。Web UIで録音を60秒で止め、Whisperが返す音声長もサーバーで検査する |
| 同時会話 | 最大4セッション。各セッション1発話ずつ |
| セッション期限 | 操作が15分ないセッション |
| 返答音声の保持 | メモリ内で最大2分。会話停止で直ちに破棄 |
| 録音ファイル | Whisper処理中だけOS一時フォルダーに置き、成功・失敗後に削除 |

ブラウザーが `MediaRecorder` で対応する形式を選び、`Content-Type` に記載します。サーバーは形式とコンテナ署名を照合し、任意のサーバーファイルパスを受け取りません。空の音声はLLMへ渡しません。

## エンドポイント

### `GET /api/v1/voice/status`

音声APIの制限と現在のセッション数を返します。Whisper、LM Studio、Irodori-TTSの接続までは検査しません。

### `POST /api/v1/voice/sessions`

会話履歴を持つセッションを作成します。

```json
{
  "session_id": "UUID",
  "state": "active",
  "generation": 0,
  "expires_in_seconds": 900
}
```

### `POST /api/v1/voice/sessions/{session_id}/turns/{turn_id}`

Raw audio bytes を本文として送ります。`Content-Type` は上記の対応形式です。成功時は文字起こし・返答テキスト・音声取得URL・処理時間を返します。

```json
{
  "session_id": "UUID",
  "turn_id": "UUID",
  "generation": 0,
  "transcript": "今回の発話",
  "response_text": "短い返事",
  "speech": {
    "url": "/api/v1/voice/sessions/UUID/turns/UUID/audio",
    "media_type": "audio/wav",
    "expires_in_seconds": 120
  },
  "timings_seconds": {
    "stt": 0.0,
    "llm": 0.0,
    "acknowledgement_preparation": 0.0,
    "acknowledgement": 0.0,
    "tts": 0.0,
    "total": 0.0
  }
}
```

同じセッションの重複・並行発話は `409`、サイズ超過は `413`、無音は `422`、処理期限超過は `504` で拒否します。段階期限はSTT 90秒、LLM 45秒、TTS 90秒、全体期限は180秒です。段階期限超過のコードは `stt_timeout`、`llm_timeout`、`tts_timeout`、全体期限超過は `turn_timeout` です。CUDA設定時にNVIDIA GPUまたはCUDAランタイムが利用できない場合は `503 cuda_unavailable` を返します。段階期限超過ではセッションを無効化し、後着結果を配信しません。同期モデル処理のスレッドは期限超過後も動く場合があり、強制終了は保証しません。各エラーは `{ "error", "message", "retryable" }` の形です。

### `GET /api/v1/voice/sessions/{session_id}/turns/{turn_id}/audio`

期限内かつ現在の停止世代に属するTTS音声を返します。音声本文はJSONに埋め込まず、`audio/wav` などのバイナリとして配信します。

### `DELETE /api/v1/voice/sessions/{session_id}`

セッションを停止し、停止世代を進め、返答音声を消去して処理中タスクへキャンセルを通知します。返答音声・しっぽ動作の遅着を世代検査で拒否します。応答の `audio_device_stop_confirmed` は、このAPIがスマートフォン内のAndroid音声機器を直接制御していないため `false` です。Web UI は端末側の録音・再生も停止します。

## データと未実装範囲

- 録音音声はログへ記録せず、Whisperが読む一時ファイルを処理後に削除します。
- 文字起こしと返事の音声は会話セッション内だけで扱い、停止／期限切れで参照できなくします。履歴はセッションの `VoicePipeline` にだけ保持します。
- 相づちの候補選択・生成準備・配信判断はサーバー側の責務です。現在のWeb UI/APIは相づちを配信せず最終返答を1回再生し、再生開始・完了・失敗通知も未実装です。Androidは受信音声の再生のみを担当し、相づちの内容や発生タイミングを決定しません。
- Pi 4しっぽ制御もサーバー側の責務で、アプリサーバーがMCP経由でPi 4デバイスAPIを呼び出します。Android Webは明示的なユーザー操作・設定をサーバーへ送り、機器状態を表示します。AndroidからPi 4デバイスAPIへ直接接続しません。サーバー経由のクライアント操作APIとPi 4連携は未実装です。
- Android実機、モデル接続、HTTPS終端、無音・破損音声、停止競合、再接続の受け入れ確認は未実施です。
