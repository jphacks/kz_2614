# Android WebView クライアント

AndroidアプリはサーバーのWeb画面を開くWebViewラッパーです。サーバーURLを入力して「Webを開く」を押すと、最後に使ったURLを保存して次回起動時に再度開きます。

## 接続と音声会話

1. リポジトリルートで `python -m Programs.ServerSide.dashboard.server` を実行してサーバーを起動します。
2. スマートフォンから到達できるHTTPS URLを用意します。LANで管理トークンを使う場合は、32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` とHTTPSリバースプロキシを設定します。
3. アプリへ `https://サーバー名/` を入力してWeb画面を開きます。
4. Web画面で会話を開始し、録音時にAndroidのマイク許可を与えます。ボタンで1発話を録音して送信し、返答音声を再生します。

アプリはHTTPS URLのみ受け付けます。WebViewは、マイク要求を開いているサーバーの同一オリジンに限って許可します。WebView内で別オリジンへ移動した場合は、端末のブラウザーへ渡します。

## 実装範囲

- `MainActivity.java`: URL入力、WebView表示、最後に開いたURLの保存、同一オリジンの音声許可。
- `AndroidManifest.xml`: インターネットと録音の権限、起動Activityを宣言。
- アプリの最小対応OSはAndroid 10（API 29）です。
- `ServerSide/dashboard/web/voice_call.js`: MediaRecorderで1発話を録音し、VOICE_API.mdで許可されたMIME形式とBearer認証でサーバーへ送信。返答音声をWebView内で再生し、録音形式が合わない場合は送信前に案内する。画面が非表示になると端末音声を停止し、サーバーへセッション終了を要求する。
- Web画面とAPIの仕様は [ServerSide/VOICE_API.md](../../ServerSide/VOICE_API.md) を参照。
- 現在の音声会話はターン制です。常時接続の全二重通話、相づちの配信、Pi 4への音声機器連携は含みません。
- Windowsでのビルドは、このフォルダーから `.\gradlew.bat assembleDebug` を実行します。APKは `app/build/outputs/apk/debug/app-debug.apk` に生成されます。日本語を含む現在のプロジェクトパスでビルドできるよう `gradle.properties` に `android.overridePathCheck=true` を設定しています。
- Debug APKのビルドと、Android 10のHWV33へのインストール・`MainActivity`起動は確認済みです。Android Studioから実行中プロセスへデバッガーが接続することも確認済みです。HTTPSサーバー接続、マイク権限、録音・音声送受信は未確認です。
