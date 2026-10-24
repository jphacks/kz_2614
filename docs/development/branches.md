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
