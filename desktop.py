"""Simple desktop surface. Packet details stay in the engine and CLI."""
import json
import math
from pathlib import Path
import queue
import re
import threading
import uuid
from datetime import datetime

from rareview import RareFilter as FilterSpec,view as rare_view,prepare
from presentation import build_view,radius_value,display_subset,radar_cache
from preferences import Preferences,preferred_device
from radar import RadarWindow,fill_tree,setup_tree,show_color_legend
from battle_view import BattlePanel
from storage import Store,read_map_data
import theme
from archives import Archives
from backup import export_world,import_worlds,inspect_bundle,import_legacy_directory

PROJECT_URL='https://github.com/Vicuna1277/ROCO-Nearby-Pet'


class Desktop:
    def __init__(self,api):
        import tkinter as tk
        from tkinter import ttk
        theme.enable_high_dpi()
        self.api=api;self.root=tk.Tk();self.style=theme.apply(self.root)
        self.root.title('ROCO附近精灵 · 0.19.2')
        self.prefs=Preferences(api.BASE/'data/app_settings.json');saved=self.prefs.load()
        if not saved:
            try:
                legacy=json.loads((api.BASE/'data/display_settings.json').read_text(encoding='utf-8-sig'))
                if isinstance(legacy,dict):saved['radius_m']=legacy.get('radius_m',200)
            except (OSError,ValueError):pass
        geometry=saved.get('geometry','920x700')
        if not isinstance(geometry,str) or not re.fullmatch(r'\d{3,4}x\d{3,4}',geometry):geometry='920x700'
        width,height=map(int,geometry.split('x'))
        width=max(680,min(width,self.root.winfo_screenwidth()-40));height=max(600,min(height,self.root.winfo_screenheight()-80))
        self.root.geometry(f'{width}x{height}');self.root.minsize(680,600)
        self.job=None;self.snapshot=None;self.source_snapshot=None;self.revision=-1;self.radar=None
        self.devices=[];self.device_queue=queue.Queue();self.device_refreshing=False
        self.filter_timer=self.save_timer=None;self.closed=False;self.last_error=None
        self.clearing=False;self.clear_timer=None
        self.switching=False;self.switch_timer=None
        self.transferring=False;self.transfer_queue=queue.Queue();self.transfer_timer=None;self.transfer_done=None
        self.full_map=None
        try:self.applied_filter=FilterSpec.build(**saved.get('filter',{}))
        except (ValueError,TypeError,AttributeError):self.applied_filter=FilterSpec()
        try:self.applied_radius=radius_value(saved.get('radius_m',200))
        except ValueError:self.applied_radius=200
        def string(key,default=''):
            value=saved.get(key,default)
            return tk.StringVar(value=value if isinstance(value,str) else default)
        self.mode=string('mode','live')
        if self.mode.get() not in ('live','offline'):self.mode.set('live')
        self.path=string('file');self.database=string('database',str(api.DEFAULT_DB))
        self.archives=Archives(api.BASE,self.database.get())
        self.database.set(str(self.archives.database()))
        self.archive_name=tk.StringVar(value=self.archives.get(self.archives.current)['name'])
        self.last_backup_dir=saved.get('backup_directory','')
        self.profile=string('profile');self.device_id=string('device_id')
        self.radius=tk.StringVar(value=f'{self.applied_radius:g}')
        self.record=tk.BooleanVar(value=saved.get('record') is True)
        self.speed=string('speed','不限速')
        if self.speed.get() not in ('不限速','1 倍','10 倍'):self.speed.set('不限速')
        self.query=tk.StringVar(value=self.applied_filter.query)
        self.checks={key:tk.BooleanVar(value=getattr(self.applied_filter,key)) for key in ('full','melodic','hoarse','large','small')}
        self.status=tk.StringVar(value='选择网卡，开始发现附近精灵。')
        self.summary=tk.StringVar(value='候选 0    收藏 0    已捕获 0')
        self.filter_status=tk.StringVar(value='声音组内取并集，体型组内取并集，两组取交集；最近20条更新距离。')
        self.filters_open=saved.get('filters_open',True) is not False
        self._build()
        for variable in (self.query,*self.checks.values()):variable.trace_add('write',self.schedule_filter)
        for variable in (self.mode,self.path,self.database,self.profile,self.device_id,self.radius,self.record,self.speed):
            variable.trace_add('write',self.schedule_save)
        self.mode.trace_add('write',lambda *_:self.change_mode())
        self.change_mode();self.update_custom_fields();self.layout_filters()
        self.root.bind('<Configure>',self.window_resized)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.poll_timer=self.root.after(200,self.poll)
        self.root.after(100,self.refresh_devices)
        self.history_timer=self.root.after(50,self.load_history)

    def _build(self):
        from tkinter import ttk,font
        height_width=max(85,font.Font(self.root,font=(theme.FONT,11)).measure('100% 大块头')+18)
        self.outer=ttk.Frame(self.root,padding=12);self.outer.pack(fill='both',expand=True)
        header=ttk.Frame(self.outer);header.pack(fill='x',pady=(0,8))
        ttk.Label(header,text='ROCO附近精灵',font=('Segoe UI',18,'bold'),foreground=theme.PRIMARY).pack(side='left')
        ttk.Label(header,text='',font=('Microsoft YaHei UI',12),foreground=theme.MUTED).pack(side='left',padx=12)
        ttk.Button(header,text='设置',command=self.settings).pack(side='right')
        ttk.Button(header,text='数据',command=self.data_menu).pack(side='right',padx=6)
        ttk.Button(header,text='悬浮雷达',command=self.show_radar).pack(side='right',padx=6)
        archive_row=ttk.Frame(self.outer);archive_row.pack(fill='x',pady=(0,8))
        archive_row.columnconfigure(1,weight=1)
        ttk.Label(archive_row,text='世界',font=(theme.FONT,10,'bold')).grid(row=0,column=0,padx=(0,8))
        self.archive_combo=ttk.Combobox(archive_row,textvariable=self.archive_name,state='readonly',
            values=[e['name'] for e in self.archives.worlds()],width=12)
        self.archive_combo.grid(row=0,column=1,sticky='ew')
        self.archive_combo.bind('<<ComboboxSelected>>',self.select_archive)
        ttk.Button(archive_row,text='新建',command=self.new_archive).grid(row=0,column=2,padx=4)
        ttk.Button(archive_row,text='改名',command=self.rename_archive).grid(row=0,column=3,padx=(0,4))
        ttk.Button(archive_row,text='导入',command=self.import_world_ui).grid(row=0,column=4,padx=(0,4))
        ttk.Button(archive_row,text='导出此世界',command=self.export_world_ui).grid(row=0,column=5)
        source=ttk.Frame(self.outer,padding=10,style='Card.TFrame');source.pack(fill='x')
        modes=ttk.Frame(source,style='Card.TFrame');modes.pack(fill='x',pady=(0,8))
        ttk.Radiobutton(modes,text='实时采集',variable=self.mode,value='live').pack(side='left')
        ttk.Radiobutton(modes,text='离线抓包',variable=self.mode,value='offline').pack(side='left',padx=14)
        self.live_row=ttk.Frame(source,style='Card.TFrame')
        self.live_row.columnconfigure(1,weight=1)
        ttk.Label(self.live_row,text='网卡',style='Card.TLabel').grid(row=0,column=0,padx=(0,10))
        self.device_combo=ttk.Combobox(self.live_row,state='readonly',width=20)
        self.device_combo.grid(row=0,column=1,sticky='ew')
        self.device_combo.bind('<<ComboboxSelected>>',self.choose_device)
        self.refresh_button=ttk.Button(self.live_row,text='刷新',command=self.refresh_devices)
        self.refresh_button.grid(row=0,column=2,padx=(8,0))
        self.offline_row=ttk.Frame(source,style='Card.TFrame');self.offline_row.columnconfigure(0,weight=1)
        ttk.Entry(self.offline_row,textvariable=self.path).grid(row=0,column=0,sticky='ew')
        ttk.Button(self.offline_row,text='选择文件',command=self.choose_file).grid(row=0,column=1,padx=(8,0))
        actions=ttk.Frame(self.outer);actions.pack(fill='x',pady=8)
        self.start_button=ttk.Button(actions,text='开始采集',style='Primary.TButton',command=self.start)
        self.start_button.pack(side='left')
        self.stop_button=ttk.Button(actions,text='停止',command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=6)
        self.pause_button=ttk.Button(actions,text='暂停',command=self.pause)
        self.range_entry=ttk.Entry(actions,textvariable=self.radius,width=7)
        self.range_entry.pack(side='right');self.range_entry.bind('<Return>',lambda _:self.apply_range())
        ttk.Button(actions,text='应用距离',command=self.apply_range).pack(side='right',padx=5)
        ttk.Label(actions,text='候选半径 / 米').pack(side='right',padx=7)
        filter_header=ttk.Frame(self.outer);self.filter_header=filter_header;filter_header.pack(fill='x',pady=(0,6))
        ttk.Label(filter_header,text='筛选',font=('Microsoft YaHei UI',11,'bold')).pack(side='left')
        ttk.Button(filter_header,text='清除',command=self.clear_filter).pack(side='right')
        self.filter_toggle=ttk.Button(filter_header,text='收起',command=self.toggle_filters)
        self.filter_toggle.pack(side='right',padx=6)
        self.filters=ttk.Frame(self.outer,padding=8,style='Card.TFrame');self.filters.pack(fill='x')
        self.filters.columnconfigure(1,weight=1);self.filters.columnconfigure(3,weight=1)
        ttk.Label(self.filters,text='名称',style='Card.TLabel').grid(row=0,column=0,sticky='w',padx=(0,6))
        ttk.Entry(self.filters,textvariable=self.query,width=13).grid(row=0,column=1,sticky='ew',padx=(0,14))
        checks=ttk.Frame(self.filters);checks.grid(row=1,column=0,columnspan=4,sticky='w',pady=6)
        ttk.Label(checks,text='声音（或）').grid(row=0,column=0,sticky='w')
        ttk.Label(checks,text='体型（或）').grid(row=1,column=0,sticky='w')
        for i,(key,label) in enumerate((('full','满声 ±100'),('melodic','婉转声'),('hoarse','粗嗓门'),('large','大块头'),('small','小不点'))):
            ttk.Checkbutton(checks,text=label,variable=self.checks[key]).grid(row=i//3,column=i%3+1,sticky='w',padx=5,pady=3)
        self.filter_status_label=ttk.Label(self.outer,textvariable=self.filter_status,foreground=theme.MUTED,wraplength=600)
        self.filter_status_label.pack(fill='x',pady=(4,6))
        stats=ttk.Frame(self.outer);self.stats=stats;stats.pack(fill='x',pady=(0,4))
        ttk.Label(stats,textvariable=self.summary,font=('Microsoft YaHei UI',11,'bold')).pack(side='left')
        ttk.Button(stats,text='导出',command=self.export_menu).pack(side='right')
        ttk.Button(stats,text='历史记录',command=self.load_history).pack(side='right',padx=6)
        ttk.Button(stats,text='大地图',command=self.show_full_map).pack(side='right')
        ttk.Label(self.outer,textvariable=self.status,style='Muted.TLabel',wraplength=800).pack(side='bottom',fill='x',pady=(5,0))
        self.tabs=ttk.Notebook(self.outer);self.tabs.pack(fill='both',expand=True)
        self.trees={};self.table_rows={};self.table_pages={};self.table_controls={}
        for key,title in (('nearby','候选'),('favorites','收藏'),('ordinary_cache','记录')):
            frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=title)
            tree=ttk.Treeview(frame,columns=('name','voice','height','weight','location'),show='headings')
            for column,label,width in [('name','名称 / 标签',200),('voice','声音',90),('height','身高 %',height_width),('weight','体重 %',85),('location','距离 / 方位',135)]:
                tree.heading(column,text=label);tree.column(column,width=width,minwidth=55,stretch=column=='name')
            vertical=ttk.Scrollbar(frame,command=tree.yview)
            horizontal=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
            tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
            tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
            frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
            setup_tree(tree);self.trees[key]=tree
            controls=ttk.Frame(frame);controls.grid(row=2,column=0,columnspan=2,sticky='ew',pady=3)
            previous=ttk.Button(controls,text='‹ 上一页',command=lambda key=key:self.change_table_page(key,-1))
            previous.pack(side='left')
            import tkinter as tk
            label=tk.StringVar(value='1/1 · 每页100条')
            ttk.Label(controls,textvariable=label,anchor='center').pack(side='left',fill='x',expand=True)
            following=ttk.Button(controls,text='下一页 ›',command=lambda key=key:self.change_table_page(key,1))
            following.pack(side='right')
            self.table_rows[key]=[];self.table_pages[key]=0;self.table_controls[key]=(label,previous,following)
        self.battle_panel=BattlePanel(self.tabs)
        self.tabs.add(self.battle_panel.frame,text='战斗信息')
        self.tabs.bind('<<NotebookTabChanged>>',lambda _:self.render_table())

    def change_mode(self):
        live=self.mode.get()=='live'
        self.live_row.pack_forget();self.offline_row.pack_forget();self.pause_button.pack_forget()
        (self.live_row if live else self.offline_row).pack(fill='x')
        self.start_button.configure(text='开始采集' if live else '读取抓包')
        if not live:self.pause_button.pack(side='left',padx=6)

    def window_resized(self,event):
        if event.widget is not self.root:return
        # Native DPI text needs real space; narrow, short windows keep the list usable.
        threshold=min(760,round(440*self.root.winfo_fpixels('1i')/96))
        if 100<event.height<threshold and self.filters_open:
            self.filters_open=False;self.layout_filters()
        self.schedule_save()

    def refresh_archives(self):
        self.archive_combo['values']=[e['name'] for e in self.archives.worlds()]
        self.archive_name.set(self.archives.get(self.archives.current)['name'])


    def transfer(self,label,work,done):
        """Stop capture first, then keep ZIP and SQLite work off the GUI thread."""
        if self.clearing or self.switching or self.transferring:return
        self.transferring=True;self.transfer_done=done
        self.start_button.configure(state='disabled')
        self.archive_combo.configure(state='disabled')
        self.status.set(label+'，正在保存当前采集…')
        worker=self.job
        if worker and not worker.done:worker.stop()
        def launch():
            self.transfer_timer=None
            if self.closed:return
            if worker and not worker.done:self.transfer_timer=self.root.after(50,launch);return
            if worker and worker.last:self.render(worker.last)
            self.job=None;self.revision=-1;self.status.set(label+'…')
            def run():
                try:self.transfer_queue.put((work(),None))
                except Exception as error:self.transfer_queue.put((None,str(error)))
            threading.Thread(target=run,daemon=True).start()
        launch()

    def export_world_ui(self):
        from tkinter import filedialog
        if self.clearing or self.switching or self.transferring:return
        identifier=self.archives.current;name=self.archives.get(identifier)['name']
        filename=re.sub(r'[<>:"/\\|?*]','_',name).strip(' .') or 'RoCo世界'
        path=filedialog.asksaveasfilename(parent=self.root,title='导出当前世界：精灵缓存、收藏和地图涂色',
            initialdir=str(self.api.BASE),
            initialfile=filename+'.rocosave',defaultextension='.rocosave',
            filetypes=[('RoCo 世界存档包','*.rocosave')])
        if not path:return
        self.last_backup_dir=str(Path(path).parent);self.schedule_save()
        self.transfer('导出世界',lambda:export_world(self.archives,path,identifier),
            lambda result:self.status.set(f'已导出世界「{name}」的缓存、收藏和涂色：{path}'))

    def import_world_ui(self):
        from tkinter import filedialog,simpledialog,messagebox
        if self.clearing or self.switching or self.transferring:return
        path=filedialog.askopenfilename(parent=self.root,title='导入 RoCo 世界存档包',
            initialdir=self.last_backup_dir or None,filetypes=[('RoCo 存档包','*.rocosave')])
        if not path:return
        try:
            manifest=inspect_bundle(path);name=None
            if len(manifest['worlds'])==1:
                used={entry['name'].casefold() for entry in self.archives.entries}
                suggestion=self.archives.unique_name(manifest['worlds'][0]['name'],used)
                name=simpledialog.askstring('导入世界','导入为新的世界名称，保留已有世界数据：',
                    initialvalue=suggestion,parent=self.root)
                if name is None:return
                name=self.archives.validate_name(name)
        except (ValueError,OSError) as error:messagebox.showerror('导入世界',str(error),parent=self.root);return
        self.last_backup_dir=str(Path(path).parent);self.schedule_save()
        def loaded(identifier):
            self.select_archive(identifier=identifier);self.refresh_archives()
            legacy='旧版账号分组名已忽略；' if manifest.get('format')=='roco-account-save' else ''
            self.status.set(legacy+f'已恢复 {len(manifest["worlds"])} 个独立世界及其精灵记录和涂色。')
        self.transfer('导入世界',lambda:import_worlds(self.archives,path,name),loaded)

    def import_legacy_ui(self):
        from tkinter import filedialog
        if self.clearing or self.switching or self.transferring:return
        path=filedialog.askdirectory(parent=self.root,title='选择旧版 data 文件夹或程序目录（请先关闭旧版）')
        if not path:return
        def loaded(identifier):
            self.select_archive(identifier=identifier);self.refresh_archives()
            self.status.set('已从旧版目录恢复独立世界，同名已自动区分；原目录未改动。')
        self.transfer('导入旧版数据',lambda:import_legacy_directory(self.archives,path),loaded)

    def map_data(self,scene):
        value=self.snapshot or {}
        profile,exploration,historical=read_map_data(self.database.get(),value.get('profile') or self.profile.get(),scene)
        exploration=dict(exploration,scope=self.archives.current)
        player=value.get('player_position') if str(value.get('scene'))==str(scene) else None
        return dict(scene=scene,archive_id=self.archives.current,archive_name=self.archives.get(self.archives.current)['name'],
            profile=profile,player_position=player,
            historical_position=historical,exploration=exploration)

    def show_full_map(self):
        from fullmap import FullMapWindow
        if self.full_map is None:
            self.full_map=FullMapWindow(self.root,self.map_data,settings_path=self.api.BASE/'data/fullmap_settings.json')
        self.full_map.show()

    def select_archive(self,_event=None,identifier=None):
        if self.clearing or self.switching or self.transferring:self.refresh_archives();return
        if identifier is None:
            identifier=next(e['id'] for e in self.archives.worlds() if e['name']==self.archive_name.get())
        if identifier==self.archives.current:return
        self.switching=True;self.start_button.configure(state='disabled')
        self.archive_combo.configure(state='disabled')
        worker=self.job
        live_switch=bool(worker and not worker.done and worker.config['mode']=='live')
        if live_switch:worker.request_world(str(self.archives.database(identifier)),identifier)
        elif worker and not worker.done:worker.stop()
        def finish():
            from tkinter import messagebox
            self.switch_timer=None
            if self.closed:return
            if live_switch and worker.world_error:
                self.switching=False;self.archive_combo.configure(state='readonly');self.start_button.configure(state='disabled')
                messagebox.showerror('切换失败',worker.world_error,parent=self.root);return
            if live_switch and worker.world_id!=identifier:
                if worker.done:
                    self.switching=False;self.archive_combo.configure(state='readonly');self.start_button.configure(state='normal');return
                self.status.set('保持抓包连接，正在切换世界…');self.switch_timer=self.root.after(50,finish);return
            if not live_switch and worker and not worker.done:
                self.status.set('正在保存当前存档，随后切换…')
                self.switch_timer=self.root.after(50,finish);return
            try:
                self.archives.select(identifier)
                self.database.set(str(self.archives.database()))
                self.revision=-1;self.snapshot=None;self.source_snapshot=None
                if live_switch:self.render(worker.last)
                else:self.job=None;self.load_history()
                self.refresh_archives();self.save_preferences()
                if self.radar:self.radar.render()
                if self.full_map:self.full_map.refresh()
                self.status.set(f'已切换到「{self.archive_name.get()}」；'+('保持抓包连接，暂停读入3秒后自动恢复。' if live_switch else '点击开始采集。'))
            except Exception as error:messagebox.showerror('切换存档',str(error),parent=self.root)
            finally:
                self.switching=False;self.archive_combo.configure(state='readonly')
                self.start_button.configure(state='disabled' if live_switch else 'normal');self.stop_button.configure(state='normal' if live_switch else 'disabled')
        finish()

    def new_archive(self):
        from tkinter import simpledialog,messagebox
        if self.clearing or self.switching or self.transferring:return
        name=simpledialog.askstring('新建存档','为这个世界或本批次命名：',parent=self.root)
        if name is None:return
        try:self.select_archive(identifier=self.archives.create(name));self.refresh_archives()
        except (ValueError,OSError) as error:messagebox.showerror('新建存档',str(error),parent=self.root)

    def rename_archive(self):
        from tkinter import simpledialog,messagebox
        if self.clearing or self.switching or self.transferring:return
        name=simpledialog.askstring('存档改名','存档名称：',initialvalue=self.archive_name.get(),parent=self.root)
        if name is None:return
        try:
            self.archives.rename(self.archives.current,name);self.refresh_archives()
            if self.source_snapshot:self.render(self.source_snapshot)
            if self.radar:self.radar.render()
            if self.full_map:self.full_map.refresh()
        except (ValueError,OSError) as error:messagebox.showerror('存档改名',str(error),parent=self.root)

    def layout_filters(self):
        self.filters.pack_forget();self.filter_status_label.pack_forget()
        if self.filters_open:
            self.filters.pack(fill='x',after=self.filter_header)
            self.filter_status_label.pack(fill='x',pady=(4,6),after=self.filters)
        self.filter_toggle.configure(text='收起' if self.filters_open else '展开')

    def toggle_filters(self):
        self.filters_open=not self.filters_open;self.layout_filters();self.schedule_save()

    def choose_file(self):
        from tkinter import filedialog
        value=filedialog.askopenfilename(parent=self.root,filetypes=[('抓包文件','*.pcap *.cap'),('所有文件','*.*')])
        if value:self.path.set(value)

    def refresh_devices(self):
        if self.device_refreshing:return
        self.device_refreshing=True;self.refresh_button.configure(state='disabled')
        def read():
            try:self.device_queue.put((self.api.Npcap().devices(),None))
            except Exception as error:self.device_queue.put(([],str(error)))
        threading.Thread(target=read,daemon=True).start()

    def choose_device(self,_event=None):
        index=self.device_combo.current()
        if 0<=index<len(self.devices):self.device_id.set(self.devices[index]['name'])

    def update_custom_fields(self):pass

    def schedule_filter(self,*_args):
        if self.filter_timer is not None:self.root.after_cancel(self.filter_timer)
        self.filter_timer=self.root.after(200,self.apply_filter)

    def apply_filter(self):
        if self.filter_timer is not None:self.root.after_cancel(self.filter_timer);self.filter_timer=None
        self.applied_filter=FilterSpec.build(query=self.query.get(),**{k:v.get() for k,v in self.checks.items()})
        self.filter_status.set('声音组内取并集，体型组内取并集，两组与名称取交集。不勾选显示全部稀有类型；最近20条更新距离。')
        if self.source_snapshot:self.render(self.source_snapshot)
        if self.radar:self.radar.render()
        self.schedule_save();return True

    def clear_filter(self):
        self.query.set('')
        for variable in self.checks.values():variable.set(False)
        self.apply_filter()

    def apply_range(self):
        from tkinter import messagebox
        try:self.applied_radius=radius_value(self.radius.get())
        except ValueError as error:messagebox.showerror('候选距离',str(error),parent=self.root);return False
        if self.source_snapshot:self.render(self.source_snapshot)
        if self.radar:self.radar.render()
        self.schedule_save();return True

    def schedule_save(self,*_args):
        if self.closed:return
        if self.save_timer is not None:self.root.after_cancel(self.save_timer)
        self.save_timer=self.root.after(350,self.save_preferences)

    def save_preferences(self):
        if self.save_timer is not None:self.root.after_cancel(self.save_timer)
        self.save_timer=None
        try:radius=radius_value(self.radius.get())
        except ValueError:radius=self.applied_radius
        settings=dict(mode=self.mode.get(),file=self.path.get(),database=self.database.get(),profile=self.profile.get(),
            device_id=self.device_id.get(),radius_m=radius,record=self.record.get(),speed=self.speed.get(),
            filter=self.applied_filter.to_dict(),filters_open=self.filters_open,
            geometry=f'{self.root.winfo_width()}x{self.root.winfo_height()}',backup_directory=self.last_backup_dir)
        try:self.prefs.save(settings)
        except OSError:self.status.set('设置暂时无法保存，请检查程序目录是否可写。')

    def start(self):
        from tkinter import messagebox
        if self.clearing or self.switching or self.transferring:return
        if self.job and not self.job.done:return
        if not self.apply_range() or not self.apply_filter():return
        config=dict(mode=self.mode.get(),db=self.database.get(),profile=self.profile.get(),port=8195,
                    spatial_1044=True,radius_m=self.applied_radius,gui=True,world_id=self.archives.current)
        try:
            if config['mode']=='offline':
                if not Path(self.path.get()).is_file():raise ValueError('请先选择一个有效的抓包文件。')
                config.update(file=self.path.get(),speed={'不限速':0,'1 倍':1,'10 倍':10}[self.speed.get()])
            else:
                index=self.device_combo.current()
                if index<0 or index>=len(self.devices):raise ValueError('请先选择当前使用的网卡。')
                config['device']=self.devices[index]['name'];self.device_id.set(config['device'])
                if self.record.get():
                    folder=self.api.BASE;folder.mkdir(parents=True,exist_ok=True)
                    config['record']=str(folder/(datetime.now().strftime('roco-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]+'.pcap'))
            self.save_preferences();self.job=self.api.Job(config);self.revision=-1;self.last_error=None
            self.start_button.configure(state='disabled');self.stop_button.configure(state='normal')
            self.job.start();self.show_radar()
        except Exception as error:messagebox.showerror('暂时无法开始',str(error),parent=self.root)

    def stop(self):
        if self.job:self.job.stop()
    def pause(self):
        if self.job and self.job.config['mode']=='offline' and not self.job.done:self.job.pause()

    def render(self,value):
        self.source_snapshot=value
        self.snapshot=rare_view(dict(value,archive_name=self.archives.get(self.archives.current)['name'],
            exploration=dict(value.get('exploration') or {},scope=self.archives.current)),self.applied_filter,self.applied_radius)
        self.redraw()

    def redraw(self):
        if not self.snapshot:return
        battle=self.snapshot.get('battle') or {}
        self.battle_panel.set_data(battle)
        self.tabs.tab(self.battle_panel.frame,text=f"战斗信息 {len(battle.get('enemies',[])) if battle.get('active') else 0}")
        counts={}
        for index,(key,tree) in enumerate(self.trees.items()):
            rows=self.snapshot.get(key,[])
            if key=='ordinary_cache':rows=self.applied_filter.apply(rows)
            elif key!='favorites':rows=self.applied_filter.apply(rows)
            self.table_rows[key]=rows;counts[key]=len(rows)
            self.tabs.tab(index,text=f"{ {'nearby':'候选','favorites':'收藏','ordinary_cache':'记录'}[key]} {len(rows)}")
        self.summary.set(f"候选 {counts['nearby']}    收藏 {counts['favorites']}    已捕获 {self.snapshot['summary'].get('captured_entities_in_cache',0)}")
        self.render_table()

    def render_table(self):
        selected=self.tabs.select()
        if not selected:return
        if selected==str(self.battle_panel.frame):return
        key=list(self.trees)[self.tabs.index(selected)];rows=self.table_rows[key]
        pages=max(1,math.ceil(len(rows)/100));page=min(max(0,self.table_pages[key]),pages-1)
        self.table_pages[key]=page
        label,previous,following=self.table_controls[key]
        label.set(f'{page+1}/{pages} · 共{len(rows)}条 · 每页100条')
        previous.configure(state='disabled' if page==0 else 'normal')
        following.configure(state='disabled' if page==pages-1 else 'normal')
        fill_tree(self.trees[key],rows[page*100:(page+1)*100])

    def change_table_page(self,key,step):
        self.table_pages[key]+=step;self.render_table();self.trees[key].yview_moveto(0)

    def show_radar(self):
        if self.radar is None:
            self.radar=RadarWindow(self.root,lambda:(self.snapshot,self.applied_filter),self.radius,self.apply_range,
                settings_path=self.api.BASE/'data/radar_settings.json',data_menu=self.data_menu,full_map=self.show_full_map)
        self.radar.show()

    def load_history(self):
        from tkinter import messagebox
        if self.closed:return
        if self.job and not self.job.done:return
        try:
            store=Store(self.database.get(),rare_only=True)
            try:
                profiles=store.profiles();profile=self.profile.get()
                if not profile:profile='live-default' if 'live-default' in profiles else profiles[-1] if profiles else ''
                rows=store.rare_rows(profile) if profile else []
                last=store.exploration_last(profile) if profile else None
                scene=last['scene'] if last else None
                exploration=store.exploration(profile,scene) if profile else {}
            finally:store.close()
            self.render(dict(profile=profile,entities=rows,summary={},scene=scene,player_position=None,history_only=True,
                map_center=last['pos'] if last else None,exploration=exploration))
            self.status.set('已读取历史记录；开始采集后更新附近距离。')
        except Exception as error:messagebox.showerror('历史记录',str(error),parent=self.root)

    def data_menu(self):
        import tkinter as tk
        menu=tk.Menu(self.root,tearoff=False,font=(theme.FONT,10))
        worlds=tk.Menu(menu,tearoff=False,font=(theme.FONT,10))
        for entry in self.archives.worlds():
            worlds.add_radiobutton(label=entry['name'],variable=self.archive_name,value=entry['name'],
                command=lambda identifier=entry['id']:self.select_archive(identifier=identifier))
        menu.add_cascade(label='切换存档',menu=worlds)
        menu.add_command(label='新建存档…',command=self.new_archive)
        menu.add_command(label='当前存档改名…',command=self.rename_archive)
        menu.add_command(label='导出当前世界…',command=self.export_world_ui)
        menu.add_command(label='导入世界存档包…',command=self.import_world_ui)
        menu.add_command(label='导入旧版 data 文件夹…',command=self.import_legacy_ui)
        menu.add_command(label='列表颜色说明',command=lambda:show_color_legend(self.root))
        menu.add_separator()
        menu.add_command(label='清除精灵记录（保留收藏）…',command=lambda:self.clear_data('creatures'))
        menu.add_command(label='清除地图涂色…',command=lambda:self.clear_data('paint'))
        menu.add_separator()
        menu.add_command(label='一起清除（保留收藏）…',command=lambda:self.clear_data('both'))
        menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery())

    def clear_data(self,kind):
        from tkinter import messagebox
        if self.clearing or self.switching or self.transferring:return
        title={'creatures':'清除普通精灵记录','paint':'清除地图涂色','both':'清除精灵与涂色'}[kind]
        running=self.job is not None and not self.job.done
        text=f'存档「{self.archive_name.get()}」\n将清除当前存档内、所有场景的'+('涂色记录。' if kind=='paint' else
            '普通精灵记录（包括候选、缓存和普通捕获历史）。特殊收藏保留。')
        if kind=='both':text+='\n同时清除地图涂色。'
        if running:text+='\n会先停止当前采集或回放。'
        text+='\n每周四更新后可手动清除，不会自动定时删除。\n确认清除？'
        if not messagebox.askyesno(title,text,parent=self.root):return
        self.clearing=True
        database=self.job.config['db'] if running else self.database.get()
        if running:self.job.stop()
        def finish():
            self.clear_timer=None
            if self.closed:return
            if running and not self.job.done:
                self.clear_timer=self.root.after(50,finish);return
            try:
                value=(self.job.last if running else self.snapshot) or {}
                with_profile=value.get('profile') or self.profile.get()
                store=Store(database)
                try:
                    profiles=store.profiles()
                    profile=with_profile or ('live-default' if 'live-default' in profiles else profiles[-1] if profiles else '')
                    if not profile:raise ValueError('当前没有可清除的数据。')
                    scopes=set(profiles)|{profile}
                    removed=sum(store.clear_ordinary(scope) for scope in scopes) if kind in ('creatures','both') else 0
                    painted=sum(store.clear_exploration(scope) for scope in scopes) if kind in ('paint','both') else 0
                    rows=store.rows(profile)
                    exploration=store.exploration(profile,value.get('scene'))
                finally:store.close()
                # Retire the stopped worker snapshot so polling cannot restore deleted rows.
                self.job=None;self.revision=-1
                self.render(dict(value,profile=profile,entities=prepare(rows),exploration=exploration))
                if self.radar:self.radar.render()
                if self.full_map:self.full_map.refresh()
                self.status.set(f'清除完成：普通精灵 {removed} 条，涂色采样 {painted} 点；收藏已保留。')
                self.start_button.configure(state='normal');self.stop_button.configure(state='disabled')
            except Exception as error:messagebox.showerror(title,str(error),parent=self.root)
            finally:self.clearing=False
        finish()

    def export_menu(self):
        import tkinter as tk
        menu=tk.Menu(self.root,tearoff=False)
        for key,title in (('nearby','候选列表'),('favorites','收藏列表'),('ordinary_cache','历史缓存')):
            menu.add_command(label=title,command=lambda selected=key:self.export(selected))
        menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery())

    def export(self,key):
        from tkinter import filedialog,messagebox
        if not self.snapshot:return
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.csv',initialdir=str(self.api.BASE),initialfile='roco_'+key+'.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        rows=self.snapshot.get(key,[])
        if key!='favorites':rows=self.applied_filter.apply(rows)
        try:self.api.save_entities(path,rows);self.status.set('列表已导出。')
        except Exception as error:messagebox.showerror('导出',str(error),parent=self.root)

    def settings(self):
        import tkinter as tk
        from tkinter import ttk,filedialog
        window=tk.Toplevel(self.root);window.title('运行设置');window.geometry('520x420');window.minsize(400,400)
        window.configure(bg=theme.SURFACE)
        pane=ttk.Frame(window,padding=20);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text='数据与记录',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w',pady=(0,12))
        ttk.Label(pane,text='历史数据文件').pack(anchor='w')
        row=ttk.Frame(pane);row.pack(fill='x',pady=6)
        ttk.Entry(row,textvariable=self.database,state='readonly').pack(side='left',fill='x',expand=True)
        def select_db():
            from tkinter import messagebox
            if self.transferring or self.switching or self.clearing:return
            if self.job and not self.job.done:
                messagebox.showinfo('数据文件','请先停止采集，再选择历史数据文件。',parent=window);return
            path=filedialog.askopenfilename(parent=window,filetypes=[('历史数据','*.sqlite3'),('所有文件','*.*')])
            if path:
                try:
                    self.archives.repoint(self.archives.current,path);self.database.set(path)
                    self.job=None;self.load_history()
                    if self.radar:self.radar.render()
                except (ValueError,OSError) as error:messagebox.showerror('数据文件',str(error),parent=window)
        ttk.Button(row,text='选择',command=select_db).pack(side='right',padx=(7,0))
        ttk.Checkbutton(pane,text='采集时保留原始抓包文件',variable=self.record).pack(anchor='w',pady=5)
        row=ttk.Frame(pane);row.pack(fill='x',pady=7)
        ttk.Label(row,text='离线回放速度').pack(side='left',padx=(0,10))
        ttk.Combobox(row,textvariable=self.speed,values=['不限速','1 倍','10 倍'],state='readonly',width=10).pack(side='left')
        ttk.Label(pane,text='设置自动保存，下次打开继续使用。',style='Muted.TLabel').pack(anchor='w')
        ttk.Separator(pane).pack(fill='x',pady=(14,8))
        ttk.Label(pane,text='芝士奶龙 · 项目主页',font=(theme.FONT,10,'bold')).pack(anchor='w')
        link=ttk.Label(pane,text=PROJECT_URL,foreground=theme.PRIMARY,cursor='hand2',wraplength=460,takefocus=True)
        link.pack(anchor='w',pady=(4,0))
        project_status=tk.StringVar(value='用默认浏览器打开，或复制地址。')
        def open_project(event=None):
            from project_links import open_project_url
            from tkinter import messagebox
            try:
                open_project_url(PROJECT_URL)
                project_status.set('已请求系统浏览器打开项目主页。')
            except Exception as error:
                project_status.set('打开失败，可点击“复制地址”手动访问。')
                messagebox.showerror('无法打开项目主页',f'系统未能打开浏览器。\n可点击“复制地址”后粘贴到浏览器。\n\n{error}',parent=window)
        def copy_project():
            window.clipboard_clear();window.clipboard_append(PROJECT_URL)
            project_status.set('项目地址已复制。')
        link.bind('<Button-1>',open_project);link.bind('<Return>',open_project)
        actions=ttk.Frame(pane);actions.pack(fill='x',pady=6)
        ttk.Button(actions,text='打开项目主页',command=open_project).pack(side='left')
        ttk.Button(actions,text='复制地址',command=copy_project).pack(side='left',padx=8)
        ttk.Label(pane,textvariable=project_status,style='Muted.TLabel',wraplength=460).pack(anchor='w')

    def poll(self):
        from tkinter import messagebox
        if self.closed:return
        try:
            devices,error=self.device_queue.get_nowait()
            self.device_refreshing=False;self.refresh_button.configure(state='normal');self.devices=devices
            self.device_combo['values']=[device.get('description') or f'网卡 {i+1}' for i,device in enumerate(devices)]
            index=preferred_device(devices,self.device_id.get())
            if index>=0:self.device_combo.current(index);self.choose_device()
            else:
                self.device_combo.set('')
                if self.device_id.get():self.status.set('上次选择的网卡暂不可用，请重新选择。')
            if error and self.mode.get()=='live':self.status.set('无法读取网卡，请确认已安装 Npcap 后刷新。')
        except queue.Empty:pass
        if self.job:
            revision,value,text,error,done,paused=self.job.read()
            if not self.switching and revision!=self.revision and value and value.get('world_id',self.archives.current)==self.archives.current:self.render(value);self.revision=revision
            self.start_button.configure(state='normal' if done else 'disabled')
            self.stop_button.configure(state='disabled' if done else 'normal')
            self.pause_button.configure(text='继续' if paused else '暂停')
            if not error:
                self.status.set('已停止，数据已保存。' if done else '已暂停回放。' if paused else
                    '正在读取抓包文件…' if self.job.config['mode']=='offline' else '切换保护中，稍后自动恢复…' if value and value.get('switch_guard') else '正在采集 · 请保持游戏连接。')
            elif error!=self.last_error:
                self.last_error=error;self.status.set('处理未能完成，已读取的数据仍保留。')
                messagebox.showerror('处理提示',error,parent=self.root)
        try:
            result,error=self.transfer_queue.get_nowait()
            self.transferring=False
            self.archive_combo.configure(state='readonly')
            self.start_button.configure(state='normal');self.stop_button.configure(state='disabled')
            if error:
                self.status.set('世界存档操作未完成，原有存档已保留。')
                messagebox.showerror('世界存档',error,parent=self.root)
            elif self.transfer_done:self.transfer_done(result)
            self.transfer_done=None
        except queue.Empty:pass
        self.poll_timer=self.root.after(250,self.poll)

    def close(self):
        if self.transferring:
            self.status.set('正在完成世界存档，请稍候…');self.root.after(100,self.close);return
        self.apply_filter();self.save_preferences()
        if self.job and not self.job.done:
            self.job.stop();self.status.set('正在保存…');self.root.after(100,self.close);return
        if self.radar:self.radar.save_settings()
        if self.full_map:self.full_map.save_settings()
        self.closed=True
        for timer in (self.poll_timer,self.filter_timer,self.save_timer,self.history_timer,self.clear_timer,self.switch_timer,self.transfer_timer):
            if timer is not None:
                try:self.root.after_cancel(timer)
                except Exception:pass
        self.root.destroy()
