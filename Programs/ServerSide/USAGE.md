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

`.env` がすでにある場合はコピーしないでください。自分の設定を保ったまま、必要な値だけ編集します。このファイルにはローカル接続先や認証情報を記録し、Gitへ追加しないでください。

## 4. 外部サービスを準備する

音声会話を使う場合は、HTTPサーバーを起動する前後に次のサービスを別々に準備してください。

1. **LM Studio**: 音声用モデルをロードし、ローカルAPIを起動します。既定URLは `http://127.0.0.1:1234`、モデル名は `.env` の `VOICE_LLM_MODEL` です。API認証を有効にしている場合は、LM Studioで使うキーと `LM_STUDIO_API_KEY` を一致させます。
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

別PCから接続するときだけ、プライベートLAN上で待受を広げます。サーバーPCの `.env` に、32文字以上のランダムな `NENGITSUNE_DASHBOARD_TOKEN` を設定してから起動してください。サーバーはトークンがない場合や32文字未満の場合、非loopback待受を拒否します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1 -BindAddress 0.0.0.0 -Port 8765
```

```bash
bash Programs/ServerSide/start_server_ubuntu.sh 0.0.0.0 8765
```

別PCのブラウザーでは `http://<サーバーPCのLAN内IPアドレス>:8765/` を開きます。画面下部の「管理アクセス」に同じトークンを入力してください。ファイアウォールは信頼する端末・LANだけに制限します。HTTP通信ではトークンや音声が暗号化されないため、インターネットへ公開しないでください。Android WebViewから音声会話を使うにはHTTPSが必要です。TLS終端の設定はこの起動スクリプトには含まれません。

## 6. ブラウザー画面と音声設定

### 音声会話

1. 画面の「会話を始める」を押します。
2. マイク利用を許可し、使う入力機器を選んで話します。会話開始後は自動で音声を送信し、サーバー側で発話を判定して途中の文字を更新します。
3. 話し終わるとサーバーが無音を検知して確定し、返答を生成します。ブラウザーにVADは置きません。
4. 画面に話している途中の文字と返答の続きを表示します。返答音声は先にできた文節から順に再生します。無音・返答生成中・再生中もマイク音声の送信は続き、発話の認識はサーバー側で制御します。
5. 周囲がうるさい時は入力音量を小さく、静かな場所で声が小さい時は大きく調整できます。「周囲がうるさい」60%・「標準」100%・「静か・小さい声」150%と、25〜200%のスライダーを使えます。変更時もマイクと送信は止まりません。
6. 会話をただちに止める場合は「すぐに停止」を押します。

Web画面はPCM16LE・16kHzモノラルを256msごとに継続送信します。従来のファイル送信APIはWebM、Ogg、WAV、MP4に対応し、1回あたり最大25,000,000バイト・60秒です。ブラウザーのマイクはセキュアコンテキストを必要とします。Android WebViewからLAN上で使う場合は、HTTPSと端末から信頼される証明書を用意してください。ローカルブラウザーでマイクを使えない場合もHTTPS環境を準備してください。

### 設定の変更

- **音声認識**: Whisperモデル、日本語／自動判定／英語、CPU／CUDA、計算形式、無音区間の除外を設定します。
- **音声合成**: Irodori-TTSチェックポイントと声の説明を設定します。
- **参照音声**: PCM WAV、16-bit、モノラルまたはステレオ、16〜48kHz、1〜30秒、最大10MBの音声をアップロードできます。アップロードしたWAVはサーバーに保存され、TTS生成時に送られます。不要になったら画面の「削除」を押してください。
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
| LAN待受が拒否される | `.env` に32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` を設定し、スクリプトを再起動します。 |
| 管理操作が認証エラーになる | サーバー起動時のトークンと同じ値を、画面下部の「管理アクセス」に入力します。 |
| CUDA／Whisperのエラーが出る | NVIDIA GPUとCUDA環境を確認します。利用できない場合は音声認識をCPU / int8に変更して保存し、新しい会話で試します。 |
| LLMに接続できない | LM StudioのAPIが起動中か、`.env` のURL・APIキー・モデル名が実際のLM Studio設定と一致するか確認します。 |
| TTSに接続できない | Irodori-TTS互換Gradioサーバーが起動中か、`.env` の `IRODORI_URL` が一致しているか確認します。 |
| マイクを使えない | ブラウザーのマイク権限を確認します。AndroidやLAN接続ではHTTPSと信頼済み証明書を用意します。 |

依存関係の詳細は [third_party/README.md](STTLLMTTS/third_party/README.md)、Web画面の説明は [dashboard/README.md](dashboard/README.md)、音声APIは [VOICE_API.md](VOICE_API.md)、要件と未実装・未確認事項は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。
