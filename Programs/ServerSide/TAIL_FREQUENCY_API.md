# PC内のトントン希望頻度API

2026-10-25更新: この文書は統合mainの現行保存APIです。ESP32 Followerの実動作APIは [ESP32資料](../../docs/esp32/README.md) を参照してください。Followerの引き継ぎは0.3回/秒固定で、ここで保存する0.3〜1.0Hzをそのまま送る互換性はありません。会話MCPから実サーボへの接続は未実装です。

PCは会話から希望する頻度を保存します。ESP32のファームウェア、通信の実装、実機への反映はESP32担当者が実装します。このAPIの成功はPC側の保存を示し、動作の開始・停止や実機への反映確認を意味しません。

## 接続先

PC内からは `http://127.0.0.1:8765`、ESP32からは同じLANのPCアドレスを使います。ESP32自身の `127.0.0.1` はPCを指しません。このPCの現在のLAN接続方法はリポジトリルートの `LOCAL_SETUP.md` を参照してください。開発中は認証なしです。

## 現在の希望値を取得

```http
GET /api/v1/tail/frequency
```

成功時はHTTP 200、`application/json` です。未設定の既定値は0.6Hzです。

```json
{
  "rate_hz": 0.6,
  "interval_ms": 1667,
  "device_confirmed": false,
  "applies_to": "configuration"
}
```

| 項目 | 意味 |
| --- | --- |
| `rate_hz` | 毎秒のトントン回数。0.3〜1.0の数値 |
| `interval_ms` | 1回ごとの間隔。`round(1000 / rate_hz)` ミリ秒 |
| `device_confirmed` | 常にfalse。実機の受信・反映はこのAPIでは確認しない |
| `applies_to` | 常に`configuration`。希望する設定値 |

将来、別の受信側を実装すればこのGETで希望値を読み出す方式も取れますが、統合済みFollowerにはこのPC設定を取得する処理はありません。PCからESP32へプッシュする処理や、その接続先IP設定は今回含めていません。読み出す頻度、機構への適用、切断時の処理はESP32側で決めます。HTTP成功と上記の数値範囲を確認してから利用してください。

## 希望値を更新

```http
PUT /api/v1/tail/frequency
Content-Type: application/json

{"rate_hz": 0.8}
```

成功時はHTTP 200で、保存後の値を返します。0.8Hzなら`interval_ms`は1250です。JSONは`rate_hz`だけを持つオブジェクトで、値は0.3〜1.0の有限な数値です。文字列・boolean・余分なキーは拒否します。

| HTTP状態 | 意味 |
| --- | --- |
| 400 | JSONや頻度が不正。既存値を保持 |
| 413 | 本文が上限を超過 |
| 415 | Content-Typeがapplication/jsonではない |
| 500 | 保存・読み出しに失敗。実機反映を意味しない |

保存先は既定で `dashboard/data/tail_settings.json` です。`NENGITSUNE_TAIL_SETTINGS_PATH` で変更でき、Gitには含めません。

## 会話からのMCP経路

`TAIL_MCP_ENABLED=true` のPC音声サーバーが、同じPython環境で `STTLLMTTS/tail_mcp_server.py` をstdio起動します。LM Studioの互換Chat APIで選ばれたツールをアプリがMCPへ渡します。

| 会話に公開するMCPツール | HTTP接続 |
| --- | --- |
| `set_tail_frequency(rate_hz)` | PC内のPUTへ保存 |
| `get_tail_frequency()` | PC内のGETから取得 |

ESP32はMCPを実装する必要はありません。LM Studioの`mcp.json`登録も、このアプリ経路には不要です。旧`tap_tail`・`stop_tail`・`get_tail_status`は互換用に残っていますが、現在の会話には公開しません。`TAIL_CONTROL_ENABLED=false`を維持します。
