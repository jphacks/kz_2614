# HTTP API・設定管理 — kameyama

`server.py` が [apps/web](../../apps/web/README.md) を配信し、音声セッション・設定・参照音声・稼働状態のHTTP窓口を提供します。音声処理は [server/ai](../ai/README.md) に委譲します。

```bash
# リポジトリルートから。server/.venvをセットアップ済みの場合
bash server/start_server_ubuntu.sh
```

このPCのLM Studio確認・CUDA準備込みの起動はルートの `./service-local.sh start` を使います。Windowsを含むセットアップは [利用手順](../../docs/setup/server.md)。通信契約は [docs/api](../../docs/api/README.md)。

保存先は既定で `server/api/data`（Git対象外）。`NENGITSUNE_SETTINGS_PATH` と `NENGITSUNE_TAIL_SETTINGS_PATH` による上書きは既存仕様に従います。参照音声と相づちは設定ファイルと同じ保存領域に置かれます。各会話は開始時のVAD設定を保持し、設定保存で進行中の入力を止めません。
