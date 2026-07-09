def pre_find_module_path(_hook_api):
    # The Python distribution used for packaging has Tcl/Tk files, but
    # PyInstaller's automatic Tcl probe can fail before it scans tkinter.
    # Keep tkinter discoverable; the spec manually bundles Tcl/Tk resources.
    return None
