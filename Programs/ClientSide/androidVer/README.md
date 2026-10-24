# Android WebView クライアント

AndroidアプリはサーバーのWeb画面を開くWebViewラッパーです。サーバーURLを入力して「Webを開く」を押すと、最後に使ったURLを保存して次回起動時に再度開きます。

## 接続と音声会話

1. リポジトリルートで `python -m Programs.ServerSide.app.dashboard.server` を実行してサーバーを起動します。
2. スマートフォンから到達できるHTTPS URLを用意します。LANで管理トークンを使う場合は、32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` とHTTPSリバースプロキシを設定します。
3. アプリへ `https://サーバー名/` を入力してWeb画面を開きます。
4. Web画面で会話を開始し、録音時にAndroidのマイク許可を与えます。ボタンで1発話を録音して送信し、返答音声を再生します。

HTTPページは表示できますが、ブラウザーのマイクAPIは安全なコンテキストでのみ利用できるため、音声会話にはHTTPSを使ってください。WebViewは、マイク要求を開いているサーバーの同一オリジンに限って許可します。WebView内で別オリジンへ移動した場合は、端末のブラウザーへ渡します。

## 実装範囲

- `MainActivity.java`: URL入力、WebView表示、最後に開いたURLの保存、同一オリジンの音声許可。
- `AndroidManifest.xml`: インターネットと録音の権限、起動Activityを宣言。
- Web画面とAPIの仕様は [ServerSide/VOICE_API.md](../../ServerSide/VOICE_API.md) を参照。
- 現在の音声会話はターン制です。常時接続の全二重通話、相づちの配信、Pi 4への音声機器連携は含みません。
- Androidエミュレーター／実機でのビルド・画面・録音・再生確認は未実施です。
