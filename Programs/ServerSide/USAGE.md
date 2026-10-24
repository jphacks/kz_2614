# ServerSideプログラム利用マニュアル

この文書は、WindowsまたはUbuntuでServerSideの環境を準備し、HTTPサーバーとブラウザー画面を使う手順を説明します。リポジトリのルートからコマンドを実行してください。

## 1. プログラムの構成

```text
ブラウザー／Android WebView
          │ HTTP（画面・設定・音声API）
          ▼
dashboard/server.py  ── Web画面の配信とHTTP受付
          │
          ▼
STTLLMTTS/voice/  ── 会話セッションと音声処理
     ├── Whisper      音声認識（STT）
     ├── LM Studio    返答生成（LLM）
     └── Irodori-TTS  音声合成（TTS）
```

ダッシュボードはブラウザー上のWeb画面です。`dashboard/server.py` が画面とHTTP APIを提供し、会話処理は `STTLLMTTS/` に委譲します。Windows・Ubuntuの起動スクリプトはこのHTTPサーバーを起動します。LM StudioとIrodori-TTSのサービスは別途起動してください。

現時点ではセットアップ・起動スクリプト、画面、音声処理コードがありますが、Whisper・LM Studio・Irodori-TTSとの実接続やAndroid実機での音声往復は未確認です。モデルや外部サービスが準備されていない場合、設定画面は開けても音声会話は完了しません。

## 2. 必要な環境

- WindowsまたはUbuntu
- Python 3.11〜3.13
- Git
- 依存関係やモデルを取得するためのインターネット接続
- 会話機能を使う場合は、起動済みのLM StudioとIrodori-TTS互換Gradioサーバー

既定のWhisper設定はCUDA / float16です。CUDAを使う場合はNVIDIA GPU、対応ドライバー、CUDA 12 cuBLAS、cuDNN 9が必要です。CPUを使う場合の設定は「6. 音声設定」を参照してください。セットアップスクリプトはGPUドライバー、CUDA、モデル、LM Studio、Irodori-TTSをインストールしません。

## 3. 初回セットアップ

セットアップスクリプトはGitと対応するPythonを用意し、`Programs/ServerSide/.venv/` を作成してServerSide共通の `requirements.txt` をインストールします。既存の `.venv` がある場合は、対応Pythonで作られたものを再利用します。

### Windows

PowerShellでリポジトリルートを開き、次を実行します。GitまたはPythonがない場合は `winget` で導入します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\STTLLMTTS\third_party\setup_windows.ps1

if (-not (Test-Path .\Programs\ServerSide\.env)) {
    Copy-Item .\Programs\ServerSide\.env.example .\Programs\ServerSide\.env
}
```

### Ubuntu

ターミナルでリポジトリルートを開き、次を実行します。APTパッケージの導入に `sudo` が必要です。

```bash
bash Programs/ServerSide/STTLLMTTS/third_party/setup_ubuntu.sh

if [ ! -f Programs/ServerSide/.env ]; then
  cp Programs/ServerSide/.env.example Programs/ServerSide/.env
fi
```

`.env` がすでにある場合はコピーしないでください。自分の設定を保ったまま、必要な値だけ編集します。このファイルにはローカル接続先や環境固有の設定を記録し、Gitへ追加しないでください。

## 4. 外部サービスを準備する

音声会話を使う場合は、HTTPサーバーを起動する前後に次のサービスを別々に準備してください。

1. **LM Studio**: 音声用モデルをロードし、ローカルAPIを起動します。既定URLは `http://127.0.0.1:1234`、モデル名は `.env` の `VOICE_LLM_MODEL` です。開発中はLM Studio側のAPI認証を無効にした設定を使います。
2. **Irodori-TTS**: Irodori-TTS互換のGradio APIを起動します。既定URLは `http://127.0.0.1:7861/` で、接続先は `IRODORI_URL` で変更できます。外部ソースはリポジトリに含まれず、セットアップスクリプトも取得・起動しません。
3. **Whisper**: サーバーが最初の音声処理でモデルをロードします。既定では初回利用時にモデルを取得し、キャッシュを `Programs/ServerSide/whisper/` に保存します。

接続先・モデル名・実行デバイスなどは `Programs/ServerSide/.env` を編集します。`.env.example` の既定はCUDAを使う設定です。GPU環境がない場合は、ダッシュボードの「音声認識」でデバイスを「CPU」、計算形式を「int8」にして保存してください。TTSもCPUで実行する場合は `.env` のモデル・codec両方のデバイスを `cpu`、精度を `fp32` にします。CPUでの処理時間や各モデルの動作は利用環境で異なります。

## 5. HTTPサーバーを起動する

依存関係を準備した後、リポジトリルートからOSに合うコマンドを実行します。

### Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1
```

### Ubuntu

```bash
bash Programs/ServerSide/start_server_ubuntu.sh
```

起動スクリプトはサーバーを前面で動かします。終了するときはターミナルで `Ctrl+C` を押してください。既定の待受アドレスは `127.0.0.1`、ポートは `8765` です。PCのブラウザーで `http://127.0.0.1:8765/` を開くとWeb画面が表示されます。

ポートが使用中の場合はポート番号を変えられます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1 -Port 8766
```

```bash
bash Programs/ServerSide/start_server_ubuntu.sh 127.0.0.1 8766
```

### 別のPCから画面を使う場合

別PCから接続するときだけ、プライベートLAN上で待受を広げます。設定・音声APIは資格情報の入力なしで利用できます。ファイアウォールで信頼する端末・LANだけに接続元を制限してください。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1 -BindAddress 0.0.0.0 -Port 8765
```

```bash
bash Programs/ServerSide/start_server_ubuntu.sh 0.0.0.0 8765
```

別PCのブラウザーでは `http://<サーバーPCのLAN内IPアドレス>:8765/` を開きます。HTTP通信では音声が暗号化されないため、インターネットへ公開しないでください。Android WebViewから音声会話を使うにはHTTPSが必要です。TLS終端の設定はこの起動スクリプトには含まれません。

## 6. ブラウザー画面と音声設定

### 現在設定できる項目

| 設定場所 | 項目 | 保存・反映 |
| --- | --- | --- |
| 音声操作 | 入力マイクの選択 | ブラウザーのタブ内に保存。利用可能なデバイスから選び、変更後は次の会話で使用します。 |
| 音声操作 | 入力音量 25〜200% | タブ内に保存し、会話中も変更できます。プリセットは60%・100%・150%です。 |
| 発話の検出 | 感度、無音待ち、最小発話時間、最大発話長、開始・終了しきい値、先頭保持時間 | サーバーへ保存し、新しい会話から適用します。音声上限と停止規則は変更しません。 |
| 音声認識（STT） | モデル、言語、CPU／CUDA、計算形式、Whisper内部VAD | サーバーへ保存し、新しい会話から適用します。 |
| 返答生成（LLM） | モデル、出力トークン上限、文脈長、履歴メッセージ数、temperature、応答待ち上限 | 今回追加した項目です。サーバーへ保存し、新しい会話から適用します。各範囲は画面で制限します。 |
| 音声合成（TTS） | チェックポイント、声の説明、合成後の出力音量（0〜200%） | サーバーへ保存し、新しい会話から適用します。100%は変更なしです。 |
| 参照音声 | WAVの登録・状態確認・削除 | サーバーへ保存します。形式・長さ・容量の条件は「設定の変更」を参照してください。 |
| 会話のふるまい | 基本の話し方、返答の長さ、質問頻度、追加指示、相づち待ち時間 | サーバーへ保存し、新しい会話から適用します。相づちは必須で、待ち時間のみ変更できます。 |
| サーバー接続 | 待受アドレス、ポート | 待受先は起動スクリプトで設定します。開発中は認証情報を使いません。 |

マイク選択と入力音量はブラウザー側の設定で、サーバーの設定JSONには保存しません。サーバーへ保存する設定は `Programs/ServerSide/dashboard/data/dashboard_settings.json` に保存され、同じ項目の `.env` 値より優先されます。設定の保存は進行中の会話を変更・中断せず、新しく開始する会話から反映されます。

### `.env` で設定する項目

ダッシュボードに入力欄がない接続先、ランタイム設定は `Programs/ServerSide/.env` で管理します。代表的な項目は次のとおりです。

- **LM Studio**: `LM_STUDIO_BASE_URL`、`VOICE_LLM_GPU_RATIO`。モデル・出力上限・文脈長・履歴数・temperature・応答待ち時間は `VOICE_LLM_MODEL`、`VOICE_LLM_MAX_TOKENS`、`VOICE_LLM_CONTEXT_LENGTH`、`VOICE_LLM_HISTORY_MESSAGES`、`VOICE_LLM_TEMPERATURE`、`VOICE_LLM_TIMEOUT_SECONDS` で初期設定し、保存後は画面の値が優先されます。
- **Whisper**: `WHISPER_MODEL_ID`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE`、`WHISPER_VAD_FILTER`、`WHISPER_LOCAL_FILES_ONLY`、`WHISPER_MAX_AUDIO_BYTES`、`WHISPER_MAX_AUDIO_SECONDS`、`WHISPER_TIMEOUT_SECONDS`。画面に保存したモデル等は該当項目の環境値より優先されます。
- **VAD**: `VOICE_VAD_THRESHOLD`、`VOICE_VAD_END_THRESHOLD`、`VOICE_VAD_MIN_SPEECH_MS`、`VOICE_VAD_SILENCE_MS`、`VOICE_VAD_PREROLL_MS`、`VOICE_VAD_MAX_SPEECH_SECONDS`。画面で保存した値が優先されます。
- **Irodori-TTS**: `VOICE_TTS_BACKEND`、`IRODORI_URL`、`VOICE_TTS_CHECKPOINT`、`VOICE_TTS_MODEL_DEVICE`、`VOICE_TTS_MODEL_PRECISION`、`VOICE_TTS_CODEC_DEVICE`、`VOICE_TTS_CODEC_PRECISION`、`VOICE_TTS_CAPTION`、`VOICE_TTS_OUTPUT_VOLUME_PERCENT`、`VOICE_TTS_SEED`、`VOICE_TTS_REFERENCE_AUDIO`、`VOICE_TTS_MAX_AUDIO_BYTES`、`VOICE_TTS_TIMEOUT_SECONDS`、`IRODORI_LOCAL_FILES_ONLY`、`IRODORI_NUM_STEPS`、`IRODORI_MAX_SECONDS`、`IRODORI_SOURCE_DIR`、`IRODORI_CODEC_REPO`、`HF_HOME`、`HF_HUB_CACHE`。出力音量の画面保存値は環境値より優先します。
- **相づち**: `VOICE_ACK_DELAY_SECONDS`。画面で保存した待ち時間が優先されます。
- **MCP**: `TAIL_MCP_ENABLED`、`TAIL_CONTROL_ENABLED`、`TAIL_DEVICE_URL`、`TAIL_MCP_LOG_LEVEL`。しっぽ制御は既定で無効です。設定JSONの保存先は必要に応じて `NENGITSUNE_SETTINGS_PATH` で変更できます。開発中はダッシュボード・機器・LM Studioの認証情報を使いません。

### 音声会話

1. 画面の「会話を始める」を押します。
2. マイク利用を許可し、使う入力機器を選んで話します。会話開始後は自動で音声を送信し、サーバー側で発話を判定して途中の文字を更新します。
3. 話し終わるとサーバーが無音を検知して確定し、返答を生成します。ブラウザーにVADは置きません。
4. 画面に話している途中の文字と返答の続きを表示します。返答音声は先にできた文節から順に再生します。無音・返答生成中・再生中もマイク音声の送信は続き、発話の認識はサーバー側で制御します。
5. 周囲がうるさい時は入力音量を小さく、静かな場所で声が小さい時は大きく調整できます。「周囲がうるさい」60%・「標準」100%・「静か・小さい声」150%と、25〜200%のスライダーを使えます。変更時もマイクと送信は止まりません。
6. 会話をただちに止める場合は「すぐに停止」を押します。

Web画面はPCM16LE・16kHzモノラルを256msごとに継続送信します。従来のファイル送信APIはWebM、Ogg、WAV、MP4に対応し、1回あたり最大25,000,000バイト・60秒です。ブラウザーのマイクはセキュアコンテキストを必要とします。Android WebViewからLAN上で使う場合は、HTTPSと端末から信頼される証明書を用意してください。ローカルブラウザーでマイクを使えない場合もHTTPS環境を準備してください。

### 設定の変更

- **発話の検出**: 標準・感度低め・感度高めのプリセットと、無音待ち320〜2000ms、最小発話128〜1000ms、発話上限1〜30秒を調整できます。詳細設定では開始・終了しきい値と先頭保持時間も設定できます。発話検出はサーバーで行います。
- **音声認識**: Whisperモデル、日本語／自動判定／英語、CPU／CUDA、計算形式、無音区間の除外を設定します。
- **音声合成**: Irodori-TTSチェックポイントと声の説明を設定します。
- **参照音声**: PCM WAV、16-bit、モノラルまたはステレオ、16〜48kHz、1秒〜5分、最大100MBの音声をアップロードできます。アップロードしたWAVはサーバーに保存され、TTS生成時に送られます。不要になったら画面の「削除」を押してください。
- **会話のふるまい**: 話し方、返答の長さ、質問頻度、追加の希望を設定できます。追加の希望は最大400文字です。安全・停止の規則や、しっぽ動作の許可はこの画面から変更できません。
- **保存**: 「サーバーに保存」を押すと設定が保存されます。変更は新しく始める会話から反映され、進行中の会話には適用されません。「既定値に戻す」はフォームを戻す操作なので、サーバー設定を変えるには続けて保存してください。

ダッシュボードに保存した設定は `Programs/ServerSide/dashboard/data/dashboard_settings.json` に保存され、同じ項目の `.env` 設定より優先されます。設定ファイルと参照WAVはローカルデータで、Gitには含まれません。

## 7. 保存・停止・セキュリティ

- 録音音声は処理中に一時領域で扱い、処理後に削除します。既定では録音音声を会話履歴として保存しません。
- 設定は `dashboard/data/dashboard_settings.json` に保存します。アップロードした参照音声は `dashboard/data/reference_voice.wav` に保存されるため、使わなくなったら画面から削除してください。
- Whisperモデルのキャッシュは `Programs/ServerSide/whisper/` に置かれます。モデルの再取得を行う場合はこのキャッシュを確認してください。
- サーバーを停止するときは起動したターミナルで `Ctrl+C` を押します。ブラウザーの「すぐに停止」は会話セッションを止める操作で、サーバープロセス自体は終了しません。
- しっぽMCP制御は初期状態で無効です。Pi 4デバイスサービスとの統合と停止確認が終わるまで、`TAIL_CONTROL_ENABLED=false` を維持してください。

## 8. 困ったとき

| 症状 | 確認・対処 |
| --- | --- |
| `.venv` がないと表示される | OSに合う `STTLLMTTS/third_party/` のセットアップスクリプトを実行してから、起動スクリプトを再実行します。 |
| `127.0.0.1:8765` に接続できない | 起動ターミナルのエラーを確認します。別ポートを使う場合は、URLのポートも同じ番号にします。 |
| 別PCから接続できない | 待受アドレス、サーバーPCのLAN内IP、ポート番号、ファイアウォールの接続元制限を確認します。 |
| CUDA／Whisperのエラーが出る | NVIDIA GPUとCUDA環境を確認します。利用できない場合は音声認識をCPU / int8に変更して保存し、新しい会話で試します。 |
| LLMに接続できない | LM StudioのAPIが起動中か、`.env` のURL・モデル名が実際のLM Studio設定と一致し、LM Studio側のAPI認証が無効になっているか確認します。 |
| TTSに接続できない | Irodori-TTS互換Gradioサーバーが起動中か、`.env` の `IRODORI_URL` が一致しているか確認します。 |
| マイクを使えない | ブラウザーのマイク権限を確認します。AndroidやLAN接続ではHTTPSと信頼済み証明書を用意します。 |

依存関係の詳細は [third_party/README.md](STTLLMTTS/third_party/README.md)、Web画面の説明は [dashboard/README.md](dashboard/README.md)、音声APIは [VOICE_API.md](VOICE_API.md)、要件と未実装・未確認事項は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。

参照WAVの保存上限と声質抽出区間は別です。保存音声は保持し、ローカルTTSは既定で先頭5秒を声質抽出に使います。`IRODORI_REFERENCE_MAX_SECONDS`で1〜30秒、CPUコーデックの並列数は`IRODORI_CPU_THREADS`で1〜16（既定2）に設定できます。このPCではGPUメモリを節約するため、TTSモデルをGPU、コーデックをCPU/fp32で動かします。
