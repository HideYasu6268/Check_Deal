import sys
import time
import pandas as pd
import requests

import paths

API_KEY_FILE = paths.path("gemini_api_key.txt")
ANOMALY_CSV = paths.path("異常値.csv")
PROMPT_BATCH_FILE = paths.path("gemini_prompt_batch.txt")
PROMPT_SINGLE_FILE = paths.path("gemini_prompt_single.txt")
MODEL = "gemini-flash-latest"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def load_api_keys():
    """gemini_api_key.txt から1行1キーでAPIキーの一覧を読み込む。
    複数書いておくと、無料枠を使い切ったキーは自動でスキップして次のキーに切り替わる。
    """
    keys = []
    with open(API_KEY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                keys.append(line)
    if not keys:
        raise RuntimeError(f"{API_KEY_FILE} にAPIキーが見つかりません")
    return keys


def load_template(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_prompt(code, g):
    table_cols = ["日付", "相手科目コード/科目", "金額", "残高"]
    table_str = g[table_cols].to_string(index=False)
    template = load_template(PROMPT_SINGLE_FILE)
    return template.format(code=code, table=table_str)


def build_batch_prompt(df):
    table_cols = ["日付", "補助コード", "相手科目コード/科目", "金額", "残高"]
    table_str = df[table_cols].to_string(index=False)
    template = load_template(PROMPT_BATCH_FILE)
    return template.format(table=table_str)


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


def explain_per_code(api_keys, df, on_progress=None):
    """補助コードごとに個別にAPIを呼び出すフォールバック方式。
    一括方式と見た目を揃えるため "## 補助コード XXXX" 形式のテキストにまとめて返す。
    """
    sections = []
    for code, g in df.groupby("補助コード", sort=False):
        prompt = build_prompt(code, g)
        explanation = call_gemini(api_keys, prompt, on_progress=on_progress)
        sections.append(f"## 補助コード {code}\n{explanation}")
        if on_progress:
            on_progress(f"補助コード {code} 完了")

    return "\n\n".join(sections)


def explain_anomalies(anomaly_df, api_keys=None, on_progress=None):
    """異常値DataFrameをGeminiに渡し、説明テキストを返す。
    まず全補助コードを1回のプロンプトにまとめる一括方式を試し、
    失敗した場合は補助コードごとの個別方式にフォールバックする。
    """
    if anomaly_df.empty:
        return ""

    if api_keys is None:
        api_keys = load_api_keys()

    try:
        prompt = build_batch_prompt(anomaly_df)
        return call_gemini(api_keys, prompt, on_progress=on_progress)
    except Exception as e:
        if on_progress:
            on_progress(f"一括方式で失敗しました: {e}")
            on_progress("補助コードごとの個別方式にフォールバックします。")
        return explain_per_code(api_keys, anomaly_df, on_progress=on_progress)


def main():
    df = pd.read_csv(ANOMALY_CSV, dtype={"補助コード": str})

    if df.empty:
        print("異常値.csv は空です。Geminiへの問い合わせは行いません。")
        return

    explanation = explain_anomalies(df, on_progress=print)
    print(explanation)
    with open(paths.path("異常値_説明.txt"), "w", encoding="utf-8") as f:
        f.write(explanation)
    print("\n異常値_説明.txt に保存しました。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
