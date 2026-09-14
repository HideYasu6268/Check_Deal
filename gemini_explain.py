import sys
import time
import pandas as pd
import requests

import paths

ANOMALY_CSV = paths.path("異常値.csv")
PROMPT_BATCH_FILENAMES = {
    "receivable": "gemini_prompt_batch.txt",
    "payable": "gemini_prompt_batch_payable.txt",
}
API_KEY_FILENAME = "gemini_api_key.txt"
MODEL = "gemini-flash-latest"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def load_api_keys():
    """gemini_api_key.txt から1行1キーでAPIキーの一覧を読み込む。
    複数書いておくと、無料枠を使い切ったキーは自動でスキップして次のキーに切り替わる。
    exeと同じフォルダに同名ファイルを置けば、exe埋め込みの既定キーより優先される。
    """
    api_key_file = paths.overridable_resource_path(API_KEY_FILENAME)
    keys = []
    with open(api_key_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                keys.append(line)
    if not keys:
        raise RuntimeError(f"{api_key_file} にAPIキーが見つかりません")
    return keys


def load_template(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_batch_prompt(df, mode="receivable", for_api=False):
    """for_api=True(API処理用)の場合のみ、1行あたりの文字数を指定する指示を追加する。
    チャットへのコピペ用（デフォルト）はチャット側が自然に折り返すため不要。
    """
    table_cols = ["日付", "補助コード", "相手科目コード/科目", "金額", "残高"]
    table_str = df[table_cols].to_string(index=False)
    prompt_file = paths.overridable_resource_path(PROMPT_BATCH_FILENAMES[mode])
    template = load_template(prompt_file)
    extra_instruction = "\n- 80文字前後の意味の区切りの良い箇所で改行を入れる事" if for_api else ""
    return template.format(table=table_str, extra_instruction=extra_instruction)


def call_gemini(api_keys, prompt, max_retries_per_key=3, on_progress=None):
    """複数のAPIキーを順番に試す。
    429(無料枠切れ)はそのキーでは回復を待っても無駄なのですぐ次のキーに切り替える。
    503(サーバー混雑)はしばらく待って同じキーでリトライし、それでもダメなら次のキーに切り替える。
    """
    if isinstance(api_keys, str):
        api_keys = [api_keys]

    last_resp = None
    for key_index, api_key in enumerate(api_keys):
        for attempt in range(max_retries_per_key):
            resp = requests.post(
                ENDPOINT,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            if resp.status_code == 429:
                if on_progress:
                    on_progress(f"キー{key_index + 1}が429(無料枠切れ)のため次のキーに切り替えます")
                last_resp = resp
                break
            if resp.status_code == 503 and attempt < max_retries_per_key - 1:
                wait = min(60, 5 * (2 ** attempt))
                if on_progress:
                    on_progress(f"キー{key_index + 1}: 503のため{wait}秒待って再試行します")
                time.sleep(wait)
                last_resp = resp
                continue
            if resp.status_code == 503:
                if on_progress:
                    on_progress(f"キー{key_index + 1}は503が続くため次のキーに切り替えます")
                last_resp = resp
                break
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    last_resp.raise_for_status()


def explain_anomalies(anomaly_df, api_keys=None, mode="receivable", on_progress=None):
    """異常値DataFrameをGeminiに渡し、説明テキストを返す。
    全補助コードを1回のプロンプトにまとめる一括方式のみ。失敗した場合は例外を投げる
    （呼び出し側で手動タブへの切り替えを促す）。
    mode: "receivable"(売掛金) または "payable"(買掛金) でプロンプトの文言を切り替える。
    """
    if anomaly_df.empty:
        return ""

    if api_keys is None:
        api_keys = load_api_keys()

    prompt = build_batch_prompt(anomaly_df, mode=mode, for_api=True)
    return call_gemini(api_keys, prompt, on_progress=on_progress)


def main():
    df = pd.read_csv(ANOMALY_CSV, dtype={"補助コード": str})

    if df.empty:
        print("異常値.csv は空です。Geminiへの問い合わせは行いません。")
        return

    explanation = explain_anomalies(df, on_progress=print)
    print(explanation)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
