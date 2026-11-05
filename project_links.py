"""Open links outside the frozen process without inheriting bundled DLL search paths."""
import os


def _open_windows(url):
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel.GetDllDirectoryW.argtypes=[w.DWORD,w.LPWSTR];kernel.GetDllDirectoryW.restype=w.DWORD
    kernel.SetDllDirectoryW.argtypes=[w.LPCWSTR];kernel.SetDllDirectoryW.restype=w.BOOL
    shell.ShellExecuteW.argtypes=[w.HWND,w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,ctypes.c_int]
    shell.ShellExecuteW.restype=ctypes.c_void_p
    previous=ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(previous),previous)
    if not kernel.SetDllDirectoryW(None):
        raise OSError('无法准备系统浏览器运行环境')
    try:
        result=shell.ShellExecuteW(None,'open',url,None,None,1)
    finally:
        kernel.SetDllDirectoryW(previous.value or None)
    if (result or 0)<=32:
        raise OSError(f'Windows 未能打开项目地址（错误码 {result or 0}）')
    return True


def open_project_url(url):
    if os.name=='nt':return _open_windows(url)
    import webbrowser
    if not webbrowser.open_new_tab(url):raise OSError('未找到可用的浏览器')
    return True
