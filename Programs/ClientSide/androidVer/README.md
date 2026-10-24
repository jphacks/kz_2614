# Androidネイティブ音声通話

WebViewを使わず、Android標準のマイク・スピーカーとTCPソケットで既存STT・LLM・TTSへ接続します。画面はPCのIPアドレス、ポート（既定8766）、通話開始、停止です。

Android Studioでこのフォルダーを開き、Debug APKをビルド・実行してください。Android 10以降、マイク権限が必要です。サーバー起動とプロトコル、確認範囲は [VOICE_TCP.md](../../ServerSide/VOICE_TCP.md) を参照。今回のAPKビルド・端末録音再生は未確認です。

## ライセンス表示

テンプレートのAndroid robot図形とGradle Wrapperの表示は [THIRD_PARTY_NOTICES.md](../../../THIRD_PARTY_NOTICES.md) を参照してください。Android robotはGoogle作成・共有の作品を変更して用い、CC BY 3.0に従います。
