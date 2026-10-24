# 仕様書・図の入口

2026-10-25更新。`975b3ad`で統合したmainのコードを基準に、現行の仕様と未完了の接続を整理しています。文書の「実装済み」「確認記録あり」「未実装」を区別してください。

| 読みたい内容 | 参照先 |
| --- | --- |
| 全体構成・会話・割込み・通信復旧・MCP・保存の図 | [構成と処理フロー](architecture.md) |
| サーバーの責務・制約・受け入れ条件 | [サーバー要件](../Programs/ServerSide/REQUIREMENTS.md) |
| Web・Android・ESP32の担当と対応状況 | [端末要件](../Programs/ClientSide/REQUIREMENTS.md) |
| HTTP PCM・NDJSON・停止・再生結果通知 | [Web音声API](../Programs/ServerSide/VOICE_API.md) |
| TCPのフレーム形式・profile・端末対応 | [TCP音声API](../Programs/ServerSide/VOICE_TCP.md) |
| 現在のMCPが保存する希望頻度 | [頻度設定API](../Programs/ServerSide/TAIL_FREQUENCY_API.md) |
| ESP32実機API・引き継ぎ・未接続箇所 | [ESP32資料](esp32/README.md) |
| 管理するファイルと除外するファイル | [Git除外方針](development/gitignore.md) |
| 起動・設定・実測の記録 | [このPC](../LOCAL_SETUP.md) / [利用手順](../Programs/ServerSide/USAGE.md) |
| ソースの配置と共同作業 | [配置](../Programs/README.md) / [ブランチ運用](development/branches.md) |
| 検証の経緯・ライセンス | [作業履歴](../Programs/WORK_HISTORY.md) / [第三者素材](../THIRD_PARTY_NOTICES.md) |

## 更新する順序

1. 動作を変えたら対応するAPI契約・要件を更新する。
2. 経路や責務が変わったらMermaid図を更新する。未接続の経路を実線へ変えるのは実装後とする。
3. 検証した環境と未確認事項を作業履歴へ残す。自動テストの成功を実機成功と置き換えない。
4. 配置変更では相対リンクと `.gitignore` の適用先も検査する。

`archive/` と `esp32/history/` は当時の設計・担当者の記録です。そこにあるIP、COM番号、旧パス、旧Pi案を現在の稼働設定として扱いません。現在のコードと文書が食い違う場合は、差分を確認し、未実装を実装済みに書き換えないでください。
