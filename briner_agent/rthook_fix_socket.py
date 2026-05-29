import sys
import os

# This hook runs BEFORE PyInstaller's pyi_rth_multiprocessing.
# On Python 3.8+, Windows changed how DLL dependencies of .pyd files are resolved:
# sys.path alone is not enough — os.add_dll_directory() must be called explicitly.
# Without it, _socket.pyd fails to load ("No module named '_socket'") on machines
# that do not have Python 3.14 installed system-wide.

if hasattr(sys, '_MEIPASS'):
    meipass = sys._MEIPASS
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))

    # 1. Register DLL search directories (Python 3.8+ API for C extensions)
    for d in (meipass, exe_dir):
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
        if d not in sys.path:
            sys.path.insert(0, d)

    # 2. Pre-load _socket.pyd via ctypes so it is already in the Windows DLL cache
    #    when the Python import system tries to load it as a module.
    _pyd = os.path.join(meipass, '_socket.pyd')
    if os.path.exists(_pyd):
        try:
            import ctypes
            ctypes.CDLL(_pyd)
        except OSError as _e:
            # DLL load failed — write a diagnostic log next to the exe
            try:
                _log = os.path.join(exe_dir, 'briner_socket_error.log')
                with open(_log, 'w', encoding='utf-8') as _f:
                    _f.write(f"_socket.pyd path: {_pyd}\n")
                    _f.write(f"Error: {_e}\n")
                    _f.write(f"sys._MEIPASS: {meipass}\n")
                    _f.write(f"sys.executable: {sys.executable}\n")
            except Exception:
                pass
