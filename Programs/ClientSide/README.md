# ClientSide

AndroidではWebアプリを基本とします。設定ダッシュボードのHTML・CSS・JavaScriptは [ServerSide/web](../ServerSide/web/) に置き、サーバーから配信します。STT・TTS・会話のふるまいに加え、Irodori-TTSの参照WAVをアップロード・保存・削除できます。WAVはサーバー内の固定パスに保存され、次のTTSインスタンスから使われます。

必須動作、未決事項、受け入れ条件は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。設定ダッシュボードと設定保存APIは作成済みです。Pi 4の音声入出力、会話セッション、相づち／本返答の再生をつなぐデバイスAPIは未実装です。
