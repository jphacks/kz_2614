# ねんぎつね（仮）

音声会話と、ESP32によるしっぽのトントン動作を組み合わせる添い寝ロボットのプロジェクトです。PC側の会話システムとESP32側のファームウェアを `main` に統合しています。

## まず読む

- [現在の構成と確認状況](docs/architecture.md)
- [このPCの起動手順](LOCAL_SETUP.md)
- [Windows・Ubuntuのセットアップ](Programs/ServerSide/USAGE.md)
- [ESP32 Reader・Followerの配線と起動](Programs/ClientSide/ESP32/README.md)
- [フォルダー一覧](Programs/README.md)
- [ブランチ運用と移行方法](docs/development/branches.md)
- [外部素材・ライセンス](THIRD_PARTY_NOTICES.md)

## 現在できること

PCのWeb画面から連続音声入力、途中の文字起こし、Whisper → Gemma（LM Studio）→ Irodori-TTSによる返答音声の送信、話しかけ直したときの返答中断に対応します。VADと入力音量の適用はサーバー側です。

ESP32 Reader/Followerのスケッチ、HTTPしっぽ制御API、AndroidとESP32の音声TCP通信コードも含みます。ただし、PC会話のMCPは現在も希望頻度の保存・取得のみです。ESP32実機を動かすMCPへの接続と、新しいAndroid/ESP32音声経路の実機確認は未完了です。ソース統合と実機連携の完了は区別しています。

## 公開対象

ソース、設定テンプレート、テスト、仕様、配布条件を確認した素材だけを管理します。モデル本体、録音、参照音声、生成音声、実Wi-Fi設定、`.env`、ビルド成果物は含めません。プロジェクトのコードは [MIT](LICENSE)、第三者素材は [個別の条件](THIRD_PARTY_NOTICES.md) に従います。
