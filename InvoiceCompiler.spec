# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


def safe_collect_data_files(package_name):
    try:
        return collect_data_files(package_name)
    except Exception:
        return []


def safe_collect_dynamic_libs(package_name):
    try:
        return collect_dynamic_libs(package_name)
    except Exception:
        return []


def include_rapidocr_module(module_name):
    return not module_name.startswith('rapidocr.inference_engine.pytorch')


def tkinter_resource_paths():
    python_root = Path(sys.base_prefix)
    tcl_root = python_root / 'tcl'
    tcl_dir = tcl_root / 'tcl8.6'
    tk_dir = tcl_root / 'tk8.6'
    dll_dir = python_root / 'DLLs'
    return tcl_dir, tk_dir, dll_dir


tcl_dir, tk_dir, dll_dir = tkinter_resource_paths()
easyofd_hiddenimports = collect_submodules('easyofd', on_error='ignore')
tkinter_hiddenimports = collect_submodules('tkinter', on_error='ignore') + ['_tkinter']
rapidocr_hiddenimports = collect_submodules('rapidocr', filter=include_rapidocr_module, on_error='ignore')
onnxruntime_hiddenimports = [
    'onnxruntime',
    'onnxruntime.capi._pybind_state',
    'onnxruntime.capi.onnxruntime_inference_collection',
]
pymupdf_hiddenimports = collect_submodules('pymupdf', on_error='ignore') + collect_submodules('fitz', on_error='ignore')
rapidocr_datas = safe_collect_data_files('rapidocr')
onnxruntime_binaries = safe_collect_dynamic_libs('onnxruntime')
pymupdf_datas = safe_collect_data_files('pymupdf') + safe_collect_data_files('fitz')
tkinter_datas = []
if tcl_dir.exists():
    tkinter_datas.append((str(tcl_dir), '_tcl_data'))
if tk_dir.exists():
    tkinter_datas.append((str(tk_dir), '_tk_data'))
tkinter_binaries = []
for dll_name in ('tcl86t.dll', 'tk86t.dll'):
    dll_path = dll_dir / dll_name
    if dll_path.exists():
        tkinter_binaries.append((str(dll_path), '.'))


a = Analysis(
    ['invoice_compiler.py'],
    pathex=['tools/easyofd/easyofd-20260427'],
    binaries=onnxruntime_binaries + tkinter_binaries,
    datas=[
        ('tools/ofd2pdf', 'tools/ofd2pdf'),
        ('tools/easyofd', 'tools/easyofd'),
        ('tools/rapidocr_models', 'tools/rapidocr_models'),
    ] + rapidocr_datas + pymupdf_datas + tkinter_datas,
    hiddenimports=easyofd_hiddenimports + tkinter_hiddenimports + rapidocr_hiddenimports + onnxruntime_hiddenimports + pymupdf_hiddenimports,
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=['pyinstaller_hooks/runtime_tkinter.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='InvoiceCompiler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
