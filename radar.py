"""Native Tk floating radar; all widget access stays on the GUI thread."""
import math
import time
import json
from pathlib import Path
from filters import voice_label
from body import pct_text
from presentation import display_name,voice_text,location_text,row_key,favorite_tags,project,height_extreme,height_text,radar_cache
from mapview import MapRenderer
import theme
from battle_view import BattlePanel


def entity_style(entity):
    tags=favorite_tags(entity)
    if entity.get('captured'):return 'captured','#94a3b8'
    if '异色' in tags and '炫彩' in tags:return 'both','#e879f9'
    if '异色' in tags:return 'shiny','#a78bfa'
    if '炫彩' in tags:return 'colorful','#fbbf24'
    if entity.get('voice')==100:return 'full_melodic','#34d399'
    if entity.get('voice')==-100:return 'full_hoarse','#fb923c'
    voice=voice_label(entity.get('voice'))
    if voice=='婉转声':return 'melodic','#34d399'
    if voice=='粗嗓门':return 'hoarse','#fb923c'
    return 'ordinary','#38bdf8'


LIST_COLORS={
    'full_melodic':('#15803d','#ffffff'),
    'full_hoarse':('#c2410c','#ffffff'),
    'melodic':('#dcfce7','#14532d'),
    'hoarse':('#ffedd5','#7c2d12'),
    'both':('#fae8ff','#252832'),
    'shiny':('#ede9fe','#252832'),
    'colorful':('#fef3c7','#252832'),
    'ordinary':('#ffffff','#50545f'),
    'captured':('#eef2f6','#64748b'),
}

EMPHASIS_COLORS={
    'full_melodic':('#14532d','#ffffff'),
    'melodic':('#86efac','#14532d'),
    'full_hoarse':('#7c2d12','#ffffff'),
    'hoarse':('#fdba74','#7c2d12'),
    'ordinary':('#e2e8f0','#334155'),
    'both':('#f0abfc','#701a75'),
    'shiny':('#c4b5fd','#4c1d95'),
    'colorful':('#fde68a','#713f12'),
}


def list_tag(entity):
    """Voice backgrounds take precedence; rare identity stays in the name and radar dot."""
    if entity.get('captured'):return 'captured'
    label=voice_label(entity.get('voice'))
    if label in ('婉转声','粗嗓门'):
        base=('full_melodic' if entity.get('voice')==100 else 'melodic') if label=='婉转声' else (
            'full_hoarse' if entity.get('voice')==-100 else 'hoarse')
        return base
    return entity_style(entity)[0]


def row_halves(entity):
    """Front=name/voice, back=body/location. Heavy side signals the height extreme."""
    tag=list_tag(entity);base=LIST_COLORS[tag]
    extreme=height_extreme(entity)
    if tag=='captured' or not extreme:return base,base
    heavy=EMPHASIS_COLORS[tag]
    return (heavy,base) if extreme=='large' else (base,heavy)


def show_color_legend(root):
    import tkinter as tk
    from tkinter import ttk
    window=tk.Toplevel(root);window.title('声音 × 身高 · 分段颜色');window.geometry('520x440');window.minsize(400,350)
    window.configure(bg=theme.SURFACE)
    ttk.Label(window,text='四档声音 × 两种极端身高',font=(theme.FONT,12,'bold'),padding=12).pack(fill='x')
    grid=ttk.Frame(window,padding=(10,0));grid.pack(fill='both',expand=True)
    grid.columnconfigure(0,weight=1);grid.columnconfigure(1,weight=1)
    labels=[]
    for row,(base,voice) in enumerate((('full_melodic','+100 dB'),('melodic','+96～+99 dB'),
            ('full_hoarse','-100 dB'),('hoarse','-99～-96 dB'))):
        for col,(suffix,size) in enumerate((('small','0% 小不点'),('large','100% 大块头'))):
            entity={'voice':100 if base=='full_melodic' else 99 if base=='melodic' else -100 if base=='full_hoarse' else -99,
                'height_pct':0 if suffix=='small' else 100}
            front,back=row_halves(entity);bg,fg=LIST_COLORS[base]
            label=tk.Text(grid,height=3,width=1,wrap='word',bg=bg,fg=fg,font=(theme.FONT,10),bd=0,padx=5,pady=7,takefocus=0)
            for tag,colors in (('front',front),('back',back)):label.tag_configure(tag,background=colors[0],foreground=colors[1])
            label.insert('end','丢丢 · '+voice+' · ','front');label.insert('end','高'+size,'back');label.configure(state='disabled')
            label.grid(row=row,column=col,sticky='nsew',padx=3,pady=3);labels.append(label)
        grid.rowconfigure(row,weight=1)
    ttk.Label(window,text='大块头：前段加深；小不点：后段加深。\n满声仍用深色，其他身高保持原声音底色。',
        padding=12,wraplength=450).pack(fill='x')
    return window


def setup_tree(tree):
    for tag,(background,foreground) in LIST_COLORS.items():
        tree.tag_configure(tag,background=background,foreground=foreground)


def fill_tree(tree,rows):
    selected=tree.selection();scroll=tree.yview()[0]
    previous=getattr(tree,'_roco_rows',{});current={};order=[]
    for e in rows:
        iid=json.dumps(row_key(e),ensure_ascii=False)
        values=(display_name(e),voice_text(e.get('voice')),height_text(e),pct_text(e.get('weight_pct')),location_text(e))
        signature=(list_tag(e),values);current[iid]=signature;order.append(iid)
        if iid not in previous:tree.insert('','end',iid=iid,tags=(signature[0],),values=values)
        elif signature!=previous[iid]:tree.item(iid,tags=(signature[0],),values=values)
    removed=[iid for iid in previous if iid not in current]
    if removed:tree.delete(*removed)
    if order!=getattr(tree,'_roco_order',[]):
        for index,iid in enumerate(order):tree.move(iid,'',index)
    tree._roco_rows=current;tree._roco_order=order
    surviving=[iid for iid in selected if iid in current]
    if surviving:tree.selection_set(surviving)
    tree.yview_moveto(scroll)


class FlowBar:
    """Small control groups wrap onto additional lines in a narrow window."""
    def __init__(self,parent):
        from tkinter import ttk
        self.frame=ttk.Frame(parent)
        self.frame.pack(fill='x',padx=5,pady=3)
        self.groups=[];self.pending=None
        self.frame.bind('<Configure>',lambda _:self.schedule())

    def group(self):
        from tkinter import ttk
        group=ttk.Frame(self.frame)
        self.groups.append(group)
        self.schedule()
        return group

    def schedule(self):
        if self.pending is None:self.pending=self.frame.after_idle(self.layout)

    def layout(self):
        self.pending=None
        width=max(1,self.frame.winfo_width());x=y=row_height=0
        for group in self.groups:
            # A whole group can also wrap at high Windows display scaling.
            children=group.winfo_children();inner_x=inner_y=inner_h=group_w=0
            for child in children:
                w,h=child.winfo_reqwidth(),child.winfo_reqheight()
                if inner_x and inner_x+w>width:
                    inner_x=0;inner_y+=inner_h+3;inner_h=0
                if child.winfo_manager()=='pack':child.pack_forget()
                child.place(x=inner_x,y=inner_y,width=w,height=h)
                inner_x+=w+4;inner_h=max(inner_h,h);group_w=max(group_w,inner_x-4)
            group.configure(width=max(1,group_w),height=inner_y+inner_h)
            w,h=group.winfo_reqwidth(),group.winfo_reqheight()
            if x and x+w>width:x=0;y+=row_height+3;row_height=0
            group.place(x=x,y=y,width=w,height=h)
            x+=w+8;row_height=max(row_height,h)
        height=y+row_height
        if self.frame.winfo_reqheight()!=height:self.frame.configure(height=height)


class CardList:
    """One compact rich-text paragraph per creature, wrapping only when needed."""
    def __init__(self,parent):
        import tkinter as tk
        from tkinter import ttk
        self.frame=ttk.Frame(parent)
        self.canvas=tk.Canvas(self.frame,highlightthickness=0,bg='#ffffff',width=1,height=1)
        scrollbar=ttk.Scrollbar(self.frame,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
        self.content=tk.Frame(self.canvas,bg='#ffffff')
        self.content_id=self.canvas.create_window(0,0,anchor='nw',window=self.content)
        self.cards={};self.order=[];self.selected=None;self.scale=1;self.width=1;self.layout_timer=None
        self.canvas.bind('<Configure>',self.resize)
        self.content.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.bind_wheel(self.canvas);self.bind_wheel(self.content)
        self.empty=tk.Label(self.content,text='暂无记录',bg='#ffffff',fg='#777b86',anchor='w',font=('Microsoft YaHei UI',10))
        self.empty.pack(fill='x',padx=6,pady=6)
        self.frame.bind('<Destroy>',self.destroyed)

    def destroyed(self,event):
        if event.widget is self.frame and self.layout_timer is not None:
            try:self.frame.after_cancel(self.layout_timer)
            except Exception:pass

    def bind_wheel(self,widget):widget.bind('<MouseWheel>',self.wheel)
    def wheel(self,event):
        if event.delta:self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        return 'break'

    def resize(self,event):
        if self.width==event.width:return
        self.width=max(1,event.width)
        self.canvas.itemconfigure(self.content_id,width=self.width)
        self.empty.configure(wraplength=max(20,self.width-12))
        self.schedule_layout()

    def schedule_layout(self):
        if self.layout_timer is None:self.layout_timer=self.frame.after(30,self.layout)

    def layout(self):
        self.layout_timer=None
        if not self.frame.winfo_ismapped():return
        for card in self.cards.values():
            text=card['text']
            # Never use Text.count -update: Tk can block its event loop while
            # synchronously laying out thousands of imported/hidden paragraphs.
            # The timer runs after the normal display pass, without nested updates.
            lines=text.count('1.0','end-1c','displaylines')
            count=lines if isinstance(lines,int) else lines[0] if lines else 0
            height=max(1,count+1)
            if int(text.cget('height'))!=height:text.configure(height=height)

    def text_resized(self,event,key):
        card=self.cards.get(key)
        if card is not None and card.get('width')!=event.width:
            card['width']=event.width;self.schedule_layout()

    def set_rows(self,rows,scale=1):
        import tkinter as tk
        from body import pct_text,enrich
        keys=[row_key(e) for e in rows];keep=set(keys);changed=keys!=self.order
        for key in list(self.cards):
            if key not in keep:self.cards.pop(key)['frame'].destroy()
        if rows:self.empty.pack_forget()
        else:self.empty.pack(fill='x',padx=6,pady=6)
        for entity,key in zip(rows,keys):
            e=entity if 'height_pct' in entity else enrich(entity)
            bg,fg=LIST_COLORS[list_tag(e)]
            front,back=row_halves(e)
            if key not in self.cards:
                frame=tk.Frame(self.content,highlightthickness=1,highlightbackground='#edf0f5',padx=3,pady=2)
                text=tk.Text(frame,height=1,width=1,wrap='word',bd=0,highlightthickness=0,
                             padx=0,pady=0,spacing1=0,spacing3=0,cursor='arrow',takefocus=0)
                text.pack(fill='x')
                text.bind('<Configure>',lambda event,key=key:self.text_resized(event,key))
                self.cards[key]={'frame':frame,'text':text,'signature':None}
                for widget in (frame,text):
                    self.bind_wheel(widget)
                    widget.bind('<Button-1>',lambda _,selected=key:self.select_key(selected,scroll=False))
            card=self.cards[key];text=card['text']
            location=location_text(e).replace(' m · ','m ')
            parts=(display_name(e),' · ',voice_text(e.get('voice')),
                   f" · 高{height_text(e)} 重{pct_text(e.get('weight_pct'))} · {location}")
            signature=(parts,scale,bg,fg,front,back,e.get('voice'))
            if signature!=card['signature']:
                changed=True
                font_size=max(7,round(10*scale))
                card['frame'].configure(bg=bg)
                text.configure(state='normal',bg=bg,fg=fg,font=('Microsoft YaHei UI',font_size))
                text.tag_configure('name',foreground=fg,font=('Microsoft YaHei UI',font_size,'bold'))
                text.tag_configure('voice',foreground=fg)
                text.tag_configure('front',background=front[0],foreground=front[1])
                text.tag_configure('back',background=back[0],foreground=back[1])
                text.delete('1.0','end');text.insert('end',parts[0],('name','front'));text.insert('end',parts[1],'front')
                text.insert('end',parts[2],('voice','front'));text.insert('end',parts[3],'back')
                text.tag_raise('front');text.tag_raise('back');text.configure(state='disabled')
                card['signature']=signature
        if keys!=self.order:
            for key in keys:self.cards[key]['frame'].pack_forget()
            for key in keys:self.cards[key]['frame'].pack(fill='x',padx=1,pady=(0,1))
        self.order=keys;self.scale=scale
        if self.selected not in keep:self.selected=None
        self.highlight()
        if changed:self.schedule_layout()

    def highlight(self):
        for key,card in self.cards.items():
            card['frame'].configure(highlightbackground='#49618f' if key==self.selected else '#edf0f5')

    def select_key(self,key,scroll=True):
        if key not in self.cards:return
        self.selected=key;self.highlight()
        if scroll:
            self.frame.after_idle(lambda:self.scroll_to(key))

    def scroll_to(self,key):
        if not self.frame.winfo_exists() or key not in self.cards:return
        top=self.cards[key]['frame'].winfo_y();height=max(1,self.content.winfo_height())
        self.canvas.yview_moveto(top/height)


class PagedCardList:
    """Bound the native widgets for a week of cached creatures without hiding the total."""
    def __init__(self,parent,page_size=50):
        import tkinter as tk
        from tkinter import ttk
        self.frame=ttk.Frame(parent);self.page_size=page_size;self.page=0;self.rows=[];self.scale=1
        controls=ttk.Frame(self.frame);controls.pack(fill='x',padx=3,pady=3)
        self.previous=ttk.Button(controls,text='‹',width=2,command=lambda:self.change_page(-1));self.previous.pack(side='left')
        self.page_text=tk.StringVar(value='1/1')
        ttk.Label(controls,textvariable=self.page_text,anchor='center').pack(side='left',fill='x',expand=True)
        self.next=ttk.Button(controls,text='›',width=2,command=lambda:self.change_page(1));self.next.pack(side='right')
        self.content=CardList(self.frame);self.content.frame.pack(fill='both',expand=True)
        self.frame.bind('<Map>',lambda event:self.show_page() if event.widget is self.frame else None)

    @property
    def cards(self):return self.content.cards

    @property
    def order(self):return self.content.order

    def set_rows(self,rows,scale=1):
        self.rows=rows;self.scale=scale
        if self.frame.winfo_ismapped():self.show_page()
        else:self.update_controls()

    def update_controls(self):
        pages=max(1,math.ceil(len(self.rows)/self.page_size));self.page=min(max(0,self.page),pages-1)
        self.page_text.set(f'{self.page+1}/{pages}')
        self.previous.configure(state='disabled' if self.page==0 else 'normal')
        self.next.configure(state='disabled' if self.page==pages-1 else 'normal')

    def show_page(self):
        self.update_controls();start=self.page*self.page_size
        self.content.set_rows(self.rows[start:start+self.page_size],self.scale)

    def change_page(self,step):
        self.page+=step;self.show_page();self.content.canvas.yview_moveto(0)

    def select_key(self,key,scroll=True):
        index=next((i for i,e in enumerate(self.rows) if row_key(e)==key),None)
        if index is None:return
        self.page=index//self.page_size;self.show_page();self.content.select_key(key,scroll)


class RadarWindow:
    def __init__(self,root,provider,radius_variable,apply_range,settings_path=None,data_menu=None,full_map=None):
        import tkinter as tk
        from tkinter import ttk
        self.provider=provider;self.root=root;self.timer=None;self.points=[]
        self.settings_path=Path(settings_path) if settings_path else None
        self.render_timer=self.save_timer=self.init_timer=None;self.sash_initialized=False
        self.zoom=1.0;self.ui_scale=1.0;self.sash_ratio=.43;settings={}
        if self.settings_path:
            try:settings=json.loads(self.settings_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        if not isinstance(settings,dict):settings={}
        def bounded(name,default,low,high):
            try:
                value=float(settings.get(name,default))
                return min(high,max(low,value)) if math.isfinite(value) else default
            except (TypeError,ValueError):return default
        self.zoom=bounded('zoom',1,.25,8)
        self.ui_scale=bounded('ui_scale',1,.5,2)
        self.sash_ratio=bounded('sash_ratio',.43,.12,.85)
        self.map_renderer=MapRenderer();self.map_photo=None;self.map_frame=None
        self.view_mode=tk.StringVar(value=settings.get('view_mode') if settings.get('view_mode') in ('radar','map') else 'map')
        self.paint_visible=tk.BooleanVar(value=settings.get('paint_visible',True) is not False)
        self.window=tk.Toplevel(root)
        self.window.title('ROCO附近精灵 · 竖版雷达')
        width=int(bounded('width',320,220,max(220,root.winfo_screenwidth()-40)))
        height=int(bounded('height',720,300,max(300,root.winfo_screenheight()-90)))
        self.window.geometry(f'{width}x{height}')
        self.window.minsize(220,300)
        self.topmost=tk.BooleanVar(value=True)
        self.window.attributes('-topmost',True)
        self.window.protocol('WM_DELETE_WINDOW',self.hide)
        self.bar=FlowBar(self.window)
        group=self.bar.group()
        ttk.Label(group,text='候选').pack(side='left')
        self.radius_entry=ttk.Entry(group,textvariable=radius_variable,width=5)
        self.radius_entry.pack(side='left',padx=3)
        self.radius_entry.bind('<Return>',lambda _:apply_range())
        ttk.Label(group,text='米').pack(side='left')
        ttk.Button(group,text='应用',width=4,command=apply_range).pack(side='left',padx=3)
        group=self.bar.group()
        ttk.Label(group,text='视野').pack(side='left')
        ttk.Button(group,text='−',width=2,command=lambda:self.change_zoom(1/1.25)).pack(side='left',padx=2)
        ttk.Button(group,text='+',width=2,command=lambda:self.change_zoom(1.25)).pack(side='left',padx=2)
        ttk.Button(group,text='复位',width=4,command=self.reset_zoom).pack(side='left',padx=2)
        group=self.bar.group()
        for label,mode in (('雷达','radar'),('地图','map')):
            ttk.Radiobutton(group,text=label,value=mode,variable=self.view_mode,
                            command=self.change_mode).pack(side='left')
        group=self.bar.group()
        ttk.Checkbutton(group,text='涂色100m',variable=self.paint_visible,command=self.change_mode).pack(side='left')
        if data_menu:ttk.Button(group,text='存档',command=data_menu,width=4).pack(side='left',padx=(4,0))
        if full_map:
            group=self.bar.group()
            ttk.Button(group,text='大地图',command=full_map).pack(side='left')
        group=self.bar.group()
        ttk.Label(group,text='界面').pack(side='left')
        self.scale_value=tk.StringVar(value=f'{self.ui_scale*100:g}')
        self.scale_entry=ttk.Entry(group,textvariable=self.scale_value,width=4)
        self.scale_entry.pack(side='left',padx=3)
        self.scale_entry.bind('<Return>',lambda _:self.apply_ui_scale())
        ttk.Label(group,text='%').pack(side='left')
        ttk.Button(group,text='缩放',width=4,command=self.apply_ui_scale).pack(side='left',padx=3)
        group=self.bar.group()
        ttk.Checkbutton(group,text='置顶',variable=self.topmost,command=lambda:self.window.attributes('-topmost',self.topmost.get())).pack(side='left')
        self.body=ttk.Panedwindow(self.window,orient='vertical')
        self.body.pack(fill='both',expand=True,padx=5)
        self.radar_frame=ttk.Frame(self.body);bottom=ttk.Frame(self.body)
        self.body.add(self.radar_frame,weight=1);self.body.add(bottom,weight=2)
        self.canvas=tk.Canvas(self.radar_frame,bg='#eaf4fc',highlightthickness=0,width=1,height=230)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda _:self.queue_render())
        self.canvas.bind('<Motion>',self.hover)
        self.canvas.bind('<Button-1>',self.select_point)
        if full_map:self.canvas.bind('<Double-Button-1>',lambda _:full_map())
        self.canvas.bind('<MouseWheel>',self.wheel_zoom)
        self.tabs=ttk.Notebook(bottom);self.tabs.pack(fill='both',expand=True)
        self.tree=self.make_cards('候选')
        self.favorite_tree=self.make_cards('收藏')
        self.battle_panel=BattlePanel(self.tabs)
        self.tabs.add(self.battle_panel.frame,text='战斗')
        self.note=tk.StringVar(value='1秒刷新 · 北朝上 · 等待数据')
        self.note_label=ttk.Label(self.window,textvariable=self.note,wraplength=width-12,padding=(5,3))
        self.note_label.pack(fill='x')
        self.hover_text=tk.StringVar(value='滚轮缩放雷达 · 拖动中间分隔条调整上下比例')
        self.hover_label=ttk.Label(self.window,textvariable=self.hover_text,wraplength=width-12,padding=(5,0,5,4))
        self.hover_label.pack(fill='x')
        self.tabs.bind('<<NotebookTabChanged>>',lambda _:self.tab_hint())
        self.window.bind('<Configure>',self.resize)
        self.window.bind('<Map>',self.mapped)
        self.body.bind('<ButtonRelease-1>',lambda _:self.remember_sash())
        self.window.bind('<Destroy>',self.destroyed)
        style=ttk.Style(root)
        style.configure('Radar.TButton',padding=(5,4),font=(theme.FONT,10))
        style.configure('Radar.TEntry',padding=3,font=(theme.FONT,10))
        style.configure('Radar.TLabel',font=(theme.FONT,10))
        style.configure('Radar.TCheckbutton',padding=2,font=(theme.FONT,10))
        style.configure('Radar.TRadiobutton',padding=2,font=(theme.FONT,10))
        for group in self.bar.groups:
            for widget in group.winfo_children():
                kind=widget.winfo_class()
                if kind in ('TButton','TEntry','TLabel','TCheckbutton','TRadiobutton'):widget.configure(style='Radar.'+kind)
        self.bar.schedule()
        self.window.withdraw();self.tick_count=0
        self.timer=root.after(1000,self.tick)

    def make_cards(self,title):
        cards=PagedCardList(self.tabs);self.tabs.add(cards.frame,text=title)
        return cards

    def tab_hint(self):
        self.hover_text.set('当前敌方信息，结束后自动清空' if self.tabs.select()==str(self.battle_panel.frame)
                            else '仅最近收到20条目标更新距离；历史在主窗口记录页 · 双击地图看全图')

    def mapped(self,event):
        if event.widget is self.window and not self.sash_initialized:
            if self.init_timer is not None:self.root.after_cancel(self.init_timer)
            # Wait for the wrapped toolbar and footer to settle before restoring
            # a split based on the final pane height.
            self.init_timer=self.root.after(100,self.initialize_sash)

    def initialize_sash(self):
        self.init_timer=None
        if not self.sash_initialized and self.body.winfo_height()>20:
            self.body.sashpos(0,int(self.body.winfo_height()*self.sash_ratio))
            self.sash_initialized=True

    def remember_sash(self):
        if self.body.winfo_height()>20:
            self.sash_ratio=min(.85,max(.12,self.body.sashpos(0)/self.body.winfo_height()))
            self.schedule_save()

    def resize(self,event):
        if event.widget is not self.window:return
        wrap=max(80,event.width-12)
        self.note_label.configure(wraplength=wrap);self.hover_label.configure(wraplength=wrap)
        self.queue_render();self.schedule_save()

    def queue_render(self):
        if self.render_timer is None:self.render_timer=self.root.after(40,self.render)

    def schedule_save(self):
        if self.save_timer is not None:self.root.after_cancel(self.save_timer)
        self.save_timer=self.root.after(400,self.save_settings)

    def save_settings(self):
        if self.save_timer is not None:self.root.after_cancel(self.save_timer)
        self.save_timer=None
        if not self.settings_path or not self.window.winfo_exists():return
        if self.window.winfo_width()<100:return
        settings=dict(width=self.window.winfo_width(),height=self.window.winfo_height(),
                      zoom=self.zoom,ui_scale=self.ui_scale,sash_ratio=self.sash_ratio,view_mode=self.view_mode.get(),
                      paint_visible=self.paint_visible.get())
        try:
            self.settings_path.parent.mkdir(parents=True,exist_ok=True)
            self.settings_path.write_text(json.dumps(settings,indent=2),encoding='utf-8')
        except OSError:pass

    def show(self):
        self.window.deiconify();self.window.lift();self.render()

    def hide(self):
        self.save_settings();self.window.withdraw()

    def destroyed(self,event):
        if event.widget is self.window:
            for timer in (self.timer,self.render_timer,self.save_timer,self.init_timer):
                if timer is not None:
                    try:self.root.after_cancel(timer)
                    except Exception:pass

    def change_zoom(self,factor):
        self.zoom=min(8,max(.25,self.zoom*factor))
        self.render();self.schedule_save()

    def change_mode(self):
        self.render();self.schedule_save()

    def reset_zoom(self):
        self.zoom=1;self.render();self.schedule_save()

    def wheel_zoom(self,event):
        if event.delta:self.change_zoom(1.25 if event.delta>0 else 1/1.25)
        return 'break'

    def apply_ui_scale(self):
        from tkinter import messagebox
        try:
            value=float(self.scale_value.get())
            if not math.isfinite(value) or not 50<=value<=200:raise ValueError
        except (ValueError,TypeError):
            messagebox.showerror('界面缩放','请输入 50～200 的百分比，例如 75。',parent=self.window)
            return False
        self.ui_scale=value/100
        self.render();self.schedule_save()
        return True

    def tick(self):
        self.tick_count+=1
        if not self.window.winfo_exists():return
        if self.window.state()!='withdrawn':self.render()
        self.timer=self.root.after(1000,self.tick)

    def render(self):
        if self.render_timer is not None:
            self.root.after_cancel(self.render_timer);self.render_timer=None
        if not hasattr(self,'tree'):return
        value,spec=self.provider()
        battle=(value or {}).get('battle') or {}
        self.battle_panel.set_data(battle,self.ui_scale)
        self.tabs.tab(self.battle_panel.frame,text=f"战斗 {len(battle.get('enemies',[])) if battle.get('active') else 0}")
        if value and value.get('archive_name'):self.window.title(f"ROCO附近精灵 · {value['archive_name']} · 雷达")
        c=self.canvas;c.delete('all');self.points=[]
        width,height=max(c.winfo_width(),1),max(c.winfo_height(),1)
        if width<10 or height<10:return
        padding=min(26,max(10,min(width,height)*.13))
        cx,cy,r=project(0,0,1,width,height,padding=padding)
        radius=value.get('radius_m',200) if value else 200
        self.visible_radius=radius/self.zoom
        font=('Microsoft YaHei UI',max(6,round(9*self.ui_scale)))
        map_active=False;map_status=''
        center=(value.get('player_position') or value.get('map_center')) if value else None
        if self.view_mode.get()=='map':
            frame,map_status=self.map_renderer.render(value.get('scene') if value else None,
                center,self.visible_radius,width,height,padding)
            if frame is not None:
                from PIL import ImageTk
                if self.paint_visible.get() and value.get('exploration'):
                    frame=self.map_renderer.paint(frame,value.get('scene'),center,self.visible_radius,
                        width,height,padding,value['exploration'])
                if frame is not self.map_frame:
                    self.map_photo=ImageTk.PhotoImage(frame,master=self.window);self.map_frame=frame
                c.create_image(0,0,image=self.map_photo,anchor='nw',tags='map_background')
                map_active=True
        if not map_active:self.map_photo=None;self.map_frame=None

        def label(x,y,text,anchor='center'):
            item=c.create_text(x,y,text=text,fill='#f1f5f9' if map_active else '#36576d',anchor=anchor,font=font)
            if map_active:
                box=c.bbox(item)
                plate=c.create_rectangle(box[0]-2,box[1]-1,box[2]+2,box[3]+1,fill='#0f172a',outline='')
                c.tag_lower(plate,item)
            return item

        if map_active:
            label(12,14,'北 ↑',anchor='w')
            scale_m=self.visible_radius/2;bar=r/2
            x0,y0=width-12-bar,height-14
            c.create_line(x0,y0,x0+bar,y0,fill='#0f172a',width=5,tags='map_scale')
            c.create_line(x0,y0,x0+bar,y0,fill='#ffffff',width=2,tags='map_scale')
            label(x0+bar/2,y0-12,f'{scale_m:g}m')
        else:
            fractions=(.5,1) if min(width,height)<180 else (.25,.5,.75,1)
            for fraction in fractions:
                q=r*fraction
                c.create_oval(cx-q,cy-q,cx+q,cy+q,outline='#b6d4e8',tags='radar_grid')
                label(cx+3,cy-q+8,f'{self.visible_radius*fraction:g}m',anchor='w')
            c.create_line(cx-r,cy,cx+r,cy,fill='#b6d4e8',tags='radar_grid')
            c.create_line(cx,cy-r,cx,cy+r,fill='#b6d4e8',tags='radar_grid')
            offset=max(7,padding/2)
            for direction,x,y in [('北',cx,cy-r-offset),('南',cx,cy+r+offset),('西',cx-r-offset,cy),('东',cx+r+offset,cy)]:
                label(x,y,direction)
        historical=bool(value and value.get('map_center') and not value.get('player_position'))
        c.create_polygon(cx,cy-6,cx-5,cy+5,cx+5,cy+5,fill='#94a3b8' if historical else '#f8fafc',outline='#0f172a',width=2)
        if historical and map_active:label(cx,cy+17,'历史')
        candidates=spec.apply(value.get('nearby',[])) if value else []
        favorites=value.get('favorites',[]) if value else []
        self.tree.set_rows(candidates,self.ui_scale);self.favorite_tree.set_rows(favorites,self.ui_scale)
        self.tabs.tab(0,text=f'候选 {len(candidates)}');self.tabs.tab(1,text=f'收藏 {len(favorites)}')
        if not value:
            label(cx,cy+20,'等待数据');self.note.set('1秒刷新 · 北朝上 · 等待数据');return
        points={row_key(e):e for e in candidates}
        points.update({row_key(e):e for e in favorites if not e.get('captured') and not e.get('other_scene')})
        for entity in points.values():
            distance=entity.get('distance_m')
            if distance is None or distance>self.visible_radius:continue
            x,y,_=project(entity['dx_m'],entity['dy_m'],self.visible_radius,width,height,padding=padding)
            color=entity_style(entity)[1];size=max(2,(5 if favorite_tags(entity) else 3)*self.ui_scale)
            if map_active:c.create_oval(x-size-1,y-size-1,x+size+1,y+size+1,fill='#0f172a',outline='#0f172a')
            c.create_oval(x-size,y-size,x+size,y+size,fill=color,outline='#f8fafc' if favorite_tags(entity) else color)
            self.points.append((x,y,entity))
        pending=' · 待玩家坐标' if not value.get('player_position') else ''
        if value.get('map_center') and not value.get('player_position'):pending=' · 历史位置，待玩家坐标'
        mode_note=(f'地图 · {map_status}' if map_active else f'{map_status}，显示雷达') if self.view_mode.get()=='map' else '雷达'
        if map_active and self.paint_visible.get():mode_note+=' · 涂色100m'
        self.note.set(f"1秒刷新 · 北朝上 · {mode_note} · 候选{radius:g}m / 视野{self.visible_radius:g}m{pending}")

    def nearest(self,event):
        nearest=min(self.points,key=lambda point:math.hypot(point[0]-event.x,point[1]-event.y),default=None)
        return nearest[2] if nearest and math.hypot(nearest[0]-event.x,nearest[1]-event.y)<=12 else None

    def hover(self,event):
        entity=self.nearest(event)
        if entity:self.hover_text.set(f"{display_name(entity)} · {voice_text(entity.get('voice'))} · 高{height_text(entity)} 重{pct_text(entity.get('weight_pct'))} · {location_text(entity)}")
        else:self.tab_hint()

    def select_point(self,event):
        entity=self.nearest(event)
        if not entity:return
        cards=self.favorite_tree if favorite_tags(entity) else self.tree
        self.tabs.select(cards.frame);cards.select_key(row_key(entity))


