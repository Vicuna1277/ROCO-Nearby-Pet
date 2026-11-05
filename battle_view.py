"""Compact, wrapping current-enemy view shared by desktop and floating radar."""
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from body import pct_text
from presentation import voice_text
import theme


class BattlePanel:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent, padding=8)
        self.status = tk.StringVar(value='等待进入战斗')
        ttk.Label(self.frame, textvariable=self.status, foreground=theme.PRIMARY,
                  font=(theme.FONT, 10, 'bold'), wraplength=240).pack(fill='x', pady=(0, 6))
        body = ttk.Frame(self.frame)
        body.pack(fill='both', expand=True)
        self.text = tk.Text(body, width=1, height=3, wrap='char', relief='flat',
                            bg=theme.CARD, fg=theme.TEXT, padx=10, pady=8,
                            highlightthickness=1, highlightbackground=theme.OUTLINE,
                            font=(theme.FONT, 11), cursor='arrow', state='disabled')
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        ttk.Label(self.frame, text='性格：只适用于S5赛季\n当前敌方 · 不受附近筛选影响\n百分比按体型表换算；未知信息显示 —',
                  foreground=theme.MUTED, font=(theme.FONT, 9), wraplength=250).pack(fill='x', pady=(6, 0))
        self.last = None
        self.set_data(None)

    def set_data(self, data, scale=1):
        data = data or {}
        if self.last == (data, scale):
            return
        from copy import deepcopy
        self.last = (deepcopy(data), scale)
        enemies = data.get('enemies', []) if data.get('active') else []
        stamp = data.get('updated_ts')
        clock = datetime.fromtimestamp(stamp).strftime('%H:%M:%S') if stamp else ''
        self.status.set(f"{data.get('status', '等待进入战斗')}{' · '+clock if clock else ''}")
        size = max(8, round(11 * scale))
        self.text.configure(state='normal', font=(theme.FONT, size))
        self.text.delete('1.0', 'end')
        self.text.tag_configure('title', font=(theme.FONT, size + 1, 'bold'), spacing1=5, spacing3=4)
        self.text.tag_configure('detail', spacing3=12)
        for tag, bg, fg in (('normal', '#e8f2fb', '#193b54'), ('melodic', '#dcfce7', '#166534'),
                            ('hoarse', '#ffedd5', '#9a3412'), ('full_plus', '#166534', '#ffffff'),
                            ('full_minus', '#9a3412', '#ffffff')):
            self.text.tag_configure(tag, background=bg, foreground=fg)
        if not enemies:
            self.text.insert('end', '等待服务器发送当前敌方信息。\n进入战斗后自动更新，结束后清空。')
        for enemy in enemies:
            voice = enemy.get('voice')
            tag = ('full_plus' if voice == 100 else 'full_minus' if voice == -100 else
                   'melodic' if voice is not None and voice >= 96 else
                   'hoarse' if voice is not None and voice <= -96 else 'normal')
            name = enemy.get('name') or '未知精灵'
            level = enemy.get('level')
            self.text.insert('end', name + (f' · Lv.{level}' if level is not None else '') + '\n', ('title', tag))
            self.text.insert('end', ' ' + voice_text(voice) + ' \n', tag)
            self.text.insert('end', '性格：' + (enemy.get('nature_name') or '未知') + '\n')
            height, weight = enemy.get('height_m'), enemy.get('weight_kg')
            h = f'{height:.2f} m' if height is not None else '—'
            w = f'{weight:.2f} kg' if weight is not None else '—'
            body = f"身高 {h} / {pct_text(enemy.get('height_pct'))}\n体重 {w} / {pct_text(enemy.get('weight_pct'))}"
            extreme = '大块头' if enemy.get('height_pct') == 100 else '小不点' if enemy.get('height_pct') == 0 else ''
            if extreme:
                body += ' · ' + extreme
            blood = enemy.get('blood_name')
            if blood:
                body += '\n' + blood
            self.text.insert('end', body + '\n\n', 'detail')
        self.text.configure(state='disabled')
