# Ubuntu CT での起動

Ubuntu CT に Docker Engine と Docker Compose プラグインが入っており、Docker デーモンが動作することを前提とします。`compose.yaml` はリポジトリのルートにあります。

```bash
git clone https://onedev.hiroron.net/JPHACKS2026_app
cd JPHACKS2026_app
docker compose up -d
```

CT の IP アドレスの `http://<CTのIPアドレス>/` で開けます。標準ではホストの TCP 80 番を使用します。すでに 80 番を使用中、または別のリバースプロキシを経由する場合は、リポジトリのルートに Git 管理対象外の `.env` を作成し、例えば `HTTP_PORT=8080` と書いてから起動してください。この場合の直接アクセスは `http://<CTのIPアドレス>:8080/` です。

更新時は同じディレクトリで実行します。公開ドメインは `https://hiroron-jphacks2026.hiroron.net/` です。`pull_policy: build` により、`up -d` 時に新しいソースからイメージをビルドします。

```bash
git pull --ff-only
docker compose up -d --build
```

確認用コマンド:

```bash
docker compose ps
docker compose logs --tail=50 web
docker compose exec -T web sh -c 'wget -q -O /dev/null http://127.0.0.1/ && echo "HTTP OK"'
docker compose exec -T web sh -c 'test -f /usr/share/nginx/html/manifest.json && test -f /usr/share/nginx/html/sw.js && test -f /usr/share/nginx/html/icons/nengitsune-192.png && echo "PWA files OK"'
```

`HTTP_PORT` を変更した場合は CT の IP アドレスへ直接アクセスする際のポートも合わせてください。この構成は HTTP 配信です。公開ドメインの HTTPS 化には、別途リバースプロキシや証明書の設定が必要です。

ホーム画面への PWA インストールとオフライン起動を公開端末で使うには、公開ドメインを **HTTPS** で配信してください。CT の IP アドレスに直接 `http://` でアクセスした場合はインストールできません。HTTPS のリバースプロキシを経由する構成なら、`manifest.json`、`sw.js`、`/icons/` も同じドメインから配信されるようにしてください。

更新後は公開ドメインの `/manifest.json`、`/sw.js`、`/icons/nengitsune-192.png` がそれぞれ 200 を返すことを確認してください。認証の待機ページがある場合は、ブラウザで通過してから確認します。

アプリは現時点では静的な UI プロトタイプです。タイマーの操作記録は利用者のブラウザのローカルストレージに保存されます。Docker のボリュームやサーバー側のデータベースは使いません。
