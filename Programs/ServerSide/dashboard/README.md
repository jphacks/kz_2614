# Web画面とHTTP受付

ダッシュボードはブラウザーで使うWeb画面だけを指します。HTML・CSS・JavaScriptは `web/` に置き、`server.py` はそれらを配信するとともに、Web画面やAndroid WebViewが使う設定・参照WAV・音声セッションAPIのHTTPリクエストを受け付けます。音声処理と会話セッションのロジックは兄弟の [`STTLLMTTS/`](../STTLLMTTS/README.md) に委譲します。

## Web画面サーバーの起動

依存関係を [`../requirements.txt`](../requirements.txt) からインストールし、リポジトリルートで起動します。

```powershell
python -m Programs.ServerSide.dashboard.server
```

既定では `127.0.0.1:8765` のみにbindします。LANから使う場合は `Programs/ServerSide/.env` に32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` を設定し、HTTPSリバースプロキシから同じPCの `http://127.0.0.1:8765` へ転送してください。バックエンドは既定のloopback待受のままにして、8765番ポートをLANへ直接公開しない構成を推奨します。プロキシが別PCの場合だけ、バックエンドをプライベートIPで待ち受け、ファイアウォールでプロキシからの接続だけを許可してください。管理トークンのないloopback外の起動は拒否されますが、loopback待受をプロキシ経由で外部公開する場合もトークンを設定してください。

推奨経路は `Android WebView --HTTPS:443--> reverse proxy --HTTP loopback:8765--> dashboard API` です。AndroidアプリはHTTPS以外と証明書エラーを拒否します。端末で信頼される証明書と、DNS名またはそのIPアドレスを含む証明書を用意してください。

## Web APIと保存先

設定JSONと参照音声は `Programs/ServerSide/dashboard/data/` に保存され、Gitには含まれません。参照音声は固定名のPCM WAVのみを保存します。設定・参照WAV APIの詳細はサーバー要件書を、音声セッションAPIは [VOICE_API.md](../VOICE_API.md) を参照してください。

ダッシュボードで保存したSTT・TTS・会話設定は、次に作る会話パイプラインで使われます。保存済みの値は同じ項目の `.env` 値より優先されます。保存前は `.env` を初期値として読み、未指定ならSTTはCUDA / float16です。進行中の会話には後から反映されません。

Android Web向けのAPIは、状態取得、セッション開始、音声ターン送信、返答音声取得、セッション停止を提供します。入力音声は検証後に処理用一時ファイルへ置き、処理後に削除します。返答音声はメモリ上で期限付き管理されます。相づち配信と再生完了通知は未実装です。

## 未確認事項

サーバー起動、API操作、LAN・HTTPS接続、Whisper・LM Studio・Irodori-TTSとの実接続、Android実機での録音・再生は未確認です。コードがあることだけでは受け入れ完了と扱いません。
