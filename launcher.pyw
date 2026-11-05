"""Windowless GUI entry point; failures remain visible without a console."""
import sys
import traceback
from pathlib import Path

if __name__=='__main__':
    try:
        from roco_nearby_analyzer import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        details=traceback.format_exc()
        destination=Path(__file__).resolve().parent/'launch_error.log'
        try:destination.write_text(details,encoding='utf-8')
        except OSError:pass
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '启动失败。请先安装 Python 3.10+ 并运行“安装依赖.bat”。\n\n'
            +details.splitlines()[-1]+'\n\n详细信息：'+str(destination),'RoCo 启动失败',0x10)
        sys.exit(1)
