# PC・AI MVP v2修理記録と引き継ぎ

- 日付: 2026-10-25、担当: RyuuzinnRaika。
- 参照main: `6c4dd226cc4ba1912124c1e1affe0ea0b8603625`。作業前とPR準備前にfetchし、同じSHAを確認。
- 契約: [MVP](../../MVP.md)、[機器v2](../api/mvp-tail-control-v2.md)、[音声HTTP](../api/voice-http.md)。wire形式・endpoint・許容値は変更していないため、仕様変更PRは不要。
- 状態: PC実装・模擬試験・既存回帰まで。サービス再起動、設定反映、実機操作、公開配備、PRマージは行っていない。

## 保全とmain取り込み

稼働checkout `kz_2614-latest` は `feature/web-voice-integration` / `9273748e69cafd3de6c0112d8b56e96b7575c584` のまま保持。変更40ファイルをアーカイブ・binary diff・SHA256 manifestで保全し、全Git参照をbundleに保存した。保管先はcheckout外の `backups/mvp-v2-20261025-104215/`。秘密設定・音声・モデルはGitへ追加していない。作業後、40ファイル全てがmanifestと一致した。

`git log --branches --not --remotes` にローカルだけのcommitはなかった。取得したRyuuzinnRaikaとHappy-Emoriの先端はどちらも `aae38fb72b5c528e783ff259545fcb9035ec0093`。作業ブランチのmainにないcommitはなく、独立worktree `pc-ai-mvp-v2` でRyuuzinnRaikaを作り、`git merge origin/main` でfast-forwardした。mainの製品Webを保持し、他担当branchはmergeしていない。既存の未コミット音声lease/明示要求処理を参照して移植し、v1送信をv2へ置換した。関連しない稼働Web変更は元checkoutに保持した。

## 実装の要点

- `server/ai/llm/tail_tools.py`: 最終STTの今回の発話と履歴を分離。明示した短いトントン要求だけを有限tapへ接続。否定・引用・過去の話・感情だけでは開始しない。要求内の秒数を優先し、1発話1開始。allowlistはtap/stop/statusの3つ。余分な引数、boolean、非有限値、重複キーを拒否。
- `tail_mcp_server.py`: 音声所有のMCPServerをin-process Clientで使用。session/turn/許可はモデル引数でなく、サーバーが閉じ込めたleaseから取得。HTTP直前にも有効性を検査できる。単体stdioはPC頻度設定の互換2操作だけを保持し、実機への旧経路は削除。
- `tail_v2.py`: 固定private origin、redirect/proxyなし、v2 stateの型・capability・exclusiveモード・上限・epoch/revision・command/session一致を検証。v1へのfallbackなし。管理設定や開始フラグだけではv2対応を証明できない。
- `tail_control.py`: 同時1人形所有、open/tap同時1要求、直近60秒10000msのPC予算。0.5〜5秒、機器速度0.3Hz固定。未知の開始を再送・再open・新revisionへ付け替えしない。機器再起動を検出した会話は終了。
- `voice/sessions.py`: LLMや開始要求を待たず世代を無効化してstop。手動・期限・割込み・取消・処理失敗・TCP終了に接続。クライアントが途絶えてもidle期限を1秒ごとに検査。旧turnの後片付けは新turnを停止しない。
- `voice/pcm_stream.py`: VAD障害・再生失敗も独立停止へ接続。継続PCM・サーバーVAD・割込み・音声転送形式は維持。
- stopだけ409後に最新state・新command_idで再試行（最大8回）。停止全体4.5秒、同期DELETE待機最大4.7秒。stop応答と後続GETの両方でclosed/session=null/idle・同epoch・十分なrevisionを確認して初めて`command_stopped`。
- 未確認停止・epoch変更はプロセス内で開始禁止を保持。古いstopが次会話のrevisionで再試行しない。環境フラグを戻すだけでは解除しない。復旧は操作者が現物・通信を確認し、別途承認した運用復旧として扱う。復旧用の独自HTTP APIは追加していない。

## ACごとの証拠と範囲

| AC | 今回の結果 | 残る確認 |
| --- | --- | --- |
| AC-01 | 模擬PASS: 未許可・v1・不正JSON/型/capability・未校正・機器不明・旧session/turn・別会話所有を拒否。redirect/HTMLも拒否 | 公開認証・HTTPS/proxy・固定個体・機器版の現地確認 |
| AC-02 | 回帰PASS: 既存PCM・NDJSON・連続入出力・割込み・TCP試験を維持 | 実スマホ3往復、10秒無音、実返答中の割込み |
| AC-03 | 模擬PASS: 明示要求、引数/結果検査、0.5〜5秒、予算、busy、未許可/未校正、二重開始拒否 | 実機可動範囲、0.3Hz、自然期限・校正・中立/脱力 |
| AC-04 | 模擬PASS: DELETEの音声/機器結果分離、期限・取消・処理/出力障害、停止応答喪失・GET失敗・全体期限。Web既存停止試験PASS | Webによるtail_control表示/保存、実スマホ非表示・実通信断と物理観察 |
| AC-05 | 模擬PASS: open/tap保留→stop→遅着、tap先行とstop先行、次session、旧MCP/turn、重複、epoch変更、停止応答不整合 | ESP32が同じ排他区間でrevisionを検査・変更し、旧経路も拒否する実装と実機試験 |
| AC-06 | 既存Web記録の回帰PASS。PCは既存session_id/音声停止フィールドを保持 | Web担当による同じ記録IDへの停止結果接続、実スマホ記録 |
| AC-07 | 基点SHA・試験・担当境界を記録 | 同じWeb/PC/ESP32版・固定機器・日時・認証経路を揃えた通し証跡 |

模擬機器は `tests/tail_v2_mock.py`、共通JSON fixtureは `tests/fixtures/tail_v2.json`。firmwareの動作保証や実機受け入れの代わりにはならない。

## 実行コマンドと結果

リポジトリrootで以下を実行した。Pythonは既存の `kz_2614-latest/server/.venv/bin/python` を使用したが、cwdとimport元は独立worktree。`.env` はコピーせず、下記の無効設定で実機へ接続しない。試験内の例外は注入したmockまたは一時loopback HTTPだけ。

```bash
TAIL_CONTROL_ENABLED=false TAIL_DEVICE_URL=http://127.0.0.1:1 \
NODE=/home/nami/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
/home/nami/JPHACKSkz_2614/kz_2614-latest/server/.venv/bin/python -m unittest discover -s tests -v
node tests/web/product_voice_test.js
node tests/web/product_storage_test.js
node tests/web/product_proxy_test.cjs
node tests/web/product_sw_test.cjs
git diff --check
```

- Python **266件、skipなし、13.084秒、OK**。v2専用22件と音声接続7件、MCP/LLM・頻度設定・既存音声/PCM/TCP/Web wrapperの回帰を含む。
- 製品Webの独立Node4試験も全てPASS。上記`node`は指定したbundled Nodeの絶対パスで実行。
- 差分空白検査PASS。音声本文・資格情報を新しいログやfixtureへ保存していない。
- `apps/hossy-web` でnpm CLIをNodeから実行し、`npm ci --cache /tmp/npm-cache` 成功（lockfileから185 packages、audit 0件）。`ASTRO_TELEMETRY_DISABLED=1 npm run build` 成功（1 page）。npmは既存の`/tmp/.pnpm-store/v11/links/@/npm/11.21.1/…/node_modules/npm/bin/npm-cli.js`を使用。lockfile・製品Webソースは変更していない。
- 初回のoffline依存取得はcache不足、初回buildはAstroの設定保存先が未作成で失敗。通常のlockfile取得とtelemetry無効で解消。途中の回帰失敗は旧v1 fixture/テストstub更新と、取消通知を待たない仕様に合わせた試験同期修正で解消。最終の上記結果を採用。
- ローカルの証跡: `/tmp/mvp-v2-regression.log`、`/tmp/mvp-v2-web-build.log`、`/tmp/mvp-v2-npm-ci.log`。これらは実音声や実機ログではない。

## Happy-Emoriへの引き継ぎ

[既存v2契約](../api/mvp-tail-control-v2.md) の4 endpoint、JSONキー、型、上限、error codeをそのまま使用する。PCが要求するstate/ackと正常遷移は共通fixtureで照合できる。open/tap保留、stop先行、重複、再起動、次session、Reader/旧angle/konkon/modeからの迂回をファーム側でも再現してほしい。PCの試験は機器側排他処理を実証していない。

実機はまだv2対応済みと扱わない。capability・exclusive・校正・start_enabledの実機確認と、操作者立会いの有限動作/途中停止/期限/再起動確認まで既定無効を維持する。stopはPWM保持であり電源遮断ではない。今回のサービスへこの修理を反映していない。

## naminamina（Web）への引き継ぎ

機器を直接呼ばず、既存 `DELETE /api/v1/voice/sessions/{session_id}` の追加 `tail_control` を使用する。音声の `state=stopped` や `audio_device_stop_confirmed=false` と別表示にする。

```json
{"tail_control":{"status":"command_stopped","device_epoch":"7ef65580e6e346de8b503259d4e1f40c","stop_revision":8,"feedback":"command_only"}}
```

`unconfirmed` は機器停止未確認、`disabled` はこの会話で実機制御を一度も許可していない場合。欠落も確認済みにしない。端末マイク/再生の即時停止、JSON/session確認、5秒上限、実停止時刻での一度だけの記録を保持する。結果保存は同じ記録IDに追加し、確認待ちで利用時間を伸ばしたり二重記録しない。再読込や未確認後の自動再開はしない。このPRではWebの表示・保存コードを変更していない。
