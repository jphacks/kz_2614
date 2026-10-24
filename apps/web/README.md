# 音声・設定Webダッシュボード — HOSSy

HTML/CSS/JavaScriptの画面とブラウザーの音声入出力です。`index.html`、`dashboard.js`、`voice_call.js` とAudioWorkletをここで管理します。ビルドは不要で、[server/api](../../server/api/README.md) が静的ファイルを配信します。

- マイク入力を16kHz PCMへ変換して継続送信する。
- 途中の文字起こし・返答・稼働状態を表示する。
- 返答のPCMフレームを受け取りAudioWorkletへ渡す。
- 入力音量・VAD・モデル等の設定をAPIへ渡す。VADやモデル処理はサーバー側。

Webを開くには [サーバーの起動手順](../../docs/setup/server.md) に従い、PC上で `http://127.0.0.1:8765/` にアクセスします。ファイルを直接開く方法ではAPIへ接続できません。

共有仕様: [HTTP音声](../../docs/api/voice-http.md)。テストは [tests/web](../../tests/web/)。心音素材の出典は [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。

ホーム・ダイアリー・入眠タイマーを持つ製品Web/PWAは [apps/hossy-web](../hossy-web/README.md) です。公開先は [製品Webのダイアリー](https://hiroron-jphacks2026.hiroron.net/#diary) で、こちらの8765番の音声API接続済み画面とは、起動・配信・保存場所が異なります。公開先の未ログイン確認はhiroron Passportのログイン画面までで、公開UIからの音声接続を確認したものではありません。
