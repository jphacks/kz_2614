# 外部プログラムと環境セットアップ

外部プロジェクトのソースをこのフォルダー配下へ置きます。自作コードは `server/ai/` と `server/api/` に保ち、外部プロジェクトのREADME・ライセンス・バージョン情報も一緒に保管してください。

`Irodori-TTS/` は配置予定の空フォルダーです。まだソースコードは取得していません。

## ServerSideのセットアップ

次の [Windows用スクリプト](setup_windows.ps1) と [Ubuntu用スクリプト](setup_ubuntu.sh) は、GitとPython 3.11〜3.13を用意し、`server/.venv/` に仮想環境を作成して、`server/requirements.txt` を `pip install` します。既に要件を満たすGitやPythonがある場合は再インストールせずに使います。

リポジトリのルートから実行してください。

### Windows

GitまたはPythonがない場合は `winget` でインストールします。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\server\ai\third_party\setup_windows.ps1
```

### Ubuntu

`apt` でGitとPythonをインストールします。設定済みのAPTリポジトリにPython 3.11〜3.13がない場合は、対象Pythonの導入方法を表示して終了します。

```bash
bash server/ai/third_party/setup_ubuntu.sh
```

セットアップ後に仮想環境を有効化する場合:

```powershell
& .\server\.venv\Scripts\Activate.ps1
```

```bash
source server/.venv/bin/activate
```

これらのスクリプトはPython依存関係を入れるもので、外部リポジトリやWhisper・TTSモデルの取得、GPUドライバー・CUDA・cuDNNの導入は行いません。
