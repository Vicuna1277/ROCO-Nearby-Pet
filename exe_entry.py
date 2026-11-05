"""Windowless portable entry. Source files remain usable independently."""
import json
import sys
import traceback
from pathlib import Path


def verify_package(destination):
    result = {}
    try:
        import tkinter as tk
        import theme
        from body import tables
        from battle import NATURE_NAMES
        from engine import VERSION
        from roco_nearby_analyzer import BASE
        from storage import Store
        from PIL import Image
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        resources = Path(__file__).resolve().parent
        root = tk.Tk();root.withdraw();theme.apply(root);root.update();root.destroy()
        if (resources/'map_assets/10003.webp').is_file():
            with Image.open(resources/'map_assets/10003.webp') as picture:
                picture.load();result['map_size'] = list(picture.size)
        result['form_table_records']=len(tables()[0])
        assert NATURE_NAMES[5] == '逞强'
        store = Store(':memory:');store.close()
        cipher = Cipher(algorithms.AES(b'0'*16), modes.CBC(b'1'*16))
        encrypt = cipher.encryptor();encoded = encrypt.update(b'2'*16)+encrypt.finalize()
        decrypt = cipher.decryptor();assert decrypt.update(encoded)+decrypt.finalize()==b'2'*16
        # Verify the actual frozen DLL environment without launching a browser.
        import os
        if os.name=='nt':
            import ctypes,types
            from ctypes import wintypes as w
            from project_links import _open_windows
            original_loader=ctypes.WinDLL
            kernel=original_loader('kernel32',use_last_error=True)
            kernel.GetDllDirectoryW.argtypes=[w.DWORD,w.LPWSTR];kernel.GetDllDirectoryW.restype=w.DWORD
            def dll_directory():
                buffer=ctypes.create_unicode_buffer(32768);kernel.GetDllDirectoryW(len(buffer),buffer);return buffer.value
            previous=dll_directory()
            def accept_url(hwnd,verb,url,parameters,directory,show):
                assert not dll_directory()
                assert verb=='open' and url=='https://github.com/Vicuna1277/ROCO-Nearby-Pet'
                return 33
            try:
                ctypes.WinDLL=lambda name,**kwargs:types.SimpleNamespace(ShellExecuteW=accept_url) if name=='shell32' else original_loader(name,**kwargs)
                assert _open_windows('https://github.com/Vicuna1277/ROCO-Nearby-Pet')
                assert dll_directory()==previous
                result['native_url_launcher_environment_verified']=True
            finally:ctypes.WinDLL=original_loader
        # Exercise the packaged application and radar with isolated empty saves.
        import tempfile
        import roco_nearby_analyzer as api
        from desktop import Desktop
        saved_base,saved_db=api.BASE,api.DEFAULT_DB
        original_tk,original_top=tk.Tk,tk.Toplevel
        original_refresh=Desktop.refresh_devices
        def hidden(factory):
            def create(*args,**kwargs):
                window=factory(*args,**kwargs);window.attributes('-alpha',0);return window
            return create
        with tempfile.TemporaryDirectory() as folder:
            try:
                api.BASE=Path(folder);api.DEFAULT_DB=api.BASE/'data/nearby.sqlite3'
                tk.Tk,tk.Toplevel=hidden(original_tk),hidden(original_top)
                Desktop.refresh_devices=lambda self:None
                application=Desktop(api);application.root.update();application.show_radar();application.root.update()
                assert len(application.tabs.tabs())==4 and len(application.radar.tabs.tabs())==3
                application.close();result['main_window_and_radar']=True
            finally:
                api.BASE,api.DEFAULT_DB=saved_base,saved_db
                tk.Tk,tk.Toplevel=original_tk,original_top
                Desktop.refresh_devices=original_refresh
        result.update(ok=True, version=VERSION, data_root=str(BASE), resources_loaded=True,
                      tkinter=True, sqlite=True, aes=True)
    except Exception:
        result.update(ok=False,error=traceback.format_exc())
    Path(destination).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--verify-package':
        raise SystemExit(verify_package(sys.argv[2]))
    try:
        from roco_nearby_analyzer import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import ctypes
        folder=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
        details=traceback.format_exc()
        (folder/'launch_error.log').write_text(details,encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,'启动失败，详情已写入 EXE 旁的 launch_error.log。\n'+details.splitlines()[-1],'ROCO附近精灵',0x10)
        raise SystemExit(1)
