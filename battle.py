"""Observed enemy-only battle branches. No warehouse scan or world-cache writes.

Schemas verified against the 2026-10-28 PC capture. Unknown branches are ignored;
the creature inside an untriggered encounter cannot be predicted by this parser.
"""
from copy import deepcopy
import protocol as p
from body import tables, percentage

OPCODES = frozenset((4886, 4900, 4890, 4908))
MAX_UNITS = 32
NATURE_NAMES = dict(enumerate(('', '大胆', '固执', '调皮', '勇敢', '逞强', '稳重', '天真', '懒散',
    '悠闲', '坦率', '聪明', '专注', '偏执', '冷静', '理性', '警惕', '温顺', '害羞', '慎重',
    '焦虑', '胆小', '急躁', '开朗', '莽撞', '热情', '沉默', '忧郁', '平和', '粗心', '踏实', '平和')))


def fields(buf, root=False):
    """Strict nested protobuf, bounded allowance for decrypted root padding."""
    if len(buf) > 2 * 1024 * 1024:
        return []
    result = []
    pos = 0
    while pos < len(buf) and len(result) < 4000:
        start = pos
        tag, nxt = p.read_varint(buf, pos)
        if tag is None or not 1 <= tag >> 3 <= 4096:
            break
        n, wire = tag >> 3, tag & 7
        pos = nxt
        if wire == 0:
            value, nxt = p.read_varint(buf, pos)
            if value is None or value >= 1 << 64:
                pos = start
                break
        elif wire == 2:
            size, nxt = p.read_varint(buf, pos)
            if size is None or nxt + size > len(buf):
                pos = start
                break
            value = buf[nxt:nxt + size]
            nxt += size
        elif wire in (1, 5):
            nxt = pos + (8 if wire == 1 else 4)
            if nxt > len(buf):
                pos = start
                break
            value = buf[pos:nxt]
        else:
            pos = start
            break
        result.append({'n': n, 'w': wire, 'v': value})
        pos = nxt
    if pos != len(buf) and not (root and result and len(buf) - pos <= 32):
        return []
    return result


def children(fs, n):
    return [fields(f['v']) for f in fs if f['n'] == n and f['w'] == 2]


def number(fs, n):
    values = [f['v'] for f in fs if f['n'] == n and f['w'] == 0]
    return values[0] if len(values) == 1 else None


def text(fs, n):
    values = [f['v'] for f in fs if f['n'] == n and f['w'] == 2]
    if len(values) != 1:
        return None
    try:
        value = values[0].decode('utf-8')
    except UnicodeDecodeError:
        return None
    return value if 0 < len(value) <= 80 and all(ord(c) >= 32 for c in value) else None


def unit(wrapper):
    states, details = children(wrapper, 1), children(wrapper, 2)
    if len(states) != 1 or len(details) != 1:
        return None
    state, detail = states[0], details[0]
    slot, game, form, name = number(state, 1), number(detail, 2), number(detail, 15), text(detail, 3)
    spec = tables()[0].get(str(form))
    if not name and [f['v'] for f in detail if f['n']==3 and f['w']==2] == [b'']:
        name = (spec or {}).get('name') or f'精灵（形态{form}）'
    if not (slot and game and form and name):
        return None
    # Reject a mismatched state/detail pair rather than attributing it to an enemy.
    for n, expected in ((21, game), (22, form)):
        actual = number(state, n)
        if actual is not None and actual != expected:
            return None
    if text(state, 23) not in (None, name):
        return None
    voice = number(detail, 93)
    voice = p.signed64(voice) if voice is not None else None
    if voice is not None and not -100 <= voice <= 100:
        return None
    height, weight = number(detail, 26), number(detail, 27)
    result = dict(slot_id=str(slot), game_id=game, form_id=form, name=name,
                  level=number(detail, 10), height=height, weight=weight,
                  height_m=height / 100 if height is not None else None,
                  weight_kg=weight / 1000 if weight is not None else None,
                  voice=voice, blood_id=number(detail, 47),
                  nature_id=number(detail,7),nature_name=NATURE_NAMES.get(number(detail,7)) or '未知',
                  blood_name='奇异血脉' if number(detail, 47) == 24 else None,
                  height_pct=None, weight_pct=None, size_range_source=None)
    if spec and not spec.get('default_like_range'):
        for key in ('height', 'weight'):
            result[key + '_pct'] = percentage(result[key], spec.get(key + '_min_raw'), spec.get(key + '_max_raw'))[0]
        result['size_range_source'] = 'static_toolbox_table'
    return result


class BattleTracker:
    def __init__(self):
        self.reset()

    def reset(self, status='等待进入战斗', ts=None):
        self.battle_id = None
        self.enemy_side = None
        self.enemies = {}
        self.active = False
        self.status = status
        self.updated_ts = ts
        self.source_opcode = None

    def snapshot(self):
        return deepcopy(dict(active=self.active, status=self.status, battle_id=self.battle_id,
                             updated_ts=self.updated_ts, source_opcode=self.source_opcode,
                             enemies=list(self.enemies.values()) if self.active else []))

    def accept(self, plain, opcode, ts):
        if opcode not in OPCODES or len(plain) < 10 or plain[4:6] != b'\x55\xaa':
            return False
        if int.from_bytes(plain[2:4], 'big') != opcode:
            return False
        root = fields(plain[10:], root=True)
        if not root:
            return False
        if opcode == 4886:
            contexts = children(root, 6)
            if len(contexts) != 1:
                return False
            context = contexts[0]
            battle_id = number(context, 1)
            sides = children(context, 6)  # field5 is our team; never traverse it.
            if not battle_id:
                return False
            # A recognized new battle must not keep enemies from an older one,
            # even when its side layout is not supported by this sample parser.
            new_battle = str(battle_id) != self.battle_id
            if new_battle:
                self.reset('已进入新战斗，等待敌方信息', ts)
                self.battle_id = str(battle_id)
            if len(sides) != 1:
                return new_battle
            side = sides[0]
            metadata = children(side, 1)
            enemy_side = number(metadata[0], 1) if len(metadata) == 1 else None
            records = [unit(v) for v in children(side, 2)]
            if not enemy_side or not records or len(records) > MAX_UNITS or any(v is None for v in records):
                return new_battle
            if len({v['slot_id'] for v in records}) != len(records):
                return new_battle
            self.reset('战斗中', ts)
            self.battle_id, self.enemy_side = str(battle_id), enemy_side
            self.enemies = {v['slot_id']: v for v in records}
            self.active = True
        elif opcode == 4908:
            summaries = children(root, 1)
            if not self.active or len(summaries) != 1 or str(number(summaries[0], 19)) != self.battle_id:
                return False
            self.reset('战斗已结束', ts)
        else:
            if not self.active:
                return False
            updates = []
            for batch in children(root, 1 if opcode == 4900 else 3):
                for event in children(batch, 2):
                    if opcode == 4900:
                        for event_field,wrapper_field in ((58,10),(59,9)):
                            for change in children(event,event_field):
                                slot = number(change, 1)
                                for wrapper in children(change,wrapper_field):
                                    value = unit(wrapper)
                                    if not value or str(slot)!=value['slot_id'] or value['slot_id'] not in self.enemies:
                                        continue
                                    if event_field==59:
                                        current=self.enemies[value['slot_id']]['form_id']
                                        if number(change,3)!=value['form_id'] or current not in (number(change,2),value['form_id']):
                                            continue
                                    updates.append(value)
                    else:
                        for change in children(event, 44):
                            for side in children(change, 8):
                                if number(side, 1) != self.enemy_side:
                                    continue
                                for wrapper in children(side, 3):
                                    value = unit(wrapper)
                                    if value and value['slot_id'] in self.enemies:
                                        updates.append(value)
            if not updates:
                return False
            for value in updates:
                # Replacement at a known enemy slot replaces all fields atomically.
                self.enemies[value['slot_id']] = value
        self.updated_ts, self.source_opcode = ts, opcode
        return True
