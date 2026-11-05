"""RoCo nearby analyzer v0.17: grouped voice/body unions, twenty live targets."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from engine import Engine, VERSION
from sources import Npcap, PcapReader, PcapWriter, fingerprint
from storage import Store
from body import enrich
import protocol
from filters import FilterSpec, VOICE_OPTIONS, voice_label, filtered_report
from presentation import build_view,radius_value,favorite_tags
from radar import RadarWindow,fill_tree,setup_tree

BASE = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DEFAULT_DB = BASE / 'data' / 'nearby.sqlite3'


def save_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def save_entities(path, rows):
    columns = ['scene', 'identity', 'name', 'cfg_id', 'unique_id', 'refresh_id', 'actor_id', 'actor_type', 'scene_actor_id',
               'level', 'height', 'weight', 'height_pct', 'weight_pct', 'height_pct_status', 'weight_pct_status', 'size_form_id', 'mutation', 'voice', 'voice_label', 'glass_type', 'glass_value',
               'x', 'y', 'z', 'distance_m', 'observations', 'first_seen', 'last_seen',
               'direction', 'favorite_tags', 'captured', 'captured_at', 'capture_actor_id',
               'source_opcode', 'source_opcodes', 'source_branch', 'parser_version']
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, columns, extrasaction='ignore')
        writer.writeheader()
        for e in rows:
            row = enrich(e)
            row['voice_label'] = voice_label(e.get('voice'))
            row.update(e.get('pos') or {})
            if row.get('distance_m') is not None:
                row['distance_m'] = round(row['distance_m'], 3)
            writer.writerow(row)


def save_frames(path, rows):
    columns = ['ts', 'session', 'direction', 'command', 'sequence', 'opcode',
               'body_length', 'status', 'entity_count']
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def select_session_view(value, identifier):
    """Recalculate a completed report without retaining keys or reopening SQLite."""
    selected = next((s for s in value.get('sessions', []) if s['id'] == identifier), None)
    if not selected:
        return value
    result = dict(value)
    scene, player = selected['scene'], selected['player']
    seen = {tuple(item) for item in selected.get('observed_uids', [])}
    cache, current = [], []
    for original in value.get('entities', []):
        e = dict(original, distance_m=None, observed_this_session=False)
        if scene and e['scene'] == scene:
            e['observed_this_session'] = (scene, e.get('unique_id')) in seen
            if player and e.get('pos'):
                pos = e['pos']
                e['distance_m'] = math.hypot(pos['x']-player['x'], pos['y']-player['y'])/100
            current.append(e)
        cache.append(e)
    nearby = sorted([e for e in current if not e.get('captured') and e['distance_m'] is not None and e['distance_m'] <= 100],
                    key=lambda e: e['distance_m'])
    result.update(session=identifier, scene=scene, player_position=player,
                  player_position_ts=selected.get('player_ts'),
                  distance_reference_source='opcode_307' if player else None,
                  entities=cache, scene_entities=current, nearby=nearby)
    result['summary'] = dict(value['summary'], current_scene_entities=len(current), nearby_within_100m=len(nearby))
    return build_view(result,value.get('radius_m',100))


class Job:
    """All SQLite/engine/Npcap access remains on this worker thread."""
    def __init__(self, config):
        self.config = config
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.paused = False
        self.steps = 0
        self.selected_session = None
        self.packet_index = 0
        self.last = None
        self.revision = 0
        self.status = '准备中'
        self.error = None
        self.done = False
        self.thread = None
        self.last_publish = 0
        self.world_request=None;self.world_id=config.get('world_id');self.world_error=None;self.guard_until=0

    def request_world(self,database,identifier):
        with self.lock:self.world_request=(database,identifier);self.world_error=None

    def switch_world(self,engine):
        with self.lock:
            request=self.world_request;self.world_request=None
        if request:
            database,identifier=request
            try:new_store=Store(database,rare_only=True)
            except Exception as error:self.world_error=str(error);return
            engine.store.close();engine.store=new_store
            engine.source_id='live:'+uuid.uuid4().hex;engine.world_id=identifier
            engine.ingest_enabled=False;self.guard_until=time.monotonic()+3
            for session in engine.sessions.values():
                session.player=None;session.player_ts=None;session.seen.clear();session.pending_spatial.clear();session.pending_client.clear()
                session.battle.reset('已切换世界，等待战斗数据', session.last_ts)
            self.config['db']=database
            self.publish(engine,True)
            self.world_id=identifier
        engine.ingest_enabled=time.monotonic()>=self.guard_until

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def pause(self):
        with self.lock:
            self.paused = not self.paused
        self.wake.set()

    def step(self):
        with self.lock:
            self.paused = True
            self.steps += 1
        self.wake.set()

    def read(self):
        with self.lock:
            return self.revision, self.last, self.status, self.error, self.done, self.paused

    def publish(self, engine, force=False):
        now = time.monotonic()
        interval=.5 if self.config.get('gui') else .25
        if force or now - self.last_publish >= interval:
            engine.store.flush()
            value = engine.snapshot(self.selected_session)
            with self.lock:
                self.last = value
                self.revision += 1
            self.last_publish = now

    def gate(self, engine):
        while not self.stop_event.is_set():
            with self.lock:
                if not self.paused:
                    return 'run'
                if self.steps:
                    self.steps -= 1
                    return 'step'
            self.publish(engine)
            self.wake.wait(.05)
            self.wake.clear()
        return False

    def delay(self, seconds, engine):
        remaining = min(max(seconds, 0), 3600)
        while remaining > 0 and not self.stop_event.is_set():
            # Pause freezes the replay clock, and a pending step bypasses this delay.
            with self.lock:
                paused = self.paused
                stepped = self.steps > 0
            if paused:
                if stepped:
                    with self.lock:
                        self.steps -= 1
                    return
                self.publish(engine)
                self.wake.wait(.05)
                self.wake.clear()
                continue
            start = time.monotonic()
            self.wake.wait(min(.05, remaining))
            self.wake.clear()
            remaining -= time.monotonic() - start
            self.publish(engine)

    def run(self):
        store = engine = capture = writer = None
        try:
            if not protocol.CRYPTO_AVAILABLE:
                raise RuntimeError('缺少 cryptography。请先运行“安装依赖.bat”。')
            config = self.config
            if config['mode'] == 'offline':
                self.status = '正在识别文件和缓存范围'
                digest = fingerprint(config['file'])
                source = 'pcap:' + digest
                profile = config.get('profile') or 'pcap:' + digest[:16]
            else:
                source = 'live:' + uuid.uuid4().hex
                profile = config.get('profile') or 'live-default'
            store = Store(config['db'],rare_only=config.get('gui',False))
            engine = Engine(store, source, profile, config.get('port', 8195),
                            spatial_1044=config.get('spatial_1044', True),radius_m=config.get('radius_m',200))
            engine.compact=config.get('gui',False);engine.world_id=self.world_id
            if config['mode'] == 'offline':
                reader = PcapReader(config['file'])
                previous = None
                self.status = 'PCAP 回放中'
                for packet in reader:
                    permission = self.gate(engine)
                    if not permission:
                        break
                    speed = config.get('speed', 0)
                    if previous is not None and speed > 0 and permission != 'step':
                        self.delay((packet[0] - previous)/speed, engine)
                    if self.stop_event.is_set():
                        break
                    previous = packet[0]
                    engine.feed_packet(*packet)
                    self.packet_index += 1
                    engine.source_meta = dict(reader.stats)
                    self.publish(engine)
                self.status = '回放已停止，已处理记录已保存' if self.stop_event.is_set() else '回放完成，缓存已保存'
            else:
                capture = Npcap()
                capture.open(config['device'], config.get('port', 8195))
                if capture.warning:
                    engine.log('Npcap 提示：' + capture.warning)
                if config.get('record'):
                    writer = PcapWriter(config['record'], capture.link)
                    engine.log('同时保存原始 PCAP：' + config['record'])
                self.status = '实时采集中；若无密钥，请在采集开始后重新进入游戏'
                last_stats = 0
                while not self.stop_event.is_set():
                    self.switch_world(engine)
                    store=engine.store
                    packet = capture.next()
                    if packet:
                        ts, data, wirelen = packet
                        self.packet_index += 1
                        if writer:
                            writer.write(ts, data, wirelen)
                        engine.feed_packet(ts, self.packet_index, data, capture.link, wirelen)
                    else:
                        self.stop_event.wait(.02)
                    if time.monotonic() - last_stats > 1:
                        engine.source_meta = {'mode': 'live', 'device': config['device'],
                                              'linktype': capture.link, 'npcap_stats': capture.stats(),
                                              'record_file': config.get('record')}
                        last_stats = time.monotonic()
                    self.publish(engine)
                engine.source_meta['npcap_stats'] = capture.stats()
                self.status = '实时采集已停止，缓存已保存'
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.status = '处理失败；已处理记录保留'
            if engine:
                engine.log(self.error)
                engine.log(traceback.format_exc())
        finally:
            cleanup = []
            if writer:
                cleanup.append(writer.close)
            if capture:
                cleanup.append(capture.close)
            if engine:
                engine.clear_battles()
                cleanup.extend([lambda: self.publish(engine, True), engine.close])
            if store:
                cleanup.append(store.close)
            for action in cleanup:
                try:
                    action()
                except Exception as exc:
                    if not self.error:
                        self.error = f'保存/关闭失败：{exc}'
                        self.status = '结束时发生错误，请检查诊断与数据库路径'
            with self.lock:
                self.done = True


def time_text(value):
    return datetime.fromtimestamp(value).strftime('%m-%d %H:%M:%S') if value is not None else '—'


def launch_gui():
    from desktop import Desktop
    app=Desktop(sys.modules[__name__])
    app.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description='ROCO附近精灵：330 快照 + 已验证1044空间增量 + 307 + Npcap')
    parser.add_argument('pcap', nargs='?', help='classic PCAP 文件；不填则打开界面')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='SQLite 缓存位置')
    parser.add_argument('--profile', default='', help='显式指定缓存范围；只合并同一世界/线路的数据')
    parser.add_argument('--port', default='8195', help='服务端端口；空字符串为自动方向判断')
    parser.add_argument('--speed', type=float, default=0, help='回放速度，0 为不限速')
    parser.add_argument('--out', help='JSON 诊断报告')
    parser.add_argument('--entities-csv', help='全部缓存 CSV')
    parser.add_argument('--nearby-csv', help='当前候选列表 CSV')
    parser.add_argument('--radius',default='200',help='候选半径，单位米；默认200，不限制抓包接收范围')
    parser.add_argument('--favorites-csv',help='特殊收藏 CSV，不受普通筛选与距离限制')
    parser.add_argument('--frames-csv', help='最近2000条消息 CSV')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--live-device', help='Npcap 网卡名称，先用 --list-devices 查询')
    parser.add_argument('--record', help='实时模式保存原始 PCAP，不覆盖已有文件')
    parser.add_argument('--diagnostic-1044', action='store_true', help='关闭1044空间增量；捕捉成功移除仍处理')
    parser.add_argument('--name', default='', help='按名称、物种ID或unique_id的部分内容筛选')
    parser.add_argument('--voice-preset', choices=['all','melodic','hoarse','special','custom'],default='all',
                        help='声音：all全部 / melodic婉转声 / hoarse粗嗓门 / special两者 / custom自定义')
    for key,title in [('voice','声音'),('level','等级'),('height','身高百分比'),('weight','体重百分比')]:
        parser.add_argument('--'+key+'-min',help=title+'下限，包含边界')
        parser.add_argument('--'+key+'-max',help=title+'上限，包含边界')
    args = parser.parse_args()
    if args.list_devices:
        print(json.dumps(Npcap().devices(), ensure_ascii=False, indent=2))
        return 0
    if not args.pcap and not args.live_device:
        launch_gui()
        return 0
    port = int(args.port) if args.port.strip() else None
    if port is not None and not 1 <= port <= 65535:
        parser.error('端口应为 1–65535 或空字符串')
    if args.speed < 0:
        parser.error('回放速度不能为负数')
    voice_mode = args.voice_preset
    if voice_mode == 'all' and (args.voice_min is not None or args.voice_max is not None):
        voice_mode = 'custom'
    try:
        radius=radius_value(args.radius)
        spec = FilterSpec.build(query=args.name,voice_mode=voice_mode,
                                **{k:getattr(args,k) for k in ('voice_min','voice_max','level_min','level_max',
                                                              'height_min','height_max','weight_min','weight_max')})
    except ValueError as exc:
        parser.error(str(exc))
    config = {'mode': 'live' if args.live_device else 'offline', 'file': args.pcap,
              'device': args.live_device, 'db': args.db, 'profile': args.profile,
              'port': port, 'speed': args.speed, 'record': args.record,
              'spatial_1044': not args.diagnostic_1044,'radius_m':radius}
    job = Job(config)
    job.start()
    try:
        while not job.done:
            job.thread.join(.2)
    except KeyboardInterrupt:
        job.stop()
        job.thread.join(10)
    _, value, status, error, _, _ = job.read()
    if value:
        if args.out:
            save_json(args.out, filtered_report(value,spec))
        if args.entities_csv:
            save_entities(args.entities_csv, spec.apply(value['entities']))
        if args.nearby_csv:
            save_entities(args.nearby_csv, spec.apply(value['nearby']))
        if args.favorites_csv:
            save_entities(args.favorites_csv,value['favorites'])
        if args.frames_csv:
            save_frames(args.frames_csv, value['frames'])
        print(json.dumps({'status': status, 'profile': value['profile'], 'scene': value['scene'],
                          'player': value['player_position'], 'summary': value['summary'],
                          'display_filter': filtered_report(value,spec)['display_filter']},
                         ensure_ascii=False, indent=2))
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
