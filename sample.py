import pandas as pd
import numpy as np

AR_CODE = 1142  # 売掛金の科目コード

def build_ar_entries_from_transactions(
    df_tx: pd.DataFrame,
    ar_code: int = AR_CODE,
    date_col: str = "日付",
    amount_col: str = "金額",
    debit_acct_col: str = "コード",
    debit_customer_col: str = "コード.1",   # 1142が借方にいるときの「2つ隣」
    credit_acct_col: str = "コード.3",
    credit_customer_col: str = "コード.4",  # 1142が貸方にいるときの「2つ隣」
) -> pd.DataFrame:
    """
    取引一覧から売掛金(1142)の増減明細を作る
      - 1142が借方: +金額（売掛増＝請求）
      - 1142が貸方: -金額（売掛減＝入金など）
    """
    df = df_tx.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce")

    mask_debit = df[debit_acct_col] == ar_code
    mask_credit = df[credit_acct_col] == ar_code

    ar = df.loc[mask_debit | mask_credit].copy()

    ar["customer_code"] = np.where(
        mask_debit.loc[ar.index],
        ar[debit_customer_col],
        ar[credit_customer_col],
    )

    ar["ar_delta"] = np.where(mask_debit.loc[ar.index], ar[amount_col], -ar[amount_col])
    ar["src"] = "tx"
    ar["src_index"] = ar.index

    keep = ["src", "src_index", "customer_code", date_col, amount_col, "ar_delta"]
    return ar[keep].dropna(subset=["customer_code", date_col, amount_col, "ar_delta"])


def build_opening_entries(
    df_open: pd.DataFrame,
    opening_date: pd.Timestamp,
    customer_col: str = "コード",     # ← ユーザー要件：これが取引先コード
    opening_amount_col: str = "繰越残高",  # 必要なら "当月残高" に差し替え
) -> pd.DataFrame:
    """
    開始残高（繰越残高）を売掛増(+ar_delta)として追加する
    """
    df = df_open.copy()
    df[customer_col] = pd.to_numeric(df[customer_col], errors="coerce")
    df[opening_amount_col] = pd.to_numeric(df[opening_amount_col], errors="coerce").fillna(0)

    op = df[[customer_col, opening_amount_col]].copy()
    op = op[op[opening_amount_col] != 0].copy()

    op.rename(columns={customer_col: "customer_code"}, inplace=True)
    op["日付"] = opening_date
    op["金額"] = op[opening_amount_col].abs()

    # 繰越残高は通常「売掛残（借方残）」想定 → +で積む
    # もしマイナスがあり得て意味が「前受」等なら、この扱いは要調整
    op["ar_delta"] = op[opening_amount_col]
    op["src"] = "opening"
    op["src_index"] = -1  # 擬似行
    return op[["src", "src_index", "customer_code", "日付", "金額", "ar_delta"]]


def reconcile_one_to_one_same_amount(
    ar_entries: pd.DataFrame,
    date_col: str = "日付",
    amount_col: str = "金額",
    amount_tolerance: int | float = 0,
) -> dict[str, pd.DataFrame]:
    """
    1対1、同額（±許容差）だけ消込。FIFO（古い売掛増から当てる）
    """
    df = ar_entries.copy()
    df["customer_code"] = pd.to_numeric(df["customer_code"], errors="coerce")
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce")

    df = df.dropna(subset=["customer_code", date_col, amount_col, "ar_delta"]).copy()
    df["abs_amount"] = df[amount_col].abs()

    inc = df[df["ar_delta"] > 0].sort_values(["customer_code", date_col, "src", "src_index"]).copy()
    dec = df[df["ar_delta"] < 0].sort_values(["customer_code", date_col, "src", "src_index"]).copy()

    matched_rows = []
    used_inc = set()
    used_dec = set()

    for cust, dec_c in dec.groupby("customer_code", sort=False):
        inc_pool = inc[(inc["customer_code"] == cust) & (~inc["src_index"].isin(used_inc))].copy()

        for _, pay in dec_c.iterrows():
            key_pay = (pay["src"], int(pay["src_index"]) if pay["src_index"] != -1 else -1)
            if key_pay in used_dec:
                continue

            target = pay["abs_amount"]
            candidates = inc_pool[
                (inc_pool["abs_amount"] >= target - amount_tolerance) &
                (inc_pool["abs_amount"] <= target + amount_tolerance)
            ]
            if candidates.empty:
                continue

            inv = candidates.iloc[0]

            key_inv = (inv["src"], int(inv["src_index"]) if inv["src_index"] != -1 else -1)
            used_inc.add(inv["src_index"])
            used_dec.add(key_pay)

            matched_rows.append({
                "customer_code": cust,
                "invoice_src": inv["src"],
                "invoice_src_index": int(inv["src_index"]),
                "payment_src": pay["src"],
                "payment_src_index": int(pay["src_index"]),
                "invoice_date": inv[date_col],
                "payment_date": pay[date_col],
                "amount": float(inv["abs_amount"]),
                "days_diff": (pay[date_col] - inv[date_col]).days,
            })

            inc_pool = inc_pool[inc_pool["src_index"] != inv["src_index"]]

    matched = pd.DataFrame(matched_rows)

    # 残り（未消込）
    open_invoices = inc[~inc["src_index"].isin(used_inc)].copy()
    # dec側はsrc_index=-1があり得ない（openingは+のみ想定）ので keyで管理せず単純に残り抽出
    matched_pay_idx = set(matched["payment_src_index"].tolist()) if not matched.empty else set()
    unapplied_payments = dec[~dec["src_index"].isin(matched_pay_idx)].copy()

    return {
        "matched": matched.sort_values(["customer_code", "invoice_date", "payment_date"]) if not matched.empty else matched,
        "open_invoices": open_invoices.sort_values(["customer_code", date_col, "src", "src_index"]),
        "unapplied_payments": unapplied_payments.sort_values(["customer_code", date_col, "src", "src_index"]),
    }


# ===== 使い方（例） =====
# tx = pd.read_csv("取引一覧.csv", encoding="cp932")
# op = pd.read_csv("開始残高.csv", encoding="cp932")
#
# ar_tx = build_ar_entries_from_transactions(tx)
# opening_date = ar_tx["日付"].min()  # 取引一覧の最初の日付に寄せる（任意で固定日でもOK）
# ar_open = build_opening_entries(op, opening_date=opening_date, opening_amount_col="繰越残高")
#
# ar_all = pd.concat([ar_open, ar_tx], ignore_index=True)
# res = reconcile_one_to_one_same_amount(ar_all, amount_tolerance=0)
#
# res["open_invoices"]      # 未入金（開始残高も含む）
# res["unapplied_payments"] # 消込できない入金
# res["matched"]            # 1対1で消込できたペア
