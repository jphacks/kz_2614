# ブランチ運用

共有の統合先は `main` です。PC音声側とEMORI ESP32側の履歴をmergeで保持し、作業場所を分けるためだけの長期ブランチは終了します。

- 新規作業は最新mainから `feature/<内容>` または `fix/<内容>` を作成する。
- 変更・関連テスト・仕様をまとめ、mainへ統合する。
- 統合されたことを確認してから作業ブランチを削除する。共同作業中のブランチや未統合コミットは削除しない。
- 今回の旧先端は `archive/2026-10-25-pc-voice`（f6b716e）と `archive/2026-10-25-emori-esp32`（5d0daa4）のタグで参照できる。どちらもmainの履歴に含める。

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

## HOSSy Web統合後の整理（2026-10-25）

- `main`：PC/ESP32の統合仕様とHOSSy製品Webを含む統合先。
- `feature/server-voice`：このPCの旧`kameyama`を改名。mainへ統合した旧先端f6b0055を保持する。未統合の変更はない。
- `feature/web-voice-integration`：整理済みmainから作る次の作業用ブランチ。このPCの稼働チェックアウトkz_2614-latestもここへ切り替える。製品Webの音声API接続は今後の作業であり、名前を作っただけでは実装完了にならない。
- `HOSSY＿WEB`（全角アンダースコア）と`emori-ESP32`：既存のリモート名と履歴を保持する。共同作業中の参照を壊さないため、無断削除・force-pushはしない。次の作業はmainから半角の`feature/web-...`、`feature/esp32-...`へ切る。

命名は`feature/<領域>-<内容>`、`fix/<領域>-<内容>`、`docs/<内容>`。英小文字・数字・半角ハイフンを使い、全角記号や担当者名だけの名前を避ける。ローカルmain更新とGitHubへのpushは区別し、公開の有無を作業報告に記録する。
