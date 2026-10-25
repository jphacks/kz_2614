# MVPしっぽ制御 v2 — 修正目標の通信契約

**状態: Web・PC・ESP32 Followerのv2実装を統合。模擬検証と実機書込み・公開配備・通し受け入れは区別し、後者は未確認。旧main `3b6a0c1` のFollowerでは使えません。wire契約は変更していません。**

[PC修理記録](../development/pc-ai-mvp-v2.md) / [機器修理記録](../esp32/mvp-v2-repair.md)。

[共通MVP仕様](../../MVP.md)の有限トントン・停止・遅着拒否を、PC担当 `RyuuzinnRaika` とESP32担当 `Happy-Emori` が同じ形式で実装します。Web担当 `naminamina` はPCの結果を表示し、機器HTTPを直接呼びません。既存の[機器HTTP API](esp32-http.md)は現行v1の説明として保持します。

## 1. 対象と前提

- 指定操作者・固定PC controller・固定人形各1、同時会話1つ。機器のURLと対応個体はPCの管理設定で固定し、ブラウザーやLLMの任意URL・device指定を採用しません。
- 既存公開認証を維持し、機器APIは管理されたPCから到達する経路に限定します。以下のUUIDやepochは順序制御用で、本人認証や所有権の証明ではありません。本格的な所有・共有管理は[#10](https://github.com/jphacks/kz_2614/issues/10)の別要件です。
- 機器の開始許可は既定無効。操作者が個体・可動範囲・配線・モードを準備してから有効化します。中立・脱力の自動解除やReaderへの自動復帰はしません。
- v2運用は `control_mode="v2_exclusive"`。有効な間は、closed中も含め、旧 `/api/teleop`・`/api/angle`・`/api/konkon`・動作を許す `/api/mode` を拒否します。旧停止やローカル停止を受け付ける実装は、必ず後述の同じ停止処理でrevisionも更新します。旧経路が停止フェンスを迂回して動かせる実装は不合格です。
- capability未対応、v1だけの応答、不正なJSON、epoch不明は実機開始不可。v1への自動フォールバックや、旧tapへ追加キーを付ける代用は禁止します。

## 2. 識別子と状態

| 名前 | 定義 |
| --- | --- |
| `device_epoch` | 機器が起動ごとに生成する128bit以上の乱数を32桁小文字hexで表す。RAM保持。前回起動と再利用しない |
| `revision` | 機器が管理する0からの整数。成功した変更要求と、有限動作終了・ローカル停止などの状態変更で増やす |
| `expected_revision` | PCが直近の状態応答から得たrevision。未来値を予約・推測しない |
| `session_id` | PC側の現在の音声会話UUID。会話をまたいで再利用せず、LLMの引数にしない |
| `command_id` | PCが変更要求ごとに作るUUID。同じ要求は同じ本文とIDを持つ。認証用途ではない |
| `phase` | `closed` または `open` |
| `motion` | `idle` または `tapping`。機械的な位置・接触の測定値ではない |

revisionの型はJSONで安全に表現できる非負整数（最大 `9007199254740991`）。最大値に達する前に停止・closedへ移り、新epochへ切り替えます。桁あふれ・巻戻りで古い要求を再び有効にしません。起動時も新epoch・closed・sessionなし・idleとし、保存された会話やtapを再開しません。要求ごとのNVS書込みやPCとの時計同期は不要です。

## 3. APIと応答

起点は固定したFollowerのHTTP origin。全変更要求は `Content-Type: application/json`、最大1024 bytes、必須フィールドだけのobjectとします。数値のboolean・文字列・非有限値、重複キー、不明キーは拒否します。受信本文には有限の読取期限を設け、未完了本文で停止処理を塞がないようにします。

| API | JSON本文 | 条件と作用 |
| --- | --- | --- |
| `GET /api/v2/tail/state` | なし | 下記の現在状態。キャッシュせず返す |
| `POST /api/v2/tail/open` | 共通3項目＋`session_id` | closed・idleかつ開始許可/校正/許可モードを確認し、openと対象sessionを設定。動作は開始しない |
| `POST /api/v2/tail/tap` | 共通3項目＋`session_id`＋`duration_ms` | open・idle・同じsession・開始許可を確認し、有限tapを開始 |
| `POST /api/v2/tail/stop` | 共通3項目 | 開始許可の有無によらず、動作とReader追従を停止、closed・sessionなし・idleへ。既にclosedでもrevisionを増やす |

共通3項目は `device_epoch`、`expected_revision`、`command_id`。`duration_ms` は500〜5000の整数。速度は機器側で0.3Hz固定、直近60秒の受理済み要求時間の合計は10000ms以下とします。早期停止による予算返却は行いません。速度・角度の引数は追加しません。校正済みの可動範囲だけを使います。

stateの最小応答例（値は例示）:

```json
{
  "protocol_version": 2,
  "capabilities": ["revision_fence"],
  "control_mode": "v2_exclusive",
  "device_epoch": "7ef65580e6e346de8b503259d4e1f40c",
  "revision": 8,
  "session_id": null,
  "phase": "closed",
  "motion": "idle",
  "start_enabled": false,
  "calibrated": false,
  "feedback": "command_only",
  "limits": {"min_duration_ms": 500, "max_duration_ms": 5000, "rate_hz": 0.3, "window_ms": 60000, "budget_ms": 10000}
}
```

成功応答は200で、この状態objectに要求の `command_id` を加えます。状態値・識別子・上限が不一致なら成功扱いしません。通常のtap期限到達ではmotionだけidleへ戻してrevisionを増やし、sessionは明示stopまでopenに保ちます。stopは最後のPWMを保持し、電源遮断を意味しません。

失敗はJSON `{ "error": "コード" }`。400=`invalid_request`、403=`start_disabled`、409=`stale_epoch` / `stale_revision` / `invalid_phase` / `session_mismatch` / `mode_not_ready`、429=`budget_exceeded`、503=`not_calibrated`。失敗によって指令を実行・予算消費しません。エラー後の状態確認はGETで行います。

## 4. 原子的な停止フェンス

**全変更要求について、epochとrevisionの照合、条件検査、状態変更、revision増加を機器内の同じ排他区間で行います。** epoch違い・revision不一致は副作用なしで409。自然な期限到達やローカル停止とも同じ排他を使います。

例: 古いopen/tapが `expected_revision=7` で遅延中にstopが7を受理すると、機器はclosed・revision=8になります。古い要求は後から到着しても409であり、openそのものの遅着でも会話が復活しません。

- open/tapはPC側で同時1要求まで。応答を確認してから次のrevisionを使います。stopはその待ち行列・LLM推論待ちの外から発行します。
- 同じ変更要求が二重到着しても、最初の受理でrevisionが進むため二度目は409となり、再実行しません。409を過去の成功の証拠にしません。
- open/tapは応答喪失・409でも自動再送、再open、最新revisionへの付け替えを行いません。開始不明ならstopへ進み、手動の新しい開始判断を待ちます。
- PCは停止開始時に音声会話世代を無効化し、未送信open/tapを破棄。モデル完了時、MCP呼出し直前、機器HTTP送信直前にも現在session/turn/世代を検査します。旧タスクが最新revisionを読み直して再発行するのは禁止です。
- 新しい会話は、前の会話の停止確認が済んでから新sessionでopen。PCは旧停止処理を終了させ、古いstopタスクが新会話用revisionで再試行しないよう直列化します。
- 機器epochの変化を検出したPCは会話を終了し、自動openしません。古いepochのopen/tap/stopはすべて拒否されます。

## 5. 停止確認とWebへの受け渡し

1. Webは端末の入力・出力・待機音声を直ちに停止。PCは音声会話を無効化し、LLMを待たず機器stopを発行します。
2. stopが409なら最新stateを取得し、新command_id・最新revisionでstopを再試行できます。これは停止だけの例外です。epochが変わった場合は新会話へ移らず未確認とします。
3. PCは停止開始から**合計5秒**で確認処理を打ち切ります。機器HTTP各要求の期限は残り予算以内とし、機器待ちで既存の音声DELETE応答確認5秒を越えないよう、超過分を待たず未確認を返す実装にします。
4. 指令停止確認の条件は、stopの200/正しいcommand_idと、その後のGETで**同一epoch、stop応答以上のrevision、closed、session=null、idle、v2_exclusive**を確認することです。cached ackだけで確認済みとはしません。
5. 通信断等で確認できなければ「機器停止未確認」。新たな実機会話開始を禁止し、操作者が現物と通信を確認します。PCの停止クリック直後からネットワーク未到達の機器停止まで保証したとは説明しません。機器指令停止と物理観察も分けます。

Web側の新しい機器URLは作りません。[既存voice DELETE](voice-http.md)の `session_id`・`state`・既存フィールドは保持し、**追加する修正目標**は次の `tail_control` です。

```json
{"tail_control":{"status":"command_stopped","device_epoch":"7ef65580e6e346de8b503259d4e1f40c","stop_revision":8,"feedback":"command_only"}}
```

`status` は `command_stopped` / `unconfirmed` / `disabled` のいずれか。epoch/revisionは不明ならnull。`disabled` はこの会話で実機制御を一度も許可していない場合だけで、一度送信した後の失敗をdisabledへ変えません。未対応の旧PCで `tail_control` が欠けた場合も機器停止確認には使いません。既存 `audio_device_stop_confirmed=false` を機器停止の成否へ読み替えません。

機器付きMVPセッションでは、PCが版対応・固定機器・開始許可を確認できるまでtapを禁止します。Webの日記は実際の端末停止時刻で一度だけ保存し、機器の確認待ちで時刻を伸ばしたり二重記録したりしません。機器停止結果を保存する場合は同じ記録IDに上記の確認状態のみを追加し、物理動作成功を自動記録しません。

## 6. 同じ契約で実施する必須試験

PCの模擬機器とESP32実装は同じJSON fixtures・期待状態で比較します。PCは `tests/fixtures/tail_v2.json`・`tests/tail_v2_mock.py`・`tests/test_tail_v2.py`、機器側は [v2専用ホスト試験](../../tests/esp32/README.md) を使用します。既存の頻度保存試験やPC mockを実機試験の代用にしません。

| 入力・競合 | 必須結果 |
| --- | --- |
| 正常open→tap→期限→stop | 速度/時間/予算を守り、revisionが進む。最後はclosed/idle |
| open/tapを保留→stop受理→旧要求解放 | 409、動作も再openもなし |
| 同一open/tapが二重到着 | 1回だけ受理。2回目は409、期限延長なし |
| tap受理とstopが競合 | tap先行ならstop再照会後に停止、stop先行ならtap拒否 |
| stop応答喪失・再試行・GET失敗 | 合計5秒以内に確認済みか未確認。新tapを自動開始しない |
| stop→新session→旧要求解放 | 新sessionを旧open/tapが上書きしない。旧stopタスクも新sessionへ再試行しない |
| 再起動→旧epochの全要求 | closed/idle起動、旧要求拒否、自動再開なし |
| v2中にReader/旧angle/konkon/modeから動作要求 | 停止フェンスを迂回できない |
| v1機器・不正capability・範囲外・未校正・予算超過 | 開始しない。成功表示しない |

模擬試験後に操作者立会いで有限動作・途中停止・期限・再起動を確認します。ファームビルド、実機試験、公開配備はこの文書の追加では実施していません。
