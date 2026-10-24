# 開発への参加

[フォルダー構成](docs/repository-layout.md) と [担当範囲](docs/development/ownership.md) を確認してから、最新mainから作業ブランチを作成します。

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git switch -c feature/your-change
```

## セットアップとテスト

[サーバー構築手順](docs/setup/server.md) に従って `server/.venv` を用意し、Web試験用にNode.jsをPATHへ追加してください。モデルをロードしない自動テストは、ルートから次で実行します。

```bash
server/.venv/bin/python -m unittest discover -s tests -v
```

Windowsは `server\.venv\Scripts\python.exe -m unittest discover -s tests -v`。Node.jsがPATHにない場合は `NODE` 環境変数で実行ファイルを指定できます。スキップがある場合は理由を記録し、実行済みとは扱わないでください。

Androidのルートは `apps/android`、ESP32の手順は [firmware/esp32](firmware/esp32/README.md)。PCのテスト成功だけではAndroid/ESP32実機の動作確認になりません。

## 統合前に残す情報

変更の目的・担当領域・確認結果・残る制限をPRまたはコミットに記載します。APIを変更したらdocs/apiを、構成を変更したらdocs/repository-layout.mdを更新し、作業結果をdocs/development/work-history.mdへ追記します。

モデル重み、参照音声、録音、生成音声、.env、Wi-Fiパスワード、機器固有設定、ビルド成果物はコミットしません。追加素材の再配布条件は [第三者素材一覧](THIRD_PARTY_NOTICES.md) に記録します。
