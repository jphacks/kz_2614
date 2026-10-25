# ESP32 Reader / Follower とSG90 3台

MVP修理のFollower新版は **v2_exclusive** です。[v2契約](../../docs/api/mvp-tail-control-v2.md)、[修理・設定・実機試験手順](../../docs/esp32/mvp-v2-repair.md)、[ビルド/模擬試験](../../tests/esp32/README.md) を先に確認してください。起動時PWM off、手元の `a` で明示準備。Readerと旧動作APIは常時拒否します。既存v1ファームの説明と混同しないでください。2026-10-25 12:09の対象機器は旧APIだけが応答し、新版反映待ちです。

旧v1構成ではReaderが可変抵抗器を読み、Wi-Fi経由でFollowerのSG90 3台へ共通の指令を送る。新版ではPCのv2 API制御を使い、Readerの操作を拒否する。しっぽの先端指令は0～180で、0が丸い側、90が中立、180が伸びる側。これは論理指令であり、実測した先端角度ではない。

詳細な現行仕様は [docs/esp32](../../docs/esp32/README.md) にまとめた。

| フォルダ | 内容 |
| --- | --- |
| [ESP32-Reader](ESP32-Reader/ESP32-Reader.ino) | 可変抵抗器の入力、ADC校正、HTTP送信 |
| [ESP32-Follower](ESP32-Follower/ESP32-Follower.ino) | SG90 3台、v2有限タップ・停止フェンス、手元準備/中立/脱力。旧無期限往復は拒否 |
| [voice_tcp](voice_tcp/README.md) | 独立したESP-IDF音声通信部品。今回のサーボスケッチには組み込んでいない |

## FollowerのIPと搭載APIを確認する

IPはWi-Fi内で機器を識別するアドレスです。**サーボにつながるFollower**をUSB接続した担当PCで、Arduino IDEの対象ポートを選び、シリアルモニターを**115200 baud**で開きます。接続時の`Servo ESP32 IP: ...`を確認してください。`Reader IP:`は別機器です。表示を見逃した場合は担当者と起動ログ/ルーターの接続一覧を確認し、IP確認のためだけに動作中の機器を無断でリセットしません。

以下は読み取りのみ。`192.168.100.111`は2026-10-25の確認値なので、実際の表示へ置き換え、音声PCから実行します。

```bash
curl -i --connect-timeout 3 --max-time 5 http://192.168.100.111/api/v2/tail/state
```

- 200 JSONなら`protocol_version=2`、capability、`control_mode=v2_exclusive`等を[v2契約](../../docs/api/mvp-tail-control-v2.md)と照合する。起動直後の`start_enabled=false`は正常。200だけで校正・開始許可・物理停止確認済みにはしない。
- 404ならv2対応を確認できない。担当者が書込み版を確認する。2026-10-25 12:09にはこのパスが404、旧`GET /api/tail/status`が200 idleで、新版未反映と判断した。旧status成功をv2対応の証拠にせず、旧動作APIへ切り替えない。
- 音声APIの`/api/v1/voice/`は別契約なので変更しない。製品ブラウザーは機器を直接操作せず、PCが仲介する。

## Arduino IDEで使う（新版Follower）

1. 対象個体と既存のWi-Fi/Tail設定・NVS校正・復元手順を担当者が確認し、[実機更新手順](../../docs/esp32/mvp-v2-repair.md#未実施の実機試験と実施条件)に沿って保全する。既存設定をテンプレートで上書きしない。
2. mainの対象SHAと実際に開いたFollowerソースを確認する。既存の確認環境はArduino-ESP32 3.3.12、ボードは`ESP32 Dev Module`。追加サーボライブラリは不要。
3. 新規環境だけ[WiFiConfig.example.h](ESP32-Follower/WiFiConfig.example.h)と[TailConfig.example.h](ESP32-Follower/TailConfig.example.h)からGit対象外の設定を作る。テンプレートは開始無効・90〜90であり実機準備済みではない。実値は担当者が個体の確認済み範囲に合わせる。
4. 保全した設定・パーティションを維持し、全消去せずに確認した新版を書き込む。公開templateビルド成果物をそのまま実機へ書き込まない。書込み後も起動はPWM offで、上記のIP/v2状態を確認する。
5. 操作者立会いで準備を進める。シリアル`v`で状態、`a`で開始許可とPWMを有効化する。**`a`は校正範囲内に丸めた90度への動作を伴う。** `x`は保持停止、`d`はPWM off、`n`は中立へ動かす操作。Reader復帰`t`は拒否され、再接続で自動準備しない。
6. [限定実機試験](../../docs/esp32/mvp-v2-repair.md)の500ms動作・途中停止から段階的に確認し、PC担当と会話からの試験へ進む。物理停止の観察とAPI上の指令状態を別に記録する。

Readerソースは旧構成のため保持していますが、新版FollowerのMVP通し試験にReaderの書込みやつまみ追従は必要ありません。

## 配線の要約

| 対象 | 接続 |
| --- | --- |
| 可変抵抗器 | 両端をReaderの3V3/GND、中央をGPIO34 |
| サーボ信号 | FollowerのGPIO18・19・21 |
| サーボ電源 | 3台分を供給できる外部5 V電源、ESP32と共通GND |
| 識別LED | GPIO2。Readerは1回、Followerは2回の点滅 |

可変抵抗器の入力へ5 Vを接続しない。サーボの電源はGPIO/3V3から取らず、USB給電中のESP32の電源端子へ外部電源の＋をつなぎ合わせない。

## APIとMCP

- [現行しっぽv2契約](../../docs/api/mvp-tail-control-v2.md) / [旧v1参照](../../docs/api/esp32-http.md)
- [モード・校正・タイミング](../../docs/requirements/esp32.md)
- [現在のPC側MCPと実機向け提案の違い](../../docs/esp32/mcp-integration.md)
- [過去の実機記録](../../docs/esp32/history/2026-10-25-0222-LLMしっぽMCP統合調査.md)

APIのトークン設定は不要。Wi-Fiの実設定、`TailConfig.h`、ビルド生成物はGitに含めない。COM番号とIPは接続環境で変わる。

現在のmainは会話所有のMCP経由で有限tap・独立stop・v2状態確認を実装しています。旧頻度MCPはPC内の希望値の互換機能で、実機動作成功とは別です。コード統合と実機への書込み・公開配備・受け入れを区別します。
