import sys
import os


def base_dir():
    """exe化(PyInstaller onefile)時はexeのあるフォルダ、それ以外はこのスクリプトのフォルダを返す。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def path(*parts):
    return os.path.join(base_dir(), *parts)


def resource_path(*parts):
    """アプリに同梱した読み取り専用データ(プロンプトのテンプレート等)のパスを返す。
    PyInstaller(onefile)実行時はexe内に埋め込まれ一時展開フォルダ(_MEIPASS)に置かれるため、
    APIキーや出力CSVのような exe と同じ場所に置く可変ファイルには使わないこと。
    """
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", base_dir()), *parts)
    return os.path.join(base_dir(), *parts)
