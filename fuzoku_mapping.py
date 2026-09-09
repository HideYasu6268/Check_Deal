import re

CODE_HEADER_PATTERN = re.compile(r"補助コード\s*(\S+)")


def parse_fuzoku_list(text):
    """補助一覧のタブ区切りテキストから {補助コード: 会社名} の辞書を作る。

    想定フォーマット（1行1件、タブ区切り）:
        会社名\tコード\t繰越残高\t借方金額\t貸方金額\t当月残高
    会社名・コード以外の列は無視する。コード列が数字でない行（見出し等）はスキップする。
    """
    mapping = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        code = parts[1].strip()
        if not name or not code:
            continue
        if not code.isdigit():
            continue
        mapping[code] = name
    return mapping


def format_mapping(mapping):
    """{補助コード: 会社名} を貼り付け欄と同じ 会社名\\tコード 形式のテキストに戻す。"""
    return "\n".join(f"{name}\t{code}" for code, name in sorted(mapping.items()))


def apply_company_names(explanation_text, mapping):
    """Geminiの説明文中の "補助コード XXXX" を "補助コード XXXX（会社名）" に置き換える。
    対応する会社名が見つからないコードはそのまま残す。
    """
    def repl(m):
        code = m.group(1)
        name = mapping.get(code)
        if name:
            return f"補助コード {code}（{name}）"
        return m.group(0)

    return CODE_HEADER_PATTERN.sub(repl, explanation_text)
