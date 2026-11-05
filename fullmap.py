"""Whole-map coverage viewer; independent pan/zoom never changes radar or candidates."""
import json
import math
from pathlib import Path
from mapview import MapRenderer
from presentation import project
from radar import FlowBar
import theme


class FullMapWindow:
    def __init__(self,root,provider,settings_path=None):
        import tkinter as tk
        from tkinter import ttk
        self.root=root;self.provider=provider;self.renderer=MapRenderer()
        self.settings_path=Path(settings_path) if settings_path else None
        saved={}
        if self.settings_path:
            try:saved=json.loads(self.settings_path.read_text('utf-8'))
            except (OSError,ValueError):pass
        if not isinstance(saved,dict):saved={}
        def bounded(key,default,low,high):
            try:
                value=float(saved.get(key,default))
                return max(low,min(high,value)) if math.isfinite(value) else default
            except (ValueError,TypeError):return default
        self.scene='10003';self.center=None;self.zoom=1;self.dataset=None;self.value=None
        self.photo=None;self.frame=None;self.timer=self.render_timer=None;self.drag=None
        self.window=tk.Toplevel(root);self.window.withdraw();self.window.title('RoCo · 完整地图')
        w=int(bounded('width',900,420,max(420,root.winfo_screenwidth()-60)))
        h=int(bounded('height',820,350,max(350,root.winfo_screenheight()-90)))
        self.window.geometry(f'{w}x{h}');self.window.minsize(420,350)
        self.window.configure(bg=theme.SURFACE);self.window.protocol('WM_DELETE_WINDOW',self.hide)
        self.title=tk.StringVar(value='完整地图')
        ttk.Label(self.window,textvariable=self.title,font=(theme.FONT,12,'bold'),padding=(12,8)).pack(fill='x')
        self.bar=FlowBar(self.window)
        group=self.bar.group()
        ttk.Button(group,text='全图',command=self.fit).pack(side='left')
        ttk.Button(group,text='−',width=3,command=lambda:self.change_zoom(1/1.3)).pack(side='left')
        ttk.Button(group,text='+',width=3,command=lambda:self.change_zoom(1.3)).pack(side='left')
        ttk.Button(group,text='定位我',command=self.locate).pack(side='left')
        group=self.bar.group()
        ttk.Label(group,text='涂色对比').pack(side='left')
        self.strength=tk.DoubleVar(value=bounded('strength',70,20,90))
        ttk.Scale(group,from_=20,to=90,length=120,variable=self.strength,command=lambda _:self.queue_render()).pack(side='left')
        self.strength_text=tk.StringVar();ttk.Label(group,textvariable=self.strength_text,width=5).pack(side='left')
        self.canvas=tk.Canvas(self.window,bg='#0f172a',highlightthickness=0,cursor='fleur',width=1,height=1)
        self.canvas.pack(fill='both',expand=True,padx=8)
        self.canvas.bind('<Configure>',lambda _:self.queue_render())
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<ButtonPress-1>',self.start_drag)
        self.canvas.bind('<B1-Motion>',self.move_drag)
        self.canvas.bind('<ButtonRelease-1>',lambda _:setattr(self,'drag',None))
        self.canvas.bind('<Double-Button-1>',lambda _:self.fit())
        self.note=tk.StringVar(value='紫色：已记录的100米覆盖 · 未涂色：尚未记录 · 滚轮缩放，按住拖动')
        self.footer=ttk.Label(self.window,textvariable=self.note,padding=(12,8),wraplength=w-24)
        self.footer.pack(fill='x')
        self.window.bind('<Configure>',lambda e:self.footer.configure(wraplength=max(100,e.width-24)) if e.widget is self.window else None)
        self.window.bind('<Destroy>',self.destroyed)
        self.timer=root.after(1000,self.tick)

    def config(self):return self.renderer.maps.get(str(self.scene))

    def radius(self):return self.config()['side_units']/200/self.zoom

    def size(self):return max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())

    def reset_center(self):
        config=self.config()
        if config:self.center={'x':config['origin_x']+config['side_units']/2,'y':config['origin_y']+config['side_units']/2}

    def show(self):
        self.window.deiconify();self.window.lift();self.fit()

    def hide(self):self.save_settings();self.window.withdraw()

    def fit(self):
        self.zoom=1;self.reset_center();self.refresh()

    def refresh(self):
        if not self.window.winfo_exists() or self.window.state()=='withdrawn':return
        try:
            value=self.provider(self.scene)
        except Exception as error:
            self.canvas.delete('all');self.photo=None;self.frame=None
            self.note.set('暂时无法读取当前存档：'+str(error));return
        dataset=(value.get('archive_id'),value.get('profile'),str(self.scene))
        if dataset!=self.dataset:self.zoom=1;self.reset_center();self.dataset=dataset
        self.value=value;self.render()

    def render(self):
        if self.render_timer is not None:self.root.after_cancel(self.render_timer);self.render_timer=None
        if not self.value or not self.center:return
        width,height=self.size()
        if width<10 or height<10:return
        frame,status=self.renderer.render(self.scene,self.center,self.radius(),width,height,20)
        c=self.canvas;c.delete('all')
        if frame is None:
            self.photo=None;self.frame=None
            c.create_text(width/2,height/2,text=status,fill='#ffffff',font=(theme.FONT,11));return
        strength=round(self.strength.get());self.strength_text.set(f'{strength}%')
        frame=self.renderer.paint(frame,self.scene,self.center,self.radius(),width,height,20,
            self.value.get('exploration') or {},opacity=round(strength/100*255))
        from PIL import ImageTk
        if frame is not self.frame:self.photo=ImageTk.PhotoImage(frame,master=self.window);self.frame=frame
        c.create_image(0,0,image=self.photo,anchor='nw',tags='full_map_background')
        pos=self.value.get('player_position') or self.value.get('historical_position')
        if pos:
            x,y,_=project((pos['x']-self.center['x'])/100,(pos['y']-self.center['y'])/100,
                self.radius(),width,height,20)
            if 0<=x<=width and 0<=y<=height:
                c.create_oval(x-5,y-5,x+5,y+5,fill='#ffffff',outline='#1c1b1f',width=2,tags='player_marker')
                label='我' if self.value.get('player_position') else '历史位置'
                c.create_text(x+9,y,text=label,fill='#ffffff',anchor='w',font=(theme.FONT,10,'bold'))
        c.create_text(14,16,text='北 ↑',fill='#ffffff',anchor='w',font=(theme.FONT,10,'bold'))
        self.title.set(f"{self.value.get('archive_name','')} · {status}")
        self.window.title('RoCo · '+self.title.get()+' · 完整地图')
        self.note.set(f"紫色：已记录100米覆盖 · 未涂色：尚未记录 · {self.zoom:g}× · 滚轮缩放，按住拖动，双击或点“全图”复位")

    def queue_render(self):
        if self.render_timer is None:self.render_timer=self.root.after(40,self.render)

    def units_per_pixel(self):
        width,height=self.size();r=max(1,min(width,height)/2-20)
        return self.radius()*100/r

    def clamp(self):
        config=self.config()
        for axis,key in (('x','origin_x'),('y','origin_y')):
            self.center[axis]=min(config[key]+config['side_units'],max(config[key],self.center[axis]))

    def change_zoom(self,factor,anchor=None):
        if not self.center or not self.config():return
        width,height=self.size();anchor=anchor or (width/2,height/2)
        before=self.units_per_pixel();old=self.zoom;self.zoom=min(16,max(.5,self.zoom*factor))
        if old==self.zoom:return
        after=self.units_per_pixel()
        self.center['x']+=(anchor[0]-width/2)*(before-after)
        self.center['y']+=(anchor[1]-height/2)*(before-after)
        self.clamp();self.render()

    def wheel(self,event):
        if event.delta:self.change_zoom(1.3 if event.delta>0 else 1/1.3,(event.x,event.y))
        return 'break'

    def start_drag(self,event):
        if self.center:self.drag=(event.x,event.y,dict(self.center))

    def move_drag(self,event):
        if not self.drag:return
        x,y,center=self.drag;units=self.units_per_pixel()
        self.center={'x':center['x']-(event.x-x)*units,'y':center['y']-(event.y-y)*units}
        self.clamp();self.queue_render()

    def locate(self):
        if not self.value:return
        pos=self.value.get('player_position') or self.value.get('historical_position')
        if pos:self.center=dict(pos);self.zoom=max(4,self.zoom);self.clamp();self.render()

    def tick(self):
        self.refresh();self.timer=self.root.after(1000,self.tick)

    def save_settings(self):
        if not self.settings_path:return
        try:
            self.settings_path.parent.mkdir(parents=True,exist_ok=True)
            self.settings_path.write_text(json.dumps({'width':self.window.winfo_width(),
                'height':self.window.winfo_height(),'strength':self.strength.get()},indent=2),encoding='utf-8')
        except OSError:pass

    def destroyed(self,event):
        if event.widget is self.window:
            for timer in (self.timer,self.render_timer):
                if timer is not None:
                    try:self.root.after_cancel(timer)
                    except Exception:pass
