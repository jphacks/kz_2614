# 仕様書・開発資料

今回の範囲・境界・修理順・合格条件は **[MVP.md](../MVP.md)** を最初に確認します。全体計画は保持し、今回の合格ゲートにはMVP.mdを優先します。各branchから [最新main版](https://github.com/jphacks/kz_2614/blob/main/MVP.md) を参照してください。

PC音声・HOSSyの製品Web・EMORIの機器仕様と、`namikawa_3Dprint`由来の3Dプリント部品を整理しています。公開mainへの反映状況は [ブランチ一覧](development/branches.md) を参照してください。次に [統合仕様書](requirements/system.md) を読むと、製品全体の動作・接続・担当・未完了事項を確認できます。

## 読む順序

1. [今回のMVP](../MVP.md)：対象・担当・境界・修理順・合格条件。続いて [統合仕様書](requirements/system.md)：何をするシステムか、どこまで接続できているか。
2. [構成と処理フロー](architecture.md)：音声・割込み・通信復旧・MCP・保存をMermaidで確認。
3. [要件一覧](requirements/README.md) / [API一覧](api/README.md)：担当する機能の詳細な契約と受け入れ条件。
4. [環境構築](setup/server.md) / [ESP32導入](../firmware/esp32/README.md)：実行環境を準備。

## 目的別の入口

| 目的 | 参照先 |
| --- | --- |
| ホーム・ダイアリー・PWAを試す | [製品Web仕様](requirements/web-product.md) / [起動手順](setup/hossy-web.md) |
| PCのAI・会話・停止の要件 | [サーバー仕様](requirements/server.md) |
| Web・Android・ESP32音声の役割 | [端末仕様](requirements/clients.md) |
| サーボの配線・校正・PWM・モード | [ESP32機器仕様](requirements/esp32.md) |
| 音声HTTP/TCPと機器HTTPの通信 | [API一覧](api/README.md) |
| LLM/MCPとESP32を接続する | [MCP統合仕様](esp32/mcp-integration.md) |
| 筐体・取付部品のSTL一覧と確認事項 | [3Dプリント部品](../3Dprint/README.md) |
| ESP32の実機記録・引き継ぎ | [ESP32担当者向け入口](esp32/README.md) |
| フォルダー・担当・作業方法 | [配置](repository-layout.md) / [分担](development/ownership.md) / [ブランチ](development/branches.md) |
| このPCの起動・実測・移行 | [ローカル環境](setup/local-pc.md) / [移行手順](development/layout-migration.md) |
| デモ・公開するデータ | [デモ](demo.md) / [Git除外](development/gitignore.md) / [ライセンス](../THIRD_PARTY_NOTICES.md) |
| 過去の検証と変更理由 | [開発履歴](development/work-history.md) |

## 文書の置き場所

| フォルダー | 置くもの |
| --- | --- |
| `requirements/` | 統合仕様とサーバー・端末・ESP32の要件/機器仕様 |
| `api/` | 通信形式、エンドポイント、制限、エラーの正本 |
| `web/` | 製品Webの案内。`history/`は取り込み元のメモ・画像・旧配信記録 |
| `esp32/` | 機器担当向けの案内、MCP統合、引き継ぎ。`history/`は当時の記録 |
| `setup/` | 環境構築、運用手順、個別環境の記録 |
| `development/` | 担当、ブランチ、Git除外、移行、変更履歴 |
| `archive/` | 採用を終了したPi案や旧構成。現行仕様には使わない |

ESP32の旧 `specification.md` / `http-api.md` は移動先案内だけを残しています。本文の更新は `requirements/esp32.md` / `api/esp32-http.md` に一本化します。実装済み・実測済み・未確認を区別し、履歴にあるIP・COM・古いパスを現行設定として扱わないでください。
