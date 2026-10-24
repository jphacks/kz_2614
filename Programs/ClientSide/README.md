# ClientSide

2026-10-24 最新指定: AndroidはWebView方式から、音声通話専用のネイティブアプリと直接TCP通信へ変更。[新しい接続・起動手順](../ServerSide/VOICE_TCP.md)を優先する。以下のWeb構成説明は既存経路の記録として保持する。APKビルド・実機通話は未確認。

AndroidではWebアプリを基本とします。[Android WebViewアプリ](androidVer/)でサーバーURLを開き、設定と音声会話の画面は [ServerSide/dashboard/web](../ServerSide/dashboard/web/) から配信します。Web画面でスマートフォンのマイクを録音し、サーバーのSTT・LLM・TTSを経て返答音声をスマートフォンで再生します。STT・TTS・会話のふるまいに加え、Irodori-TTSの参照WAVをアップロード・保存・削除できます。WAVはサーバー内の固定パスに保存され、次のTTSインスタンスから使われます。

必須動作、未決事項、受け入れ条件は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。Android WebView、ターン制の録音・送信、NDJSONによる相づち・テキスト差分・文節音声の逐次受信と順次再生、サーバーセッションAPIのコードを追加しました。再生結果通知、Pi 4のハードウェア連携、Android実機での音声確認は未完了です。音声会話ではHTTPS接続とマイク許可が必要です。

2026-10-24: [ESP32向け音声TCP通信部品](ESP32Voice/README.md)を追加。ユーザー指定によりGPIO・マイク・スピーカー制御は含めず、既存ESP32プログラムから呼ぶ送受信APIとして提供する。Android版とは別実装。ESP32実機・ビルドは未確認。
