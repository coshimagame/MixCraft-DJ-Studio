# Build guide

Windows x64 / Python 3.14。PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest==9.2.0 pyinstaller==6.22.3
.venv\Scripts\python -m pytest -q
.venv\Scripts\python make_demo.py demo
.venv\Scripts\python app.py
.venv\Scripts\python build.py
.venv\Scripts\python collect_licenses.py dist/MixCraftDJStudio/third-party-licenses
Copy-Item demo,README.md,LICENSES.md,BUILD_GUIDE.md,ABLETON_COMPATIBILITY.md -Destination dist/MixCraftDJStudio -Recurse
```

dist/MixCraftDJStudioフォルダー全体を配布する。exe単体では動かない。
build.pyはPATHをPythonとWindows標準ディレクトリに限定し、別アプリの同名DLL混入を防ぐ。
`MixCraftDJStudio.exe --smoke-test smoke` でデバイス不要の起動・ミックス検証。
`MixCraftDJStudio.exe --audio-smoke-test smoke-audio` で実出力を約4秒検証する。
後者は合成音が鳴る。結果JSON・画面PNG・イベントJSONを指定フォルダーに保存する。
署名・インストーラーは未実装。CIはPRとmain pushでテスト後にビルドしArtifactを保存。
Windows 10/Ubuntuの実機検証は未実施。Ubuntuでのビルドは別パッケージが必要。
CIで依存バージョンが取得できない場合は自動で最新版へ変更せず、互換性を再検証すること。
PyInstallerの動的DLL依存・ライセンス収集結果を配布前に確認すること。
