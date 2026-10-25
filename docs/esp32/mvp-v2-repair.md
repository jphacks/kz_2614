# ESP32担当のMVP v2修理・検証記録

2026-10-25 / 担当branch: `Happy-Emori` / 対象: Follower。
参照main: **`6c4dd226cc4ba1912124c1e1affe0ea0b8603625`**。
実装・試験ソース: **`c1ae7be80bd6031029499b8f6d1c6164db6c7c0b`**。
[共通MVP](../../MVP.md) / [変更していないv2 wire契約](../api/mvp-tail-control-v2.md)。

状態: **ソース実装・テンプレートビルド・模擬試験はPASS。実機書込み・物理受け入れ・公開配備は未実施。**
稼働している旧ファーム/音声サービスの更新を意味しない。以前、利用者がサーボ電源の接続後に「コンコンして」で動いたと報告したのは旧経路の確認であり、このv2版の証跡には数えない。

## 保全とmain取り込み

- `git fetch origin` で上記mainを取得。稼働ツリー `kz_2614-latest` の未コミット変更と、別ツリー `pc-ai-mvp-v2` のRyuuzinnRaika未pushコミット・作業中変更をそのまま保持した。
- `origin/Happy-Emori` の `aae38fb` から別ツリー `esp32-mvp-v2` を作り、cleanな `Happy-Emori` 上で `git merge --ff-only origin/main`。既存履歴の削除・reset・force-pushはしていない。
- 実Wi-Fi/Tail設定、NVS、USB/シリアル、稼働サービスには触れていない。ビルドは公開exampleだけを一時ディレクトリにコピーした。Readerのソースは変更していない。

## 実装の要点

- `TailV2.h`: epoch/revision・許可・phase/session・動作・予算を所有する。HTTP、期限終了、Wi-Fi断、PWM失敗、旧stop、手元操作が同じmutexを使う。PWM書込みとローカル準備の実処理も排他内なので、stop ACK後に保留中の書込みが追い越さない。
- `TailV2Wire.h`: mainと同じstate/open/tap/stop、成功時のcommand_id、指定errorを実装。本文上限1024 bytes、重複/不明キー、欠落、追加データ、非整数・boolean・文字列の数値、非有限値、識別子不正を拒否。整数はJSON整数表記で送り、小数/指数表記へ変換しない。
- `TailV2Http.h`: ESP-IDF HTTPタスクで受信。制御loopは別タスクで期限処理を続ける。接続からの受信期限1秒、本文読取ループ500ms、socket recv待ち100ms、送信待ち1秒。各read後も期限を再検査する。要求1件で接続を閉じ、stateを含め `Cache-Control: no-store`。実際のネットワーク全体の停止時間を保証する値ではない。
- 起動時はRFを開始して128bit乱数epochを生成し、revision=0・closed・sessionなし・idle・開始無効・PWM off。RF初期化失敗時はHTTP制御を起動しない。要求ごとのNVS書込みなし。revision上限前にはclosed/idle・新epoch・無効へ移り、巻戻りで再許可しない。
- 速度は0.3Hz固定（片端への到達を1回とする既存の単位）。校正幅に応じた固定小数点速度で動き、狭い幅を1度/秒へ切り上げない。500〜5000ms、直近60秒の受理時間合計10000ms。20枠の予約で真の移動窓を実装し、途中停止でも返却しない。
- 期限到達はmotionだけidle、sessionはopenを維持してrevisionを進める。stopはclosed/idle・sessionなしにし、既に停止中でもrevisionを進め、最後のPWMを保持する。新openは明示的なPC判断が必要。
- この版は常時 `v2_exclusive`。Reader teleop・angle・無期限konkon・旧tap・全HTTP modeを409で拒否する。旧 `/api/tail/stop` は同じ停止フェンスで受理し、v2 stateを返す。旧GET statusのv1応答は提供しない。PCは必ずv2 stateを使い、v1へフォールバックしない。
- Wi-Fi切断イベントは短い切断も記録し、loopでclosed/idle・disarmへ移す。再接続では自動rearmしない。PWM書込み失敗もclosed/idle・disarm。機械的な到達/停止はセンサーで確認していない。

## 必要な設定と手元操作

`WiFiConfig.h`、`TailConfig.h` はGit対象外のまま。公開exampleは開始許可false、角度90〜90で動かない。実値をPRやログへ書かない。

1. 操作者が対象Follower、サーボ電源・共通GND、個体の可動範囲を確認する。管理ネットワークで固定PC以外から機器へ到達させない。UUID/epochは認証ではなく、ファーム単体で所有権や接続元制限を実装したものではない。
2. 個体の既存NVS `servo-pwm/range` と `curl`、Wi-Fi/Tail設定を保全する。Tailの両端を確認済み範囲とし、`TAIL_TESTED_MAX_RATE_HZ >= 0.3`、`TAIL_MCP_API_ENABLED=true` を限定試験用の非公開設定にする。NVSの有効なrangeが無ければ未校正として拒否する。
3. 新版へ書き込んだ後も起動はPWM off。USBシリアル115200で `v` を確認し、操作者の明示操作 `a` で準備する。**`a` はPWMを有効化し、校正範囲内へ丸めた指令90度へ動かす。** closed/idleのまま開始許可を有効化し、PCがGETで新revisionを取得してからopenする。
4. `x` は保持停止とrevision更新、`d` は停止後PWM off、`n` は停止後中立1500µs。`n/d` をopenで自動解除しない。`t`（Reader復帰）は拒否する。復帰には操作者の新しい `a` が必要。
5. `1..6` はdisarm/PWM off後の校正値調整、`s` は明示保存、`r` は明示消去。今回これらは実機で実行していない。既存値を残す通常更新で `r` や全フラッシュ消去を使わない。

stopは電源遮断・機械的脱力ではない。初回準備や中立操作は動作を伴うため、書込みと同様に操作者・個体を確認してから行う。

## PC担当向けの要求・応答例

RyuuzinnRaikaの `tests/fixtures/tail_v2.json` を内容一致で採用した（SHA-256 `8c81a148a5aee80a0a66e8db272c9742adbea754f846b99b5e3b20e06e986993`）。以下は準備済みの**模擬状態**で、実機のrevisionを予約する例ではない。実機では必ず直前のGET/ACKの値を使う。

`GET /api/v2/tail/state` → 200:

```json
{"protocol_version":2,"capabilities":["revision_fence"],"control_mode":"v2_exclusive","device_epoch":"7ef65580e6e346de8b503259d4e1f40c","revision":0,"session_id":null,"phase":"closed","motion":"idle","start_enabled":true,"calibrated":true,"feedback":"command_only","limits":{"min_duration_ms":500,"max_duration_ms":5000,"rate_hz":0.3,"window_ms":60000,"budget_ms":10000}}
```

全POSTは `Content-Type: application/json`。

`POST /api/v2/tail/open`:

```json
{"device_epoch":"7ef65580e6e346de8b503259d4e1f40c","expected_revision":0,"command_id":"7ed373b7-b211-4f16-8cb5-0623fe19a3d2","session_id":"4e933206-d53c-49b3-8f8a-e86603c88aba"}
```

200は上のstate全項目を含み、`revision=1, phase="open", motion="idle", session_id=要求の値`、さらに要求と同じ `command_id` が付く。

`POST /api/v2/tail/tap`:

```json
{"device_epoch":"7ef65580e6e346de8b503259d4e1f40c","expected_revision":1,"command_id":"7ed373b7-b211-4f16-8cb5-0623fe19a3d3","session_id":"4e933206-d53c-49b3-8f8a-e86603c88aba","duration_ms":500}
```

200は `revision=2, phase="open", motion="tapping"` と要求のcommand_id。500ms後のGETは `revision=3, phase="open", motion="idle"`。

期限後の `POST /api/v2/tail/stop`:

```json
{"device_epoch":"7ef65580e6e346de8b503259d4e1f40c","expected_revision":3,"command_id":"7ed373b7-b211-4f16-8cb5-0623fe19a3d4"}
```

200は `revision=4, phase="closed", motion="idle", session_id=null` と要求のcommand_id。その後GETで同epoch・revision≥4・closed/null/idle/v2_exclusiveを確認する。期限前なら、その時点のrevision=2でstopし、ACKは3となる。

古いopen/tap/stopや重複本文 → 409 `{"error":"stale_revision"}`。再起動前のepoch → 409 `{"error":"stale_epoch"}`。最新revisionで開始を付け替えて再送しない。競合したstopだけ再GET後、新command_idで再試行する。PC側の合計5秒・新会話禁止・旧タスク取消はPC担当の責務。

## 検証結果

| 対象AC | 今回の結果 | 境界・残り |
| --- | --- | --- |
| AC-01 機器版/許可/session | PASS（模擬） | 公開認証・proxy・固定機器の実アクセス制限は未実施 |
| AC-03 有限動作/速度/予算/校正 | PASS（模擬） | 実サーボの速度・接触・電流は未実施 |
| AC-04 独立停止/保持/再開なし | PASS（機器側模擬） | PCの5秒期限、画面表示、物理停止の通し確認は未実施 |
| AC-05 遅着/重複/順序/再起動 | PASS（模擬） | 実ネットワークで要求を保留・解放する試験は未実施 |
| AC-07 版と証跡 | 本記録を作成 | Web/PC/機器を揃えた実スマホ通し証跡は未実施 |
| AC-02 / AC-06 | 今回の担当外・未実施 | 音声3往復・日記の受け入れを代行しない |

- Arduino CLI 1.5.1、Arduino-ESP32 **3.3.12 / ESP-IDF 5.5.5**、FQBN `esp32:esp32:esp32`、公開templateでFollowerビルドPASS。Flash **986887 / 1310720 bytes**、static RAM **47996 / 327680 bytes**。警告なし。ビルドログ: `/tmp/esp32-v2-build.log`。ビルド出力は書込み不可として扱う。
- C++17/GCC 15.2 + cJSON **1.7.19**（実機SDKと同版）、Address/Undefined/LeakSanitizer付き。Python **24テスト、skip=0、PASS**（0.658秒）、C++競合 **500回＋PWM/手元準備の排他2ケース、PASS**、実HTTPアダプターのstub I/O **7ケース、PASS**。`/tmp/esp32-v2-native-final.log`。
- PCの作業中 `server/ai/llm/tail_v2.py`（SHA-256 `cb81484f77e6846c31adc2f9f2438d1518fb91b87b577f537516af356b6688df`）の `validate_state` / `validate_ack` へ、コンパイルした機器コードの実JSONを渡してPASS。これは担当者のレビュー承認やPC全体の受け入れを意味しない。PC側ファイルは変更していない。
- 最初のnative実行はsandboxのptrace制限でLeakSanitizerが終了。制限外で、同じ実機通信なしの模擬試験を再実行して上記PASSを得た。失敗した実行を成功件数へ含めていない。
- 再現コマンドと依存取得: [tests/esp32/README.md](../../tests/esp32/README.md)。`git diff --check`、Git除外設定を確認。サーバー/Webコードは変更していないため、その全体テストを今回の合格として再掲しない。

## 未実施の実機試験と実施条件

操作者名・固定Followerの識別（USBポートだけでなく個体/MAC）・立会い時間を確認してから、以下を行う。シリアル接続やMAC確認でのリセットも未実施。実Wi-Fi・個体設定・バックアップはGit対象外で保管する。

1. 旧ファーム版と実設定を記録し、全フラッシュ/NVSバックアップを取得。既存校正の保存・復元方法を確認する。パーティション表を維持し、`EraseFlash=none`。このPRのtemplateバイナリは書き込まず、確認した非公開設定で別途ビルドする。以前のPC専用build/flash helperは旧checkout固定なので、新版ソースを対象にしていることを確認せず流用しない。
2. 書込み・起動後、PWM off・closed/idle・開始無効・新epochをGETと観察で確認。旧epochのopen/tap/stopが409になることを確認する。設定/NVSの保持も比較する。
3. 操作者が `a` で準備し、GET→open→500ms tap→期限、次にtap途中stopを試す。PWM保持と機械的停止を別に観察・記録する。最大5秒、0.3Hz、60秒10秒予算も段階的に確認する。
4. 古いopen/tapをproxyで保留し、stop受理・再GET・次session open後に解放する。旧要求409、動作なし、session上書きなしを確認。重複・応答喪失・順序逆転・自然期限とstopの競合も確認する。
5. Readerの継続送信・旧APIをclosed/open/停止後に試し、409と動作なしを確認。未完了/低速HTTP本文と並行した期限終了・stop、新しいGETが観測できることを確認する。CPU負荷・socket待ちの実測はここで記録する。
6. Wi-Fi断/復帰、再起動、neutral/relaxを試し、操作者の再準備なしで動かないことを確認。最後にPCの停止確認5秒・未確認表示・新会話禁止を含む実スマホ通し試験へ進む。

各行に実施日時、Web/PC/ファームSHA、個体、要求/ACK/GET、物理観察、PASS/FAILを記録する。実機未実施のまま最小MVP全体を完了としない。PRはmain向けに提出し、自動ではマージしない。
