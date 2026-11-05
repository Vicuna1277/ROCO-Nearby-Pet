"""One event/state pipeline shared by PCAP replay and live capture."""
import hashlib
import json
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field

import protocol as p
from lifecycle import actor_id_text, capture_success_ids
from presentation import build_view,radius_value
from battle import BattleTracker, OPCODES as BATTLE_OPCODES

VERSION = '0.19.2'
PARSER_VERSION = 'sample-v8-s5-nature'
SPATIAL_PENDING_TTL_S = 5.0


class StreamLimitError(RuntimeError):
    pass


class Reassembler:
    """32-bit sequence unwrapping; do not skip unresolved gaps."""
    def __init__(self, limit=4*1024*1024):
        self.next_seq = None
        self.pending = {}
        self.limit = limit
        self.gaps = self.duplicates = 0
        self.pending_bytes = 0

    def feed(self, seq, payload):
        if not payload:
            return b''
        if self.next_seq is None:
            absolute = seq
            self.next_seq = seq
        else:
            delta = ((seq - (self.next_seq & 0xffffffff) + 0x80000000) & 0xffffffff) - 0x80000000
            absolute = self.next_seq + delta
        if absolute + len(payload) <= self.next_seq:
            self.duplicates += len(payload)
            return b''
        if absolute < self.next_seq:
            cut = self.next_seq - absolute
            self.duplicates += cut
            payload, absolute = payload[cut:], self.next_seq
        if absolute > self.next_seq:
            old = self.pending.get(absolute, b'')
            if old and old[:min(len(old), len(payload))] != payload[:min(len(old), len(payload))]:
                raise StreamLimitError('TCP 重传内容冲突，停止该会话解析')
            if len(payload) > len(old):
                self.pending[absolute] = payload
                self.pending_bytes += len(payload) - len(old)
            self.gaps += 1
            if self.pending_bytes > self.limit or len(self.pending) > 4096:
                raise StreamLimitError('TCP 缺口积压超过限制，停止该会话解析；请重新建立连接')
            return b''
        output = bytearray(payload)
        self.next_seq += len(payload)
        while self.pending:
            key = min(self.pending)
            if key > self.next_seq:
                break
            value = self.pending.pop(key)
            self.pending_bytes -= len(value)
            if key + len(value) > self.next_seq:
                output.extend(value[self.next_seq-key:])
                self.next_seq = key + len(value)
        return bytes(output)


def parse_actor(record, source_opcode=330):
    top = p.parse_proto_fields(record)
    wrap = p.first_value(top, 11, 2)
    if not isinstance(wrap, bytes):
        return None, 'missing_actor_wrapper'
    fields = p.parse_proto_fields(wrap)
    basic_raw, detail_raw = p.first_value(fields, 1, 2), p.first_value(fields, 3, 2)
    if not isinstance(basic_raw, bytes) or not isinstance(detail_raw, bytes):
        return None, 'missing_basic_or_detail'
    basic, detail = p.parse_proto_fields(basic_raw), p.parse_proto_fields(detail_raw)
    cfg = p.first_value(detail, 1, 0)
    if cfg is None or not 10000 <= cfg <= 19999:
        return None, 'outside_sample_creature_cfg_range'
    pos_outer = p.first_value(basic, 8, 2)
    pos_inner = p.first_value(p.parse_proto_fields(pos_outer), 1, 2) if isinstance(pos_outer, bytes) else None
    pos = p.parse_pos_message(pos_inner) if isinstance(pos_inner, bytes) else None
    if not pos or not (100000 <= pos['x'] <= 2000000 and 100000 <= pos['y'] <= 2000000):
        return None, 'missing_or_out_of_sample_position'
    entity = {'cfg_id': cfg, 'name': p.NAME_TABLE.get(cfg, f'物种 {cfg}'), 'pos': pos,
              'source_opcode': source_opcode, 'parser_version': PARSER_VERSION, 'record_bytes': len(record)}
    for name, fs, number in [('actor_id', basic, 1), ('unique_id', detail, 17),
                             ('refresh_id', detail, 10), ('level', basic, 11),
                             ('height', detail, 11), ('weight', detail, 12),
                             ('mutation', detail, 14), ('voice', detail, 31)]:
        value = p.first_value(fs, number, 0)
        if value is not None:
            entity[name] = p.signed64(value)
    # Legacy actor_id=basic.field1 is the actor type (6), not the scene entity ID.
    entity['actor_type'] = p.first_value(basic, 1, 0)
    scene_actor_id = actor_id_text(p.first_value(basic, 2, 0))
    if scene_actor_id:
        entity['scene_actor_id'] = scene_actor_id
    glass = p.first_value(detail, 30, 2)
    if isinstance(glass, bytes):
        gf = p.parse_proto_fields(glass)
        for name, number in [('glass_type', 1), ('glass_value', 2)]:
            value = p.first_value(gf, number, 0)
            if value is not None:
                entity[name] = p.signed64(value)
    return entity, None


def spatial_1044_records(plain):
    """Only the observed S2C offset-10 / field1 / field1 / field1 record branch.

    Do not recursively search arbitrary blobs; unrelated/removal/partial-update branches
    do not establish a wild-creature observation. CBC tail bytes are not protobuf data.
    """
    if len(plain) < 11 or plain[2:4] != (1044).to_bytes(2, 'big') or plain[4:6] != b'\x55\xaa':
        return
    root = p.parse_proto_fields(plain[10:])
    for outer_index, outer in enumerate(p.values(root, 1, 2)):
        for batch_index, batch in enumerate(p.values(p.parse_proto_fields(outer), 1, 2)):
            for record_index, record in enumerate(p.values(p.parse_proto_fields(batch), 1, 2)):
                # In all spatial records of this capture the top-level discriminator is 6.
                if p.first_value(p.parse_proto_fields(record), 1, 0) == 6:
                    yield f'1[{outer_index}]/1[{batch_index}]/1[{record_index}]', record


@dataclass
class Session:
    identifier: str
    endpoints: tuple
    generation: int
    server: object = None
    direction_source: str = 'unknown'
    syn_seq: object = None
    key: object = field(default=None, repr=False)
    verified: bool = False
    scene: object = None
    scene_info: dict = field(default_factory=dict)
    player: object = None
    player_ts: object = None
    last_ts: float = 0
    failed: bool = False
    ended: bool = False
    frame_index: int = 0
    counters: Counter = field(default_factory=Counter)
    battle: BattleTracker = field(default_factory=BattleTracker)
    seen: set = field(default_factory=set)
    pending_client: list = field(default_factory=list, repr=False)
    pending_spatial: deque = field(default_factory=deque, repr=False)
    streams: list = field(default_factory=lambda: [Reassembler(), Reassembler()], repr=False)
    parsers: list = field(default_factory=lambda: [p.TgcpStreamParser(0), p.TgcpStreamParser(1)], repr=False)


class Engine:
    def __init__(self, store, source_id, profile, port=8195, spatial_1044=True, radius_m=100):
        self.store, self.source_id, self.profile, self.port = store, source_id, profile, port
        self.spatial_1044 = spatial_1044
        self.radius_m = radius_value(radius_m)
        self.sessions = OrderedDict()
        self.generations = Counter()
        self.active = None
        self.counts = Counter()
        self.opcodes = Counter()
        self.rejects = Counter()
        self.frames = deque(maxlen=2000)
        self.events = deque(maxlen=2000)
        self.candidates = deque(maxlen=200)
        self.logs = deque(maxlen=300)
        self.source_meta = {}
        self.last_snapshot_at = 0
        self.spatial_candidates = deque(maxlen=200)
        self.log('330 快照 + 307 XY；1044 捕捉成功按真实场景实体 ID 移出附近，历史缓存保留。' +
                 ('启用1044空间增量。' if spatial_1044 else '1044空间增量关闭。'))

    def log(self, text):
        self.logs.append(text)

    def _new_session(self, key, seg):
        self.generations[key] += 1
        generation = self.generations[key]
        digest = hashlib.sha256(repr(key).encode()).hexdigest()[:12]
        identifier = f'{digest}/{generation}'
        session = Session(identifier, key, generation, last_ts=seg.ts)
        if self.port:
            matches = [i for i, ep in enumerate(key) if ep[1] == self.port]
            if len(matches) == 1:
                session.server, session.direction_source = matches[0], 'configured_port'
        if seg.syn and not seg.ack_flag:
            session.syn_seq = seg.seq
            if session.server is None:
                session.server = 1 - key.index(seg.src)
                session.direction_source = 'tcp_syn'
        self.sessions[key] = session
        self.counts['sessions_created'] += 1
        if len(self.sessions) > 64:
            _, old = self.sessions.popitem(last=False)
            self._retire(old)
            self.counts['sessions_evicted'] += 1
            self.log(f'连接数量超过 64，释放最久未活动会话 {old.identifier}。')
        return session

    def _retire(self, s):
        self.counts['retired_pending_bytes'] += sum(r.pending_bytes for r in s.streams)
        self.counts['retired_bad_headers'] += sum(r.bad_headers for r in s.parsers)
        s.key = None
        s.battle.reset('连接已断开')
        s.pending_client.clear()
        s.pending_spatial.clear()

    def feed_packet(self, ts, order, data, link, wirelen):
        self.counts['packets'] += 1
        if link == 108:  # DLT_LOOP: packet parser can infer the IP version after its 4-byte family.
            link = 0
        if len(data) < wirelen:
            self.counts['truncated_packets'] += 1
        seg = p.parse_network_packet(data, link, ts, order, wirelen)
        if seg is None:
            self.counts['non_tcp_or_unparsed'] += 1
            return
        self.feed_segment(seg)

    def feed_segment(self, seg):
        self.counts['tcp_packets'] += 1
        if self.port and seg.src[1] != self.port and seg.dst[1] != self.port:
            self.counts['port_filtered_packets'] += 1
            return
        key = tuple(sorted((seg.src, seg.dst)))
        s = self.sessions.get(key)
        new_syn = seg.syn and not seg.ack_flag
        if s is None or (new_syn and (s.ended or s.syn_seq != seg.seq)):
            if s:
                self._retire(s)
            s = self._new_session(key, seg)
        self.sessions.move_to_end(key)
        s.last_ts = seg.ts
        if s.failed:
            return
        d = key.index(seg.src)
        stream = s.streams[d]
        if seg.syn and stream.next_seq is None:
            stream.next_seq = (seg.seq + 1) & 0xffffffff
        gaps, dup = stream.gaps, stream.duplicates
        try:
            raw = stream.feed((seg.seq + int(seg.syn)) & 0xffffffff, seg.payload)
        except StreamLimitError as exc:
            s.failed, s.player, s.key = True, None, None
            s.battle.reset('连接数据中断', seg.ts)
            s.pending_client.clear()
            s.pending_spatial.clear()
            self.counts['stream_failures'] += 1
            self.log(f'{s.identifier}: {exc}')
            return
        self.counts['tcp_gap_events'] += stream.gaps - gaps
        self.counts['tcp_duplicate_bytes'] += stream.duplicates - dup
        for fr in s.parsers[d].feed(raw, seg.ts, seg.order):
            self.process_frame(s, fr)
        if seg.flags & 0x04:  # RST invalidates current position and crypto.
            s.ended, s.player, s.key = True, None, None
            s.battle.reset('连接已断开', seg.ts)
            s.pending_client.clear()
            s.pending_spatial.clear()
        elif seg.flags & 0x01:
            s.ended = True
            s.battle.reset('连接已断开', seg.ts)

    def process_frame(self, s, fr):
        s.frame_index += 1
        event_base = f'{self.source_id}:{s.identifier}:{s.frame_index}'
        self.counts['tgcp_frames'] += 1
        if s.server is None and fr.command == 0x1002:
            s.server, s.direction_source = fr.direction_idx, 'server_key_frame'
        direction = 'unknown' if s.server is None else ('s2c' if fr.direction_idx == s.server else 'c2s')
        row = {'ts': fr.ts, 'session': s.identifier, 'direction': direction,
               'command': f'0x{fr.command:04X}', 'sequence': fr.sequence,
               'body_length': fr.body_length, 'opcode': None, 'status': 'plain_command'}
        self.frames.append(row)
        if direction == 'unknown':
            self.counts['direction_unknown_frames'] += 1
            return
        if fr.command == 0x1002 and direction == 's2c':
            self.counts['key_frames'] += 1
            s.battle.reset('连接更新，等待战斗数据', fr.ts)
            if len(fr.raw) >= 39:
                s.key, s.verified = fr.raw[23:39], False
                s.player = None
                s.player_ts = None
                s.pending_client.clear()
                s.pending_spatial.clear()
                row['status'] = 'key_candidate'
            else:
                row['status'] = 'short_key_frame'
            return
        if fr.command != 0x4013:
            return
        self.counts['encrypted_frames'] += 1
        if not s.key:
            self.counts['frames_without_key'] += 1
            row['status'] = 'waiting_for_key'
            return
        if not p.CRYPTO_AVAILABLE:
            row['status'] = 'missing_cryptography'
            return
        plain, mode = p.decrypt_4013(fr.body, s.key)
        if plain is None:
            self.counts['decrypt_fail'] += 1
            row['status'] = 'decrypt_failed'
            return
        if direction == 's2c':
            if len(plain) < 6 or plain[4:6] != b'\x55\xaa':
                self.counts['bad_server_markers'] += 1
                row['status'] = 'bad_marker'
                # A failed candidate or corrupt frame cannot modify application state.
                if not s.verified:
                    s.key = None
                    s.pending_client.clear()
                return
            if not s.verified:
                s.verified = True
                self.counts['key_verified_sessions'] += 1
                for pending in s.pending_client:
                    self.handle_plain(s, *pending)
                s.pending_client.clear()
        elif not s.verified:
            if len(s.pending_client) < 64 and sum(len(v[0]) for v in s.pending_client) + len(plain) <= 1024*1024:
                s.pending_client.append((plain, direction, fr.ts, event_base, row))
                row['status'] = 'waiting_for_key_verification'
            else:
                self.counts['unverified_client_dropped'] += 1
            return
        row['decrypt_mode'] = mode
        self.handle_plain(s, plain, direction, fr.ts, event_base, row)

    def handle_plain(self, s, plain, direction, ts, event_base, row):
        offset = 2 if direction == 's2c' else 6
        if len(plain) < offset + 2:
            self.counts['short_plain'] += 1
            return
        opcode = int.from_bytes(plain[offset:offset+2], 'big')
        if not getattr(self,'ingest_enabled',True) and opcode not in (338,348):
            row['status']='world_switch_guard';return
        row.update(opcode=opcode, status='decoded')
        self.counts['decrypt_ok'] += 1
        self.opcodes[f'{direction}:{opcode}'] += 1
        s.counters[f'{direction}:{opcode}'] += 1
        if direction == 's2c' and opcode in BATTLE_OPCODES:
            changed = s.battle.accept(plain, opcode, ts)
            row['status'] = 'battle_updated' if changed else 'battle_branch_ignored'
            self.counts['battle_updates' if changed else 'battle_ignored_frames'] += 1
            if changed:
                self.events.append({'event': 'battle_updated', 'ts': ts, 'session': s.identifier,
                                    'opcode': opcode, 'battle_id': s.battle.battle_id,
                                    'enemy_count': len(s.battle.enemies)})
            return
        if opcode in (1043, 1044):
            row['status'] = 'diagnostic_only'
            self.counts['diagnostic_1043_1044_frames'] += 1
            # Field shape only; no entity candidates, removal IDs, keys or raw plaintext exported.
            proto = p.extract_proto_payload(plain)
            if proto is not None:
                row['root_field_shapes'] = dict(Counter(f"{f['n']}/{f['w']}" for f in p.parse_proto_fields(proto)))
            capture_ids = capture_success_ids(plain) if opcode == 1044 and direction == 's2c' else []
            if capture_ids:
                row['capture_actor_ids'] = capture_ids
                self.counts['capture_success_notifications'] += 1
                self.counts['capture_success_actor_ids'] += len(capture_ids)
                for identifier in capture_ids:
                    if not s.scene:
                        self.counts['capture_unknown_scene'] += 1
                        continue
                    marked, status = self.store.mark_captured(self.profile,self.source_id,s.identifier,
                        s.scene,identifier,ts,f'{event_base}:capture:{identifier}')
                    self.counts['capture_records_marked'] += len(marked)
                    self.counts[f'capture_match_{status}'] += 1
                    if marked:
                        self.active = s.identifier
                    self.events.append({'event':'capture_success','ts':ts,'session':s.identifier,
                        'scene':s.scene,'opcode':1044,'scene_actor_id':identifier,
                        'matched':marked,'match_status':status})
                    if marked:
                        self.log('捕捉成功，移出附近：' + ', '.join(f"{e['name']} ({e['unique_id']})" for e in marked))
            accepted = queued = 0
            if opcode == 1044 and direction == 's2c' and self.spatial_1044:
                for branch, record in spatial_1044_records(plain):
                    self.counts['spatial_1044_branch_records'] += 1
                    entity, reason = parse_actor(record, source_opcode=1044)
                    if entity is None:
                        self.rejects['1044:' + reason] += 1
                        continue
                    if not entity.get('unique_id'):
                        self.rejects['1044:missing_identity'] += 1
                        continue
                    known = self.store.knows(self.profile, s.scene, entity['unique_id'], entity['cfg_id']) if s.scene else False
                    if not known and (entity.get('height', 0) <= 0 or entity.get('weight', 0) <= 0):
                        self.rejects['1044:missing_body_evidence'] += 1
                        continue
                    entity['source_branch'] = '1044:root.1.1.1:record_type_6'
                    entry = {'entity': entity, 'ts': ts, 'scene': s.scene,
                             'event_id': f'{event_base}:1044:{branch}'}
                    state = self._try_spatial(s, entry)
                    if state == 'accepted':
                        accepted += 1
                    elif s.scene:
                        if len(s.pending_spatial) >= 512:
                            s.pending_spatial.popleft()
                            self.counts['spatial_1044_queue_evictions'] += 1
                        s.pending_spatial.append(entry)
                        self.counts['spatial_1044_deferred'] += 1
                        queued += 1
                    else:
                        self.rejects['1044:unknown_scene'] += 1
                    if state != 'accepted':
                        self.spatial_candidates.append({'ts': ts, 'entity': entity, 'reason': state})
                row.update(status='spatial_1044_checked', entity_count=accepted, spatial_queued=queued)
            if capture_ids:
                row['status'] = 'capture_success'
            if accepted or queued:
                self.counts['spatial_1044_frames_with_records'] += 1
            elif not capture_ids:
                self.counts['diagnostic_only_frames'] += 1
            return
        if opcode == 307 and direction == 'c2s':
            pos = p.parse_player_position_307(plain)
            if pos is None:
                self.counts['position_parse_failed'] += 1
                row['status'] = 'position_parse_failed'
                return
            self.set_position(s, pos, ts)
            row['position'] = pos
        elif opcode in (338, 348) and direction == 's2c':
            scene = p.parse_scene_event(opcode, plain)
            if scene:
                self.set_scene(s, scene, opcode, ts)
                row['scene_info'] = scene
        elif opcode == 330 and direction == 's2c':
            self.active = s.identifier
            self.counts['snapshot_330_frames'] += 1
            proto = p.extract_proto_payload(plain)
            if proto is None:
                self.counts['snapshot_parse_failed'] += 1
                row['status'] = 'snapshot_parse_failed'
                return
            records = p.values(p.parse_proto_fields(proto), 7, 2)
            self.counts['snapshot_raw_records'] += len(records)
            accepted = 0
            scene_key = s.scene or f'unknown:{s.identifier}'
            for index, record in enumerate(records):
                entity, reason = parse_actor(record)
                if entity is None:
                    self.rejects[reason] += 1
                    continue
                # Retain v0.1's body-metadata evidence for NEW wild records. A cfg range alone
                # does not establish a creature's type. Partial updates of a known record are OK.
                if (entity.get('height', 0) <= 0 or entity.get('weight', 0) <= 0) and not self.store.knows(
                        self.profile, scene_key, entity.get('unique_id'), entity['cfg_id']):
                    self.rejects['new_record_missing_body_evidence'] += 1
                    self.candidates.append({'ts': ts, 'scene': scene_key, 'entity': entity,
                                            'reason': '缺少当前样本用于识别野生精灵的身高/体重，暂不计入附近'})
                    continue
                added, conflict = self.store.observe(self.profile, scene_key, entity, ts,
                                                      f'{event_base}:{index}', s.identifier, self.source_id)
                self.counts['new_observations' if added else 'duplicate_observations'] += 1
                self.counts['identity_conflicts'] += conflict
                self.counts['accepted_330_records'] += 1
                uid = entity.get('unique_id')
                if uid and not getattr(self,'compact',False) and (not self.store.rare_only or added or self.store.knows(self.profile,scene_key,uid,entity['cfg_id'])):
                    s.seen.add((scene_key, uid))
                accepted += 1
            row['entity_count'] = accepted
            self.events.append({'event': 'snapshot_merged', 'ts': ts, 'session': s.identifier,
                                'scene': scene_key, 'accepted': accepted, 'raw_records': len(records)})

    def set_scene(self, s, info, opcode, ts):
        new_scene = str(info['scene_id'])
        # 348 is kept as existing scene/teleport evidence. Coordinates from it are diagnostic only.
        if s.scene != new_scene or opcode == 348:
            s.player, s.player_ts = None, None
            s.battle.reset('场景更新，等待战斗数据', ts)
            self.counts['spatial_1044_discarded_on_scene_reset'] += len(s.pending_spatial)
            s.pending_spatial.clear()
        s.scene, s.scene_info = new_scene, dict(info)
        self.events.append({'event': 'scene_observed', 'ts': ts, 'session': s.identifier,
                            'opcode': opcode, 'scene': new_scene})

    def set_position(self, s, pos, ts):
        if s.player_ts is not None and ts < s.player_ts:
            self.counts['outdated_position_ignored'] += 1
            return
        changed = s.player is None or (s.player['x'], s.player['y']) != (pos['x'], pos['y'])
        s.player, s.player_ts = dict(pos), ts
        self.store.record_exploration(self.profile,s.scene,pos,ts)
        self.active = s.identifier
        self.counts['position_307_updates'] += 1
        self._retry_spatial(s, ts)
        if changed:
            self.counts['distance_recomputations'] += 1
            nearby = [] if getattr(self,'compact',False) else self.store.rows(self.profile, s.scene, s.player, self.radius_m) if s.scene else []
            self.events.append({'event': 'player_moved', 'ts': ts, 'session': s.identifier,
                                'scene': s.scene, 'pos': pos, 'nearby_count': len(nearby)})

    def _try_spatial(self, s, entry):
        entity, ts = entry['entity'], entry['ts']
        if s.scene is None or s.scene != entry['scene']:
            return 'unknown_or_changed_scene'
        # Receive all structurally validated creatures in a known scene. Player
        # coordinates and candidate radius affect display only, never admission.
        pos = entity['pos']
        distance = (((pos['x']-s.player['x'])**2 + (pos['y']-s.player['y'])**2)**.5/100
                    if s.player else None)
        known = self.store.knows(self.profile, s.scene, entity['unique_id'], entity['cfg_id'])
        added, conflict = self.store.observe(self.profile, s.scene, entity, ts, entry['event_id'], s.identifier, self.source_id)
        self.counts['new_observations' if added else 'duplicate_observations'] += 1
        self.counts['identity_conflicts'] += conflict
        self.counts['accepted_1044_spatial_records'] += 1
        if not known and added:
            self.counts['new_1044_spatial_entities'] += 1
        if not getattr(self,'compact',False) and (not self.store.rare_only or added or known):s.seen.add((s.scene, entity['unique_id']))
        self.active = s.identifier
        self.events.append({'event': 'spatial_creature_merged', 'ts': ts, 'session': s.identifier,
                            'scene': s.scene, 'opcode': 1044, 'unique_id': entity['unique_id'],
                            'cfg_id': entity['cfg_id'], 'distance_at_accept_m': distance,
                            'new_entity': not known and added})
        return 'accepted'

    def _retry_spatial(self, s, now):
        remaining = deque()
        for entry in s.pending_spatial:
            if now - entry['ts'] > SPATIAL_PENDING_TTL_S:
                self.counts['spatial_1044_expired'] += 1
                continue
            if self._try_spatial(s, entry) != 'accepted':
                remaining.append(entry)
        s.pending_spatial = remaining

    def battle_snapshot(self, session_id=None):
        sessions = [v for v in self.sessions.values() if session_id is None or v.identifier == session_id]
        # A newer ended/disconnected battle must hide an older session's state.
        current = max(sessions, key=lambda v: v.battle.updated_ts or 0, default=None)
        return current.battle.snapshot() if current else BattleTracker().snapshot()

    def clear_battles(self, status='采集已停止'):
        for session in self.sessions.values():
            session.battle.reset(status, session.last_ts)

    def snapshot(self, session_id=None):
        battle = self.battle_snapshot(session_id)
        session_id = session_id or self.active
        s = next((v for v in self.sessions.values() if v.identifier == session_id), None)
        scene, player = (s.scene, s.player) if s else (None, None)
        if getattr(self,'compact',False):
            return dict(profile=self.profile,battle=battle,entities=self.store.rare_rows(self.profile),scene=scene,player_position=player,
                exploration=self.store.exploration(self.profile,scene),radius_m=self.radius_m,summary={},
                world_id=getattr(self,'world_id',None),switch_guard=not getattr(self,'ingest_enabled',True))
        cache = self.store.rows(self.profile)
        scene_rows = self.store.rows(self.profile, scene, player) if scene else []
        nearby = [e for e in scene_rows if not e.get('captured') and e['distance_m'] is not None and e['distance_m'] <= 100]
        for e in scene_rows:
            e['observed_this_session'] = (e['scene'], e.get('unique_id')) in s.seen
        calculated = {(e['scene'], e['identity']): e for e in scene_rows}
        cache = [calculated.get((e['scene'], e['identity']), e) for e in cache]
        counts = dict(self.counts)
        counts.update(cached_entities=len(cache), current_scene_entities=len(scene_rows),
                      captured_entities_in_cache=sum(bool(e.get('captured')) for e in cache),
                      nearby_within_100m=len(nearby),
                      unresolved_tcp_bytes=sum(r.pending_bytes for v in self.sessions.values() for r in v.streams),
                      bad_tgcp_headers=sum(r.bad_headers for v in self.sessions.values() for r in v.parsers))
        value = {'app': 'ROCO附近精灵', 'version': VERSION, 'parser_version': PARSER_VERSION,
                'battle': battle,
                'profile': self.profile, 'source_id': self.source_id, 'source_meta': dict(self.source_meta),
                'session': s.identifier if s else None, 'active_session': self.active,
                'scene': scene, 'player_position': player,
                'exploration': self.store.exploration(self.profile,scene),
                'player_position_ts': s.player_ts if s else None,
                'distance_reference_source': 'opcode_307' if player else None,
                'distance_scale': 100, 'radius_m': self.radius_m,
                'coverage': ('330 + 已验证1044空间分支 + 缓存；不保证所有区域/协议版本完整覆盖'
                             if self.spatial_1044 else '330与缓存；1044空间增量关闭，捕捉成功移除仍启用'),
                'spatial_1044_policy': {'enabled': self.spatial_1044, 'branch': 'root.1.1.1, record field1=6',
                    'protobuf_offset': 10, 'pending_ttl_s': SPATIAL_PENDING_TTL_S, 'max_distance_m': None,
                    'admission': 'known scene + verified actor branch; no distance or player-position gate',
                    'player_position': 'last 307 in current scene context; idle time alone does not invalidate',
                    'capture_success_branch': 'root.1.173; explicit true; exact uint64 scene_actor_id',
                    'other_1044_branches': 'diagnostic-only', 'opcode_1043': 'diagnostic-only'},
                'summary': counts, 'opcode_counts': dict(self.opcodes), 'reject_reasons': dict(self.rejects),
                'sessions': [{'id': v.identifier, 'endpoints': v.endpoints, 'scene': v.scene,
                              'key_verified': v.verified, 'player': v.player, 'failed': v.failed,
                              'ended': v.ended, 'direction_source': v.direction_source,
                              'player_ts': v.player_ts, 'observed_uids': list(v.seen)}
                             for v in self.sessions.values()],
                'nearby': nearby, 'scene_entities': scene_rows, 'entities': cache,
                'frames': list(self.frames), 'timeline': list(self.events), 'logs': list(self.logs),
                'unconfirmed_330_candidates': list(self.candidates),
                'unconfirmed_1044_candidates': list(self.spatial_candidates),
                'pending_1044_spatial_records': sum(len(v.pending_spatial) for v in self.sessions.values()),
                'retention': {'frames': 2000, 'timeline': 2000, 'logs': 300,
                              'observation_history': 'SQLite, retained incrementally'}}
        return build_view(value,self.radius_m)

    def close(self):
        self.clear_battles()
        for s in self.sessions.values():
            s.key = None
            s.pending_client.clear()
            s.pending_spatial.clear()
        self.store.flush()
