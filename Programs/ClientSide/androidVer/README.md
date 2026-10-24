# Androidネイティブ音声通話

WebViewを使わず、Android標準のマイク・スピーカーとTCPソケットで既存STT・LLM・TTSへ接続します。画面はPCのIPアドレス、ポート（既定8766）、通話開始、停止です。

Android Studioでこのフォルダーを開き、Debug APKをビルド・実行してください。Android 10以降、マイク権限が必要です。サーバー起動とプロトコル、確認範囲は [VOICE_TCP.md](../../ServerSide/VOICE_TCP.md) を参照。今回のAPKビルド・端末録音再生は未確認です。
