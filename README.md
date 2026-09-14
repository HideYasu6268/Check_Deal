# 補助元帳 異常値チェック

売掛金・買掛金の総勘定元帳（補助元帳）PDFを解析し、残高が「正常なパターン」から外れている取引先（補助コード）を自動検出。Gemini APIで異常の原因を日本語で説明させ、補助コードを会社名に置き換えて表示するデスクトップツール（customtkinter製GUI）。

非エンジニアの方は `非エンジニア向けの使い方.txt` を参照してください。このREADMEは開発・ビルド向けです。

## 処理の流れ

1. **PDF解析** (`extract_ledger.py`)
   - `pdfplumber` で総勘定元帳PDFの表を抽出し、`日付 / 補助コード / 相手科目コード・科目 / 金額 / 残高` のDataFrameを作る
   - ヘッダーから補助コード→会社名（補助科目名）のマッピングも取得
   - 抽出結果は毎回 `総勘定元帳_抽出.csv`（exeと同じフォルダ）に書き出す
2. **異常判定** (`extract_ledger.judge_group`)
   - 補助コードごとに最終レコードの残高を見る
   - 残高が「0」「直近のプラス売上1件と一致」「直近のプラス売上2件の合計と一致」のいずれでもなければ異常とみなす（3ヶ月以上の未収・未払を検出する想定）
3. **原因説明** (`gemini_explain.py`)
   - 異常な補助コードの明細だけをまとめて1回のプロンプトでGemini API（`gemini-flash-latest`）に問い合わせ
   - `gemini_api_key.txt` に複数キーを1行1キーで書いておくと、429（無料枠切れ）で自動的に次のキーへ切り替える。503（混雑）は指数バックオフでリトライ
4. **会社名置換** (`fuzoku_mapping.py`)
   - Geminiの回答中の `補助コード XXXX` を `補助コード XXXX（会社名）` に置換して表示

## GUIの2モード

- **自動（API）タブ**: PDF解析→Gemini問い合わせ→会社名置換まで1ボタンで実行。API失敗時は自動で手動タブにプロンプトを引き継ぐ
- **手動（コピペ）タブ**: APIキーが無い・無料枠切れ・データ量が多い場合の代替。①プロンプト生成→②ChatGPT/Claude/Geminiのチャット画面に貼り付け→回答を③に貼り付けて会社名置換

「買掛金(負債)処理」チェックボックスで、Geminiに渡すプロンプトの文言（売掛金/買掛金）と参照するテンプレートファイルを切り替える（異常判定ロジック自体は共通）。

「設定(APIキー/プロンプト)」ボタンから、APIキーとプロンプトテンプレート（売掛金用・買掛金用）をGUI上で直接編集・保存できる。

## セットアップ（開発）

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

以下のファイルをプロジェクト直下に用意する（`.gitignore` 済み、リポジトリには含まれない）:

- `gemini_api_key.txt` — Gemini APIキーを1行1キーで記載
- 解析対象PDF（デフォルトは `総勘定元帳.pdf`。GUIの「参照」ボタンで別ファイルも選択可）

`gemini_prompt_batch.txt`（売掛金用）・`gemini_prompt_batch_payable.txt`（買掛金用）はプロンプトのテンプレート。`{table}` と `{extra_instruction}` のプレースホルダを含む。

## 実行

```
python app.py
```

## exe化（PyInstaller onefile）

`paths.py` の仕組み上、以下がポイント:

- `frozen`（exe実行）時、`resource_path()` は `sys._MEIPASS`（onefileの一時展開フォルダ）を参照する。exeに同梱する読み取り専用の既定ファイル（APIキー・プロンプトの初期値）は `--add-data` で埋め込む
- `overridable_resource_path()` は exeと同じフォルダに同名ファイルがあればそちらを優先する。GUIの「設定」ダイアログで保存すると exeと同じフォルダに書き出されるため、再ビルドせずに更新できる
- 出力CSV（`総勘定元帳_抽出.csv`）は常に `paths.path()`＝exeと同じフォルダに書き出す

ビルドコマンド例:

```
pyinstaller --onefile --windowed --name "補助元帳チェック" ^
  --add-data "gemini_prompt_batch.txt;." ^
  --add-data "gemini_prompt_batch_payable.txt;." ^
  --add-data "gemini_api_key.txt;." ^
  app.py
```

- `--windowed` はGUIアプリなのでコンソールウィンドウを非表示にする
- `gemini_api_key.txt` は配布用の既定キー（無ければ空でも可。利用者が「設定」ダイアログから自分のキーを追加できる）
- 生成物は `dist\補助元帳チェック.exe`（`build/`・`dist/`・`*.spec` は `.gitignore` 済み）

## ファイル構成

| ファイル | 役割 |
|---|---|
| `app.py` | GUI本体（customtkinter） |
| `extract_ledger.py` | PDF抽出・異常判定ロジック |
| `gemini_explain.py` | Gemini API呼び出し・プロンプト組み立て |
| `fuzoku_mapping.py` | 補助コード↔会社名の変換 |
| `paths.py` | exe化を考慮したファイルパス解決 |
| `gemini_prompt_batch.txt` / `gemini_prompt_batch_payable.txt` | プロンプトテンプレート |
| `gemini_api_key.txt` | APIキー（gitignore対象） |
