# 開発への参加

[フォルダー構成](docs/repository-layout.md) と [担当範囲](docs/development/ownership.md) を確認し、[contributors名の作業ブランチ](docs/development/branches.md) を使います。個別機能をさらに分ける場合は次の手順で最新mainからブランチを作成します。

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

## 製品Webの開発

`apps/hossy-web`直下でNode.js 22.22.0以上を使い、`npm ci`・`npm run build`を実行します。旧`astro-site/`は廃止しました。画面・保存・PWAの確認は[製品Web仕様](docs/requirements/web-product.md)、起動は[セットアップ](docs/setup/hossy-web.md)を参照してください。

contributors名の4人分の作業ブランチはその名前を維持します。個別機能の新規ブランチは半角小文字・ハイフンを使い、`feature/web-voice-integration`のように作業内容を表します。詳しくは[ブランチ運用](docs/development/branches.md)。
