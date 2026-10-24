# Web画面とHTTP受付

ダッシュボードはブラウザーで使うWeb画面だけを指します。HTML・CSS・JavaScriptは `web/` に置き、`server.py` はそれらを配信するとともに、Web画面やAndroid WebViewが使う設定・参照WAV・音声セッションAPIのHTTPリクエストを受け付けます。音声処理と会話セッションのロジックは兄弟の [`STTLLMTTS/`](../STTLLMTTS/README.md) に委譲します。

## Web画面サーバーの起動

依存関係をまだ準備していない場合は、リポジトリルートでOSに合うセットアップスクリプトを実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\STTLLMTTS\third_party\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1
```

Ubuntuでは次のように実行します。

```bash
bash Programs/ServerSide/STTLLMTTS/third_party/setup_ubuntu.sh
bash Programs/ServerSide/start_server_ubuntu.sh
```

起動スクリプトはServerSide直下の `.venv/` を使い、HTTPサーバーを前面で起動します。停止するには `Ctrl+C` を押します。既定の待受先は `127.0.0.1:8765` です。待受アドレスとポートはWindowsではパラメーター、Ubuntuでは位置引数で指定できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1 -BindAddress 0.0.0.0 -Port 8765
```

```bash
bash Programs/ServerSide/start_server_ubuntu.sh 0.0.0.0 8765
```

既定では `127.0.0.1:8765` のみにbindします。現在のサーバー開発と同じPC上のLM Studio・Irodori-TTS接続にTLSは不要です。サーバー画面を別PCから一時的に使う場合は、プライベートLANに限って `0.0.0.0` を指定できます。その場合は `Programs/ServerSide/.env` に32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` を設定し、ファイアウォールで信頼するLANだけに制限してください。HTTPではトークンと音声が暗号化されないため、信頼できる開発ネットワークに限定し、インターネットには公開しないでください。

Android WebViewの音声会話は別段階です。現在のAndroidアプリはHTTPS以外を拒否し、WebのマイクAPIもLAN上のHTTPでは使えません。Android実機で音声往復を確認する際に、TLS終端を用意してください。

## Web APIと保存先

設定JSONと参照音声は `Programs/ServerSide/dashboard/data/` に保存され、Gitには含まれません。参照音声は固定名のPCM WAVのみを保存します。設定・参照WAV APIの詳細はサーバー要件書を、音声セッションAPIは [VOICE_API.md](../VOICE_API.md) を参照してください。

ダッシュボードで保存したSTT・TTS・会話設定は、次に作る会話パイプラインで使われます。保存済みの値は同じ項目の `.env` 値より優先されます。保存前は `.env` を初期値として読み、未指定ならSTTはCUDA / float16です。進行中の会話には後から反映されません。

Android Web向けのAPIは、状態取得、セッション開始、音声ターン送信、NDJSON逐次応答、旧クライアント向け返答音声取得、セッション停止を提供します。入力音声は検証後に処理用一時ファイルへ置き、処理後に削除します。逐次応答では相づちと文節単位のTTS音声をストリームし、クライアントが受信順に再生します。再生完了通知APIと実機再生は未確認です。

## 未確認事項

サーバー起動、API操作、LAN・HTTPS接続、Whisper・LM Studio・Irodori-TTSとの実接続、Android実機での録音・再生は未確認です。コードがあることだけでは受け入れ完了と扱いません。
