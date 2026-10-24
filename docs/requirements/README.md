# 要件・仕様一覧

| 文書 | 範囲 | 詳細を持つ対象 |
| --- | --- | --- |
| [統合仕様書](system.md) | 製品全体・責務・接続・統合状況 | 担当をまたぐ動作と境界 |
| [サーバー仕様](server.md) | PCの音声・会話・停止・MCP・保存 | SV要件 / ST受け入れ条件 |
| [端末仕様](clients.md) | Web・Android・ESP32音声の入出力 | CL要件 / CT受け入れ条件 |
| [ESP32機器仕様](esp32.md) | Reader/Follower・配線・PWM・校正・モード | サーボ機器の動作と確認範囲 |

通信JSON・音声形式・HTTP/TCPエラーは [API一覧](../api/README.md)、処理の流れは [Mermaid構成図](../architecture.md) を参照します。MCPから実機までの接続条件は [統合ガイド](../esp32/mcp-integration.md) に集約しています。

受け入れ済みと書くときは、対象のコード・環境・実施した確認・残る制約を [作業履歴](../development/work-history.md) に残してください。
