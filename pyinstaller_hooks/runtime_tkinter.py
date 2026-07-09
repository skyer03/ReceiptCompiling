import os
import sys


def _resource_dir(name: str) -> str:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.join(base_dir, name)


os.environ.setdefault("TCL_LIBRARY", _resource_dir("_tcl_data"))
os.environ.setdefault("TK_LIBRARY", _resource_dir("_tk_data"))
