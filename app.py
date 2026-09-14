import threading
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

import extract_ledger
import gemini_explain
import fuzoku_mapping
import paths

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("補助元帳 異常値チェック")
        self.geometry("1000x780")

        # 手動タブで使う、直近のPDF解析結果（異常値DataFrameと会社名マッピング）
        self.anomaly_df = None
        self.mapping = None

        # --- PDF指定（全タブ共通） ---
        pdf_frame = ctk.CTkFrame(self)
        pdf_frame.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(pdf_frame, text="総勘定元帳PDF:").pack(side="left", padx=(8, 4))
        self.pdf_path_var = tk.StringVar(value=extract_ledger.DEFAULT_PDF_PATH)
        self.pdf_entry = ctk.CTkEntry(pdf_frame, textvariable=self.pdf_path_var)
        self.pdf_entry.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(pdf_frame, text="参照", width=70, command=self.browse_pdf).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(pdf_frame, text="ページ範囲:").pack(side="left", padx=(8, 4))
        self.page_range_var = tk.StringVar(value="")
        self.page_range_entry = ctk.CTkEntry(
            pdf_frame, textvariable=self.page_range_var, width=120, placeholder_text="例: 1-10,15"
        )
        self.page_range_entry.pack(side="left", padx=(0, 8))

        # 買掛金(負債)用のPDFかどうか。異常値の判定ロジックは売掛と共通のまま、
        # Geminiへの説明プロンプトの文言（売掛金/買掛金）だけ切り替える。
        self.is_payable_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            pdf_frame, text="買掛金(負債)処理", variable=self.is_payable_var
        ).pack(side="left", padx=(4, 8))

        ctk.CTkButton(
            pdf_frame, text="設定(APIキー/プロンプト)", width=170, command=self.open_settings
        ).pack(side="left", padx=(4, 8))

        # --- タブ ---
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tabview.add("手動（コピペ）")
        self.tabview.add("自動（API）")

        self._build_manual_tab(self.tabview.tab("手動（コピペ）"))
        self._build_auto_tab(self.tabview.tab("自動（API）"))

    # ------------------------------------------------------------------
    # 共通
    # ------------------------------------------------------------------
    def browse_pdf(self):
        path = filedialog.askopenfilename(
            title="総勘定元帳PDFを選択",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.pdf_path_var.set(path)

    def current_mode(self):
        return "payable" if self.is_payable_var.get() else "receivable"

    def open_settings(self):
        SettingsDialog(self)

    # ------------------------------------------------------------------
    # 自動（API）タブ
    # ------------------------------------------------------------------
    def _build_auto_tab(self, tab):
        run_frame = ctk.CTkFrame(tab, fg_color="transparent")
        run_frame.pack(fill="x", padx=4, pady=(4, 6))
        self.run_button = ctk.CTkButton(run_frame, text="解析を実行", command=self.run_pipeline)
        self.run_button.pack(side="left")
        self.status_label = ctk.CTkLabel(run_frame, text="")
        self.status_label.pack(side="left", padx=12)

        output_label = ctk.CTkLabel(tab, text="異常値の説明（会社名置き換え後）")
        output_label.pack(anchor="w", padx=4, pady=(6, 0))

        self.output_box = ctk.CTkTextbox(tab)
        self.output_box.pack(fill="both", expand=True, padx=4, pady=(2, 4))

    def set_status(self, message):
        self.status_label.configure(text=message)

    def append_output(self, text):
        self.output_box.insert("end", text + "\n")
        self.output_box.see("end")

    def run_pipeline(self):
        self.run_button.configure(state="disabled")
        self.output_box.delete("1.0", "end")
        self.set_status("実行中...")
        thread = threading.Thread(target=self._run_pipeline_worker, daemon=True)
        thread.start()

    def _run_pipeline_worker(self):
        try:
            pdf_path = self.pdf_path_var.get()
            page_range = self.page_range_var.get()

            self.after(0, self.set_status, "PDFを解析中...")
            df, anomaly_df, details, mapping = extract_ledger.run(pdf_path, page_range=page_range)
            self.anomaly_df = anomaly_df
            self.mapping = mapping

            self.after(0, self.append_output, f"抽出件数: {len(df)}行 / 異常な補助コード: {len(details)}件\n")

            if anomaly_df.empty:
                self.after(0, self.append_output, "異常な補助コードはありませんでした。")
                return

            self.after(0, self.set_status, "Geminiに問い合わせ中...")

            def progress(msg):
                self.after(0, self.append_output, msg)

            mode = self.current_mode()
            try:
                explanation = gemini_explain.explain_anomalies(
                    anomaly_df, mode=mode, on_progress=progress
                )
            except Exception as e:
                self.after(0, self.append_output, f"Geminiへの問い合わせに失敗しました: {e}")
                self.after(0, self.append_output, "手動（コピペ）タブに切り替えます。")
                self.after(0, self.switch_to_manual_tab_with_prompt, anomaly_df, mode)
                return

            replaced = fuzoku_mapping.apply_company_names(explanation, mapping)

            self.after(0, self.append_output, "\n" + replaced)
        except Exception as e:
            self.after(0, self.append_output, f"エラー: {e}")
        finally:
            self.after(0, self.set_status, "完了")
            self.after(0, lambda: self.run_button.configure(state="normal"))

    def switch_to_manual_tab_with_prompt(self, anomaly_df, mode):
        prompt = gemini_explain.build_batch_prompt(anomaly_df, mode=mode)
        self.manual_prompt_box.delete("1.0", "end")
        self.manual_prompt_box.insert("1.0", prompt)
        self.set_manual_status("レート制限等でAPI処理が失敗しました。プロンプトを引き継ぎました。コピーしてチャットに貼り付けてください")
        self.tabview.set("手動（コピペ）")

    # ------------------------------------------------------------------
    # 手動（コピペ）タブ
    # ChatGPT/Claude/Geminiのチャット画面に手動で貼り付けて使う場合のフロー。
    # APIの無料枠が足りない・データ量が多い場合の代替手段。
    # ------------------------------------------------------------------
    def _build_manual_tab(self, tab):
        step1_frame = ctk.CTkFrame(tab, fg_color="transparent")
        step1_frame.pack(fill="x", padx=4, pady=(4, 6))
        ctk.CTkButton(
            step1_frame, text="① PDFを解析してプロンプト生成", command=self.generate_manual_prompt
        ).pack(side="left")
        ctk.CTkButton(
            step1_frame, text="コピー", width=70, command=self.copy_manual_prompt
        ).pack(side="left", padx=8)
        self.manual_status_label = ctk.CTkLabel(step1_frame, text="")
        self.manual_status_label.pack(side="left", padx=12)

        ctk.CTkLabel(tab, text="① 生成されたプロンプト（ChatGPT/Claude/Geminiのチャットに貼り付けてください）").pack(
            anchor="w", padx=4, pady=(4, 0)
        )
        self.manual_prompt_box = ctk.CTkTextbox(tab, height=40)
        self.manual_prompt_box.pack(fill="x", expand=False, padx=4, pady=(2, 6))

        ctk.CTkLabel(tab, text="② チャットの回答をここに貼り付け").pack(anchor="w", padx=4, pady=(4, 0))
        self.manual_reply_box = ctk.CTkTextbox(tab, height=40)
        self.manual_reply_box.pack(fill="x", expand=False, padx=4, pady=(2, 6))

        ctk.CTkButton(
            tab, text="③ 会社名に置換して表示", command=self.apply_manual_reply
        ).pack(anchor="w", padx=4, pady=(0, 6))

        ctk.CTkLabel(tab, text="③ 置換後の結果").pack(anchor="w", padx=4, pady=(4, 0))
        self.manual_output_box = ctk.CTkTextbox(tab)
        self.manual_output_box.pack(fill="both", expand=True, padx=4, pady=(2, 4))

    def set_manual_status(self, message):
        self.manual_status_label.configure(text=message)

    def generate_manual_prompt(self):
        try:
            pdf_path = self.pdf_path_var.get()
            page_range = self.page_range_var.get()
            self.set_manual_status("PDFを解析中...")
            self.update_idletasks()

            df, anomaly_df, details, mapping = extract_ledger.run(pdf_path, page_range=page_range)
            self.anomaly_df = anomaly_df
            self.mapping = mapping

            self.manual_prompt_box.delete("1.0", "end")

            if anomaly_df.empty:
                self.manual_prompt_box.insert("1.0", "異常な補助コードはありませんでした。プロンプトは生成されません。")
                self.set_manual_status(f"抽出{len(df)}行 / 異常0件")
                return

            prompt = gemini_explain.build_batch_prompt(anomaly_df, mode=self.current_mode())
            self.manual_prompt_box.insert("1.0", prompt)
            self.set_manual_status(f"抽出{len(df)}行 / 異常{len(details)}件のプロンプトを生成しました")
        except Exception as e:
            self.set_manual_status(f"エラー: {e}")

    def copy_manual_prompt(self):
        text = self.manual_prompt_box.get("1.0", "end").strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.set_manual_status("プロンプトをクリップボードにコピーしました")

    def apply_manual_reply(self):
        reply = self.manual_reply_box.get("1.0", "end").strip()
        self.manual_output_box.delete("1.0", "end")

        if not reply:
            self.manual_output_box.insert("1.0", "貼り付けの回答が空です。")
            return

        if self.mapping is None:
            self.manual_output_box.insert("1.0", "先に「① PDFを解析してプロンプト生成」を実行してください。")
            return

        replaced = fuzoku_mapping.apply_company_names(reply, self.mapping)
        self.manual_output_box.insert("1.0", replaced)

        self.set_manual_status("会社名への置換が完了しました")


class SettingsDialog(ctk.CTkToplevel):
    """APIキー・プロンプトテンプレートをGUIから編集するダイアログ。
    保存すると exe と同じフォルダに外部ファイルとして書き出され、
    以後は exe埋め込みの既定値より優先して使われる(paths.overridable_resource_path)。
    """

    FILES = {
        "gemini_api_key.txt": "APIキー(1行1キー)",
        "gemini_prompt_batch.txt": "プロンプト(売掛金)",
        "gemini_prompt_batch_payable.txt": "プロンプト(買掛金)",
    }

    def __init__(self, master):
        super().__init__(master)
        self.title("設定(APIキー/プロンプト)")
        self.geometry("700x600")
        self.transient(master)

        self.boxes = {}
        tabview = ctk.CTkTabview(self)
        tabview.pack(fill="both", expand=True, padx=8, pady=8)

        for filename, label in self.FILES.items():
            tab = tabview.add(label)
            box = ctk.CTkTextbox(tab)
            box.pack(fill="both", expand=True, padx=4, pady=(4, 4))
            try:
                with open(paths.overridable_resource_path(filename), encoding="utf-8") as f:
                    box.insert("1.0", f.read())
            except OSError:
                pass
            self.boxes[filename] = box

            bottom = ctk.CTkFrame(tab, fg_color="transparent")
            bottom.pack(fill="x", padx=4, pady=(0, 4))
            status = ctk.CTkLabel(bottom, text="")
            status.pack(side="left")
            ctk.CTkButton(
                bottom, text="保存", width=80,
                command=lambda fn=filename, st=status: self.save(fn, st),
            ).pack(side="right")

    def save(self, filename, status_label):
        content = self.boxes[filename].get("1.0", "end").rstrip("\n") + "\n"
        target = paths.path(filename)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        status_label.configure(text=f"保存しました: {target}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
