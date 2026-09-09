import sys
import os


def base_dir():
    """exe化(PyInstaller onefile)時はexeのあるフォルダ、それ以外はこのスクリプトのフォルダを返す。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def path(*parts):
    return os.path.join(base_dir(), *parts)
