# ブランチ運用

共有の統合先は `main` です。2026-10-25の利用者指定により、統合後のmainからcontributors名の4人分の作業ブランチを用意します。

- 各メンバーは下表の作業ブランチを使う。個別の作業をさらに分ける場合は最新mainから `feature/<内容>` または `fix/<内容>` を作成する。
- 変更・関連テスト・仕様をまとめ、mainへ統合する。
- 統合されたことを確認してから作業ブランチを削除する。共同作業中のブランチや未統合コミットは削除しない。
- 過去の統合記録では旧先端は `archive/2026-10-25-pc-voice`（f6b716e）と `archive/2026-10-25-emori-esp32`（5d0daa4）のタグで保存した。今回のcloneでのタグ取得状況とは区別する。

## 4人の作業ブランチ（2026-10-25）

GitHubのcontributors一覧にある4アカウントのloginを、大文字・小文字も含めてそのままブランチ名にします。4本とも本案内を含む同じmainコミットから開始します。

| Contributor | 作業ブランチ |
| --- | --- |
| RyuuzinnRaika | `RyuuzinnRaika` |
| naminamina | `naminamina` |
| Happy-Emori | `Happy-Emori` |
| takemi853 | `takemi853` |

自分のPCでは、未コミット変更をコミットまたは退避してから、次のように切り替えます。最後の名前は自分のブランチへ置き換えてください。

```bash
git fetch origin
git switch --track origin/RyuuzinnRaika
```

同名のローカルブランチが既にある場合は `git switch RyuuzinnRaika` で切り替えます。変更は各自のブランチへpushし、main向けPRで統合します。最新mainの取り込みは作業ツリーをクリーンにして `git fetch origin`、`git merge origin/main` の順に行います。

ブランチ名は作業の区分であり、担当領域やアクセス権を変更しません。既存の共有ブランチは保持します。下の「公開5ブランチ」は、この4本を追加する前の統合作業の記録です。

## 今回の公開ブランチ統合結果（2026-10-25）

取得時点のGitHub上の公開ブランチは次の5本です。`origin/HEAD` は既定ブランチへの参照で、別の作業ブランチには数えません。

| 公開ブランチ | 取得した先端SHA | 今回の扱い |
| --- | --- | --- |
| `main` | `9273748e69cafd3de6c0112d8b56e96b7575c584` | 統合の基点 |
| `HOSSY＿WEB`（全角＿） | `33bbb7cfea565680344d808d6b0fe4326deed811` | 製品Webの公開入口・使い方の文書を履歴ごとmerge |
| `emori-ESP32` | `61bf53de45f1129df1b264e001da3403d7876a64` | 基点mainの祖先。既に包含され、追加のmerge不要 |
| `feature/web-voice-integration` | `9273748e69cafd3de6c0112d8b56e96b7575c584` | 基点mainと同一。追加のmerge不要 |
| `namikawa_3Dprint` | `7c3e9e91e940e471b2aff0de08ca4b2a8c760050` | [STL 8点](../../3Dprint/README.md)を履歴ごとmerge |

専用cloneの `docs/main-integration` で通常のmergeを行い、全5先端がローカル統合履歴の祖先であることを確認しました。ソース・STLの内容は変更せず、案内文書を更新しています。[統合PR #15](https://github.com/jphacks/kz_2614/pull/15) を通常mergeし、公開main `b4e3535c92151934ff4a0799077b1215c1d2be53` への反映を確認しました。PR #1の文書更新もこの履歴に含まれます。上表は作業開始時の取得先端であり、現在のmainの先端を固定するものではありません。

`feature/server-voice`（旧`kameyama`）、旧作業ブランチ、旧先端のarchiveタグは過去のPC側の記録です。今回取得した公開5ブランチと区別し、この専用cloneやリモートにすべて存在するとは扱いません。共同作業者のブランチは削除せず、force-push・履歴書換えを行いません。

製品Webの音声API接続と会話MCPからESP32実機への接続は未完了です。ブランチの包含確認は機能の実機受け入れを意味しません。

## 他のPCでの移行

未コミット作業がある場合は先にコミットするか退避してから実行してください。

```bash
git fetch origin --prune --tags
git switch main
git pull --ff-only origin main
```

mainがまだローカルにない場合は `git switch --track origin/main` を使います。単一ブランチだけを取得するcloneでは、まず `git remote set-branches origin '*'` で取得対象を広げてください。

フォルダーの変更は [構成図](../architecture.md) を参照してください。機器ごとのWi-Fi設定とモデル、音声、.envは取り直したりGitへ追加したりせず、各PCで管理します。

担当別の作業場所と連携は [ownership.md](ownership.md)、旧Programs環境の移行は [layout-migration.md](layout-migration.md) を参照してください。

## 過去のPC側でのHOSSy Web統合・整理記録（2026-10-25）

以下は当時の作業ブランチの記録です。現在の公開先端・統合状態は上表を優先してください。

- `main`：PC/ESP32の統合仕様とHOSSy製品Webを含む統合先。
- `feature/server-voice`：このPCの旧`kameyama`を改名。mainへ統合した旧先端f6b0055を保持する。未統合の変更はない。
- `feature/web-voice-integration`：整理済みmainから作る次の作業用ブランチ。このPCの稼働チェックアウトkz_2614-latestもここへ切り替える。製品Webの音声API接続は今後の作業であり、名前を作っただけでは実装完了にならない。
- `HOSSY＿WEB`（全角アンダースコア）と`emori-ESP32`：既存のリモート名と履歴を保持する。共同作業中の参照を壊さないため、無断削除・force-pushはしない。次の作業はmainから半角の`feature/web-...`、`feature/esp32-...`へ切る。

個別機能の命名は`feature/<領域>-<内容>`、`fix/<領域>-<内容>`、`docs/<内容>`とし、英小文字・数字・半角ハイフンを使います。上表の4人分の作業ブランチは利用者指定によりcontributors名をそのまま使います。ローカルmain更新とGitHubへのpushは区別し、公開の有無を作業報告に記録する。
