# ClientSide

AndroidではWebアプリを基本とします。[Android WebViewアプリ](androidVer/)でサーバーURLを開き、設定と音声会話の画面は [ServerSide/web](../ServerSide/web/) から配信します。Web画面でスマートフォンのマイクを録音し、サーバーのSTT・LLM・TTSを経て返答音声をスマートフォンで再生します。STT・TTS・会話のふるまいに加え、Irodori-TTSの参照WAVをアップロード・保存・削除できます。WAVはサーバー内の固定パスに保存され、次のTTSインスタンスから使われます。

必須動作、未決事項、受け入れ条件は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。Android WebView、ターン制の録音・送信・返答再生、サーバーセッションAPIのコードを追加しました。相づち配信、再生完了通知、Pi 4のハードウェア連携、Android実機確認は未完了です。音声会話ではHTTPS接続とマイク許可が必要です。
