# 今回のMVPと共通修理手順

更新日: 2026-10-25。今回の対象は **スマホで会話＋しっぽトントン＋停止＋利用記録**。
この文書を今回の範囲・境界・修理順・合格条件の唯一の正本とする。
[全体計画](docs/requirements/system.md)は将来の体験を含む資料として保持する。
広い計画や過去履歴にある機能を、今回の合格条件へ追加しない。
この文書の追加は実装・配備・実機受け入れの完了を意味しない。

## 1. 全員が同じ版を読む

- 常設URL: [mainのMVP.md](https://github.com/jphacks/kz_2614/blob/main/MVP.md)
- 自分のブランチの古いコピーだけを見て作業を始めない。
- 読むだけなら、ブランチの切替やmergeは不要。
- 取得したmainのSHAを修理メモ・PRへ残す。

```bash
git fetch origin
git rev-parse origin/main
git show origin/main:MVP.md
git show origin/main:docs/api/mvp-tail-control-v2.md
```

この文書の作成基点はmain `3b6a0c13784903ddbab284e7908b7dbfb2b6536d`。
これは固定の最新版指定ではない。以後は取得した `origin/main` を確認する。
この更新ではmainに文書を公開し、各メンバーのブランチを書き換えない。

## 2. 見せる一連の体験

1. 指定操作者が認証済みの製品Webを実スマホで開く。
2. 固定した1台の人形を確認し、15〜90分の会話時間を選んで開始する。
3. 入力準備完了（ready）から計測し、音声で3往復以上会話する。
4. 会話中に操作者が明示して頼んだ場合だけ、有限のトントンを行う。
5. 手動・期限・非表示・障害で、端末・サーバー・人形の停止を確認する。
6. 実際の開始/終了、予定期限、終了理由、記録IDを一度保存する。

今回の試験単位は **1会話・1固定人形・指定操作者**。
指定操作者と固定機器へのアクセス制限を前提にする。
既存の公開認証を外さず、公開HTTPS・転送・PCへの到達経路を確認する。
本格的な本人情報・人形所有権・共有権限（Issue #10）の一括実装は今回行わない。
限定した実機試験の合格を、公開多人サービスの受け入れと表現しない。

## 3. 今回含めないもの

- Readerによる他者操作と会話/他者モード切替。
- 帰宅検知、起床アクション、睡眠中の自動動作。
- 接触センサー、BLE、会話要約・音声本文の保存。
- ネイティブAndroid、ESP32音声入力/出力の完成。
- 新しい製品UIへの作り直し、新音源・サウンド再生の追加。
- 本格的な複数人・複数人形の所有/共有管理。

これらは次段階として保持する。関連する既存Issueを閉じたり完了扱いにしない。
既存UIのサンプルや未接続表示を実機の成功として扱わない。

## 4. 担当と接続境界

| 担当（利用者指定のGitHub名） | 今回の責務 | 主な場所 |
| --- | --- | --- |
| RyuuzinnRaika | PC・AI、音声セッション、MCP、停止制御 | `server/api`、`server/ai` |
| Happy-Emori | ESP32、機器契約、停止フェンス、実機 | `firmware/esp32`、機器API |
| naminamina | 製品Web、共通音声クライアント、停止表示・利用記録 | `apps/hossy-web`、`apps/web` |
| takemi853 | 担当未指定。担当確定までは特定領域へ固定しない | 未指定 |

過去のkameyama/HOSSy/EMORI等の呼称とGitHub名が本人一致するとは断定しない。
3Dプリント部品やAndroidの担当をtakemi853へ推測で割り当てない。
担当はアクセス権の制限ではなく、境界の確認先。
詳細は[担当範囲](docs/development/ownership.md)を参照する。

| 境界 | 契約の正本 | 双方の確認 |
| --- | --- | --- |
| Web ↔ PC | [音声HTTP](docs/api/voice-http.md) | naminamina / RyuuzinnRaika |
| PC ↔ MCP ↔ ESP32 | 修正目標は[機器v2](docs/api/mvp-tail-control-v2.md)。現状は[機器HTTP](docs/api/esp32-http.md)、[MCP統合](docs/esp32/mcp-integration.md) | RyuuzinnRaika / Happy-Emori |
| PC内の希望頻度 | [頻度API](docs/api/tail-frequency.md) | 実機指令との区別を全員確認 |

共有契約を変えるときは、mainのAPI文書を先に更新し、両端の担当で確認する。
新しい停止契約は [未実装v2契約](docs/api/mvp-tail-control-v2.md) に揃える。変更はmainの仕様PRを先に出して双方確認する。
未実装案を現行APIの対応済みキーとして送信しない。

## 5. 最新mainから再利用するもの

PR #25を統合したmainには、最新製品Webの音声接続コードがある。
固定期限、ID別の日記、保存異常表示、Service Worker保護、任意設定proxyも含む。
Webを新規に作り直す前提には戻さない。
公開配備、認証後の実スマホ音声、ESP32との通し確認は未確認。

- 共通音声クライアントの正本は `apps/web/voice_call.js`。
- `apps/hossy-web/public` の生成コピーを手編集しない。
- 製品WebのAPIは同一origin。配信側から音声PCのHTTP 8765番へ転送する。
- `VOICE_API_UPSTREAM` の設定・到達性と公開版のSHAを確認する。
- 静的previewにはproxyがない。静的画面の表示成功をAPI接続成功にしない。
- VADと発話分割はサーバー側。ブラウザー独自VADを追加しない。
- 継続PCM入力・返答・割込みを維持し、別録音ボタン方式へ戻さない。
- readyになる前の待ち時間を利用時間へ加えない。
- 15〜90分の期限は開始時に固定し、会話中の設定変更で延長しない。
- 開始失敗を利用成功として記録しない。

## 6. しっぽの現状と修正目標

PC側の後続修理・模擬試験は [PC・AI v2修理記録](docs/development/pc-ai-mvp-v2.md) を参照。以下の「現状」は修理基点mainの状態を記録しています。PC実装の追加をESP32対応・配備・全AC合格とは扱いません。通信契約と合格条件は変更していません。

**現状**: 会話MCPのallowlistは `set_tail_frequency` / `get_tail_frequency` の2操作。
PC内の希望頻度の保存・取得であり、ESP32への動作成功ではない。
子MCPの環境には `TAIL_CONTROL_ENABLED=false` が強制される。
互換用の旧 `tap_tail` の既定0.6Hzを実機用へ転用しない。
現行Follower APIはsession・stop_generation等の新しい停止契約に未対応。

**これから実装する目標**（現行機能として使用しない）:

- `tap_tail(duration_seconds)` は0.5〜5秒、速度は0.3Hz固定。
- `stop_tail()` と `get_tail_status()` を停止・確認の経路として用意する。
- LLMへ任意URL・サーボ角度・速度やsession情報を渡さない。
- サーバーが明示要求、現在のsession/turn/generation、機器、許可を検査する。
- 残り時間予算・頻度上限・機器状態を検査し、拒否理由を返す。
- 中立/脱力状態を自動解除しない。開始の不明な失敗を自動再送しない。
- 実機は既定無効を維持し、契約確認・模擬試験の合格後に、操作者と時間を確保して限定実機試験へ進む。通し合格前に通常利用へ有効化しない。

### 停止フェンスは機器側にも必要

PC/MCPだけの世代検査と停止再送では、ネットワーク中の古い開始要求を拒否できない。
stopより遅れて機器に届くtapが、停止後に動作を再開する可能性が残る。
そのため、機器が開始受理と同じ原子的な処理で停止フェンスを検査する契約を必須とする。

PCとESP32担当は [未実装v2契約](docs/api/mvp-tail-control-v2.md) を共同で確認し、次の境界を実装・検証する。

1. version/capabilityの確認と、未対応機器への開始拒否。
2. epoch・世代等による現在の会話と停止フェンスの識別。
3. 冪等なcommandの識別と、重複・遅延・順序逆転の扱い。
4. stop後に届く旧開始要求の機器側拒否。
5. 機器再起動や次sessionの後にも旧開始要求を拒否する方法。
6. 状態照会で確認できる値と、不明時の終了・再開条件。

具体的なwire形式・機器の原子的な検査/状態遷移はv2契約を正本とし、この文書へ重複定義しない。
Webは機器APIを直接呼ばず、既存voice停止へ追加するtail_control結果を表示する。
停止未確認（v2の `tail_control.status=unconfirmed`）なら新会話の実機開始は禁止する。
旧機器に未対応キーを追加送信して安全になったと判断しない。
v2未対応または契約/capabilityが未確認の旧機器では実機開始を無効にする。
修理順は **契約確定 → mockで停止試験 → 実機実装 → Web接続**。

## 7. 停止・結果表示・記録の境界

- 手動・期限・非表示・障害時は、端末のマイク/再生を即時停止する。
- サーバーDELETEは対象sessionのJSON内容を確認し、確認待ちを5秒で打ち切る。
- HTTP 200だけ、またはログインHTMLをサーバー停止確認としない。
- しっぽ停止はLLMの完了待ちから分離する。
- stop後の旧LLM応答・MCP処理・HTTP tapが新しい開始を生まないようにする。
- stop後の再読込・画面復帰でマイクや人形を自動再開しない。
- 要求送信、機器の受理、停止状態の再照会、物理観察を分けて記録する。
- 通信断や応答不整合は「停止未確認」と示し、停止確認済みとは表示しない。機器結果のwire値はv2契約の `unconfirmed` に統一する。
- 現行のstopはPWMを保持する。電源遮断・機械的な脱力を意味しない。
- 実停止時刻と、サーバー/機器の確認完了時刻を混同しない。
- 利用記録はIDで一度保存し、音声本文や資格情報を残さない。

## 8. 修理の順序と終わり方

| 順序 | 作業 | 終了条件 |
| --- | --- | --- |
| 1 | 全員が最新mainのこの文書と担当を確認 | 基点SHA・対象AC・担当境界が記録される |
| 2 | PC/ESP32で未実装v2契約とcapabilityを確認・確定 | API正本・非対応時の拒否条件を双方確認 |
| 3 | mockで許可・停止・遅着・次sessionの試験を追加 | AC-01/03/04/05に対応する失敗再現ができる |
| 4 | PC/MCPと機器へ契約を実装、限定実機試験 | 旧要求拒否と停止を機器で確認 |
| 5 | 既存Webを契約へ接続し、記録・表示を確認 | AC-02/04/06を実スマホで確認 |
| 6 | 同じ版・経路・固定人形で通し確認 | AC-01〜07の証跡と未確認が揃う |

各自の修理前にローカルの未コミット作業をコミットまたは安全に退避する。
未pushコミットの有無は他人のリモート先端から推測しない。
作業ツリーがcleanで自分のブランチにいることを確認してからmainを取り込む。

```bash
git status --short
git branch --show-current
git fetch origin
git merge origin/main
```

force-pushやresetで揃えない。競合では双方の変更を保持する。
判断できない競合は `git merge --abort` で戻し、該当箇所を担当間で確認する。
無関係な作業ブランチを一括mergeしない。修理を小さなmain向けPRにする。
PRには基点SHA・対象AC・対象テストと結果・未確認を必ず記載する。

### 修理メモのテンプレート

```text
修理ID / 対象AC:
担当 / 対象branch / 基点main SHA:
症状・再現条件:
境界契約（参照API、現状と変更後）:
変更ファイル・差分の要点:
検証コマンド・結果・証跡:
未確認・残る制限・次の一手:
```

### 各担当の修理チャットへ渡す依頼文

```text
jphacks/kz_2614 のorigin/mainをfetchし、git show origin/main:MVP.md と
git show origin/main:docs/api/mvp-tail-control-v2.md を読んでください。
参照したmain SHAを記録し、自分の担当ブランチの既存変更を保全してください。
MVP.mdの担当表に沿って自分の修理範囲と対象ACを提示し、その範囲を実装・検証してください。
既存のWeb実装を再利用し、共通契約の変更はmainの仕様PRを先行させてください。
模擬試験・配備・実スマホ・実機確認を区別し、結果と未確認を添えたmain向けPRにしてください。
他担当ブランチの上書き、force-push、未指定のサービス再起動・実機動作は行わないでください。
```

## 9. 合格条件

既存IssueのMVP-01等と混同しないよう、この文書のIDはACを使う。
自動試験の成功と実スマホ/実機の合格をそれぞれ記録する。

| ID | 合格条件 |
| --- | --- |
| AC-01 | 同一origin/API転送・認証・機器版を確認。401/403・ログインHTML・API不達・マイク拒否を開始成功にしない。未許可、未対応capability、旧session/世代、機器不明で実機開始を拒否する |
| AC-02 | 実スマホで3往復以上。10秒無音後も次の発話を受け、返答中の割込みで旧返答が止まり新発話へ進む |
| AC-03 | 明示要求と現在の許可があるtapだけ受理。0.5〜5秒、0.3Hz固定、時間予算・上限の超過を拒否し、中立/脱力を自動解除しない |
| AC-04 | 手動・期限・非表示・通信/処理障害で端末停止と独立したしっぽ停止を確認。サーバー確認5秒上限、停止未確認表示と再開なしを確認 |
| AC-05 | 旧LLM・MCP・HTTP tapを意図的に保留し、stop後および次sessionで解放しても再開しない。機器再起動・重複・順序逆転も機器側フェンスで拒否する |
| AC-06 | ready時刻・実停止時刻・予定期限・理由・IDを一度保存。開始失敗を成功記録にせず、保存失敗、複数タブ、再読込、100件上限を確認する |
| AC-07 | Web/PC/機器の版、日時、スマホ/ブラウザー、固定機器、認証・proxy経路、AC別結果が揃った通し証跡がある |

AC-05の新しい機器停止フェンス試験は **追加が必要**。既存頻度MCP試験で代用しない。
物理観察の記録がない状態で「人形停止確認済み」としない。
未確認・外部環境待ちは理由と次の作業を残す。文書やmockだけでは全体合格にしない。
全ACが対象実環境で合格した時点で最小MVP完了とする。各行にPASS/FAIL/未実施、検証SHA、結果・証跡を残し、未実施をPASSへ置き換えない。

## 10. 既存の検証コマンド

リポジトリルートから、変更に対応する既存試験を使う。

```bash
server/.venv/bin/python -m unittest discover -s tests -v
node tests/web/product_voice_test.js
node tests/web/product_storage_test.js
node tests/web/product_proxy_test.cjs
node tests/web/product_sw_test.cjs
```

WindowsのPythonは `server\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
上記4つのproduct Node試験は既存Python wrapperに未登録なので、個別に実行する。
製品Webのビルドは `apps/hossy-web` で行う。

```bash
npm ci
npm run build
```

これらは公開proxy・実マイク・機器の物理停止を証明しない。
この文書更新ではハーネスコードを追加せず、サービス再起動・音声停止・機器操作をしない。
実装修理では必要な停止フェンス試験を追加し、実機試験の時間と機器を担当間で確保する。

## 11. 記録と参照先

- 修理結果: [開発履歴](docs/development/work-history.md)
- 担当: [ownership](docs/development/ownership.md)
- ブランチの保全と取り込み: [branches](docs/development/branches.md)
- 全体計画: [system](docs/requirements/system.md)
- API: [一覧](docs/api/README.md)
- 実スマホと公開経路: [デモ確認](docs/demo.md)

過去の日付付き記録は残す。実装・配備・実機確認のどこまで進んだかを明記する。
