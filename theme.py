"""MD3 baseline color roles, rounded buttons and native DPI-aware text."""
from pathlib import Path
SURFACE='#f3f8fc'
CARD='#ffffff'
PRIMARY='#246b99'
ON_PRIMARY='#ffffff'
SOFT='#d4ebfc'
TEXT='#18374d'
MUTED='#536b7b'
OUTLINE='#bfd5e5'
FONT='Microsoft YaHei UI'


def enable_high_dpi():
    """Per-process only; call before creating the first native window."""
    import os
    if os.name!='nt':return
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ZhishiNailong.ROCONearby')
    except (AttributeError,OSError):pass
    try:
        function=ctypes.windll.user32.SetProcessDpiAwarenessContext
        function.argtypes=[ctypes.c_void_p];function.restype=ctypes.c_bool
        if function(ctypes.c_void_p(-4)):return
    except (AttributeError,OSError):pass
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError,OSError):
        try:ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError,OSError):pass


def apply_window_icon(root,icon):
    """Select native icon pixels for the current monitor rather than enlarging 16px."""
    import os
    from PIL import Image,ImageTk
    with Image.open(icon) as image:
        root._roco_icon_photos=[ImageTk.PhotoImage(image.ico.getimage(size),master=root)
                               for size in sorted(image.ico.sizes())]
    root.iconphoto(True,*root._roco_icon_photos)
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes as w
    user=ctypes.windll.user32
    user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
    user.LoadImageW.argtypes=[w.HINSTANCE,w.LPCWSTR,w.UINT,ctypes.c_int,ctypes.c_int,w.UINT]
    user.LoadImageW.restype=w.HANDLE
    user.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.SendMessageW.restype=ctypes.c_ssize_t
    user.DestroyIcon.argtypes=[w.HANDLE]
    handles=[]
    last_identity=[None]
    def update(event=None):
        if event is not None and event.widget is not root:return
        hwnd=user.GetAncestor(root.winfo_id(),2)
        try:
            user.GetDpiForWindow.argtypes=[w.HWND];user.GetDpiForWindow.restype=w.UINT
            dpi=user.GetDpiForWindow(hwnd) or 96
        except AttributeError:dpi=96
        identity=(hwnd,dpi)
        if identity==last_identity[0]:return
        fresh=[]
        for kind,base in ((0,16),(1,32)):
            pixels=round(base*dpi/96)
            handle=user.LoadImageW(None,str(icon),1,pixels,pixels,0x10)
            if handle:
                user.SendMessageW(hwnd,0x80,kind,handle);fresh.append(handle)
        if fresh:
            for handle in handles:user.DestroyIcon(handle)
            handles[:]=fresh;last_identity[0]=identity
    def dispose(event):
        if event.widget is root:
            for handle in handles:user.DestroyIcon(handle)
            handles.clear()
    root.after_idle(update)
    root.bind('<Map>',update,add='+');root.bind('<Configure>',update,add='+')
    root.bind('<Destroy>',dispose,add='+')


def rounded_buttons(root,style):
    try:
        from PIL import Image,ImageDraw,ImageTk
    except ImportError:return
    root._md3_images=[]
    scale=max(1,root.winfo_fpixels('1i')/96)
    side=max(24,round(24*scale));border=max(8,round(8*scale))
    def tile(color):
        image=Image.new('RGBA',(side*4,side*4),(0,0,0,0))
        ImageDraw.Draw(image).rounded_rectangle((0,0,side*4-1,side*4-1),radius=border*4,fill=color)
        image=image.resize((side,side),Image.Resampling.LANCZOS)
        photo=ImageTk.PhotoImage(image,master=root);root._md3_images.append(photo);return photo
    for name,normal,active,pressed in (('TButton',SOFT,'#c4e2f7','#acd4ee'),
            ('Primary.TButton',PRIMARY,'#337ead','#195780')):
        element='MD3.'+name+'.border'
        style.element_create(element,'image',tile(normal),('disabled',tile('#e2eaf0')),
            ('pressed',tile(pressed)),('active',tile(active)),border=border,padding=0,sticky='nsew')
        style.layout(name,[(element,{'sticky':'nsew','children':[
            ('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])


def apply(root):
    global FONT
    from tkinter import ttk
    from tkinter import font
    icon=Path(__file__).resolve().parent/'assets/roco.ico'
    if icon.is_file():apply_window_icon(root,icon)
    available=set(font.families(root))
    FONT=next((family for family in ('Microsoft YaHei UI','Microsoft YaHei','Segoe UI') if family in available),'TkDefaultFont')
    root.configure(bg=SURFACE)
    style=ttk.Style(root)
    style.theme_use('clam')
    style.configure('.',font=(FONT,10),background=SURFACE,foreground=TEXT)
    style.configure('TFrame',background=SURFACE)
    style.configure('TLabel',background=SURFACE,foreground=TEXT)
    style.configure('Card.TFrame',background=CARD)
    style.configure('Card.TLabel',background=CARD)
    style.configure('Muted.TLabel',foreground=MUTED)
    style.configure('TButton',padding=(12,5),width=0,borderwidth=0,background=SOFT,foreground='#123c57',focuscolor=PRIMARY)
    style.map('TButton',background=[('active','#c4e2f7'),('disabled','#e2eaf0')],foreground=[('disabled','#617583')])
    style.configure('Primary.TButton',background=PRIMARY,foreground=ON_PRIMARY)
    style.map('Primary.TButton',background=[('active','#337ead'),('disabled','#e2eaf0')],foreground=[('disabled','#617583')])
    style.configure('TEntry',padding=6,fieldbackground=CARD,bordercolor=OUTLINE,lightcolor=OUTLINE,darkcolor=OUTLINE)
    style.map('TEntry',bordercolor=[('focus',PRIMARY)],lightcolor=[('focus',PRIMARY)],darkcolor=[('focus',PRIMARY)])
    style.configure('TCombobox',padding=5,fieldbackground=CARD,arrowsize=12)
    style.map('TCombobox',fieldbackground=[('readonly',CARD)],foreground=[('readonly',TEXT)])
    style.configure('TCheckbutton',padding=3,indicatorbackground=CARD,indicatorforeground=PRIMARY)
    style.map('TCheckbutton',indicatorbackground=[('selected',SOFT)])
    style.configure('TRadiobutton',padding=3)
    style.configure('TNotebook',background=SURFACE,borderwidth=0)
    style.configure('TNotebook.Tab',padding=(16,8),background='#e9f2f9',foreground=MUTED,font=(FONT,10,'bold'))
    style.map('TNotebook.Tab',background=[('selected',SOFT)],foreground=[('selected',PRIMARY)])
    rowheight=max(34,font.Font(root,font=(FONT,11)).metrics('linespace')+12)
    style.configure('Treeview',font=(FONT,11),rowheight=rowheight,
        background=CARD,fieldbackground=CARD,foreground=TEXT,borderwidth=0)
    style.configure('Treeview.Heading',font=(FONT,10,'bold'),padding=(8,9),
        background='#e9f2f9',foreground=TEXT,borderwidth=0)
    style.map('Treeview',background=[('selected',SOFT)],foreground=[('selected','#123c57')])
    style.configure('Vertical.TScrollbar',arrowsize=10,background=OUTLINE,troughcolor=SURFACE,borderwidth=0)
    rounded_buttons(root,style)
    return style
