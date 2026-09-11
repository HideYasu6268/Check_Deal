import sys
import re
import pdfplumber
import pandas as pd

import paths

DEFAULT_PDF_PATH = paths.path("総勘定元帳.pdf")


def to_int(s):
    if s is None:
        return pd.NA
    s = s.replace(",", "").replace(" ", "").strip()
    if s == "":
        return pd.NA
    return int(s)


def first_line(s):
    if not s:
        return ""
    return s.split("\n")[0].strip().replace(" ", "")


def extract_dataframe(pdf_path):
    """総勘定元帳(補助元帳)PDFを読み込み、明細のDataFrameと
    補助コード→会社名(補助科目名)のマッピングを返す。
    """
    records = []
    sub_code = None
    sub_name = None
    fiscal_start_year = None
    company_mapping = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            # ヘッダーから補助コード・補助名を取得（会社名に空白が入る場合があるため "PAGE" の手前までを名前とみなす）
            m = re.search(r"\d+:\S+\s+(\d+):(.+?)\s*PAGE", text)
            if m:
                sub_code, sub_name = m.group(1), m.group(2).strip()
                company_mapping[sub_code] = sub_name

            tables = page.find_tables()
            for t in tables:
                rows = t.extract()
                for row in rows[2:]:  # 先頭2行はヘッダー
                    date_raw, code_col, name_col, _tax1, desc, _tax2, dr, cr, bal = row

                    # 日付欄には「仕訳番号」等が改行付きで続くフォーマットがあるため、1行目だけを日付とみなす
                    date_raw = (date_raw or "").split("\n")[0].strip()
                    desc_first = (desc or "").split("\n")[0].strip()
                    desc_first_nospace = desc_first.replace(" ", "").replace("　", "")

                    # 集計行・総計行はスキップ
                    if desc_first_nospace in ("計", "総計"):
                        continue
                    if date_raw == "" and desc_first_nospace == "":
                        continue

                    if "/" not in date_raw:
                        continue

                    month, day = (int(x) for x in date_raw.split("/"))

                    if desc_first_nospace == "前期繰越":
                        # 前期繰越が出たページの年見出しをそのまま開始年(会計年度)とする
                        year_header = int(re.search(r"(\d{4})年", text).group(1))
                        fiscal_start_year = year_header

                    if desc_first_nospace == "繰越残高":
                        # ページまたぎの重複表示（前ページ末残高の繰り返し）なので行としては採用しない
                        continue

                    year = fiscal_start_year if month >= 10 else fiscal_start_year + 1

                    aite_code = first_line(code_col)
                    aite_name = first_line(name_col)
                    if desc_first_nospace == "前期繰越":
                        aite = "前期繰越"
                    elif aite_code and aite_name:
                        aite = f"{aite_code}:{aite_name}"
                    else:
                        aite = pd.NA

                    bal_val = to_int(bal)

                    if desc_first_nospace == "前期繰越":
                        # 前期繰越は当期の入出金がないため借方/貸方欄が空。
                        # 残高（符号そのまま）を金額として扱う＝前期からの未収額を売上と同様に計上する。
                        amount = bal_val
                    else:
                        dr_val = to_int(dr)
                        cr_val = to_int(cr)
                        if pd.isna(dr_val) and pd.isna(cr_val):
                            amount = pd.NA
                        else:
                            amount = (dr_val if not pd.isna(dr_val) else 0) - (cr_val if not pd.isna(cr_val) else 0)

                    records.append({
                        "日付": pd.Timestamp(f"{year:04d}-{month:02d}-{day:02d}"),
                        "補助コード": sub_code,
                        "相手科目コード/科目": aite,
                        "金額": amount,
                        "残高": bal_val,
                    })

    df = pd.DataFrame(records)
    df["金額"] = df["金額"].astype("Int64")
    df["残高"] = df["残高"].astype("Int64")
    return df, company_mapping


def judge_group(g):
    """補助コードごとの最終レコードを見て正常/異常を判定する。

    正常とみなす条件:
      1. 最終レコードの残高が0
      2. 残高が、直近のプラスの売上額(1件)と一致
      3. 残高が、直近のプラスの売上額を2件合計した額と一致
    （3ヵ月以上の未収・未払は異常とみなすため、2件合計までしか許容しない）
    上記いずれにも当てはまらない場合は異常とする。
    """
    g = g.sort_values("日付").reset_index(drop=True)
    last = g.iloc[-1]
    residual = last["残高"]

    if pd.isna(residual):
        return True, None  # 判定不能はスキップ（異常扱いしない）

    if residual == 0:
        return True, None

    pos_amounts = [v for v in g["金額"].tolist() if pd.notna(v) and v > 0]

    running = 0
    candidates = []
    for v in reversed(pos_amounts[-2:]):
        running += v
        candidates.append(running)

    if residual in candidates:
        return True, None

    return False, {
        "残高": residual,
        "直近プラス売上1件": candidates[0] if len(candidates) >= 1 else None,
        "直近プラス売上2件合計": candidates[1] if len(candidates) >= 2 else None,
    }


def judge_anomalies(df):
    """補助コードごとに正常/異常を判定し、異常な補助コードの明細だけをまとめたDataFrameを返す。"""
    anomaly_rows = []
    details = {}
    for code, g in df.groupby("補助コード", sort=False):
        is_normal, detail = judge_group(g)
        if not is_normal:
            anomaly_rows.append(g.sort_values("日付"))
            details[code] = detail

    if anomaly_rows:
        anomaly_df = pd.concat(anomaly_rows, ignore_index=True)
    else:
        anomaly_df = df.iloc[0:0].copy()

    return anomaly_df, details


def run(pdf_path=DEFAULT_PDF_PATH):
    df, company_mapping = extract_dataframe(pdf_path)
    df.to_csv(paths.path("総勘定元帳_抽出.csv"), index=False, encoding="utf-8-sig")

    anomaly_df, details = judge_anomalies(df)

    return df, anomaly_df, details, company_mapping


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF_PATH
    df, anomaly_df, details, company_mapping = run(pdf_path)

    pd.set_option("display.unicode.east_asian_width", True)
    print(df.to_string(index=False))
    print("\n行数:", len(df))

    for code, detail in details.items():
        print(f"補助コード {code} 異常: {detail}")

    print("\n異常な補助コードの件数:", len(details))
    if len(details):
        print(anomaly_df.to_string(index=False))
