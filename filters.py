"""Presentation-only filters; capture and SQLite observations remain unfiltered."""
import math
from dataclasses import asdict, dataclass
from body import enrich

VOICE_OPTIONS = {
    '全部声音': 'all',
    '婉转声（96～100）': 'melodic',
    '粗嗓门（-100～-96）': 'hoarse',
    '婉转声或粗嗓门': 'special',
    '自定义区间': 'custom',
}


def valid_voice(value):
    return isinstance(value, int) and not isinstance(value, bool) and -100 <= value <= 100


def voice_label(value):
    if value is None:
        return '未知'
    if not valid_voice(value):
        return '超出范围'
    if value >= 96:
        return '婉转声'
    if value <= -96:
        return '粗嗓门'
    return '普通'


def number(value, label, integer=False, minimum=None, maximum=None):
    if value is None or str(value).strip() == '':
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}请输入有效数字') from None
    if not math.isfinite(result) or (integer and not result.is_integer()):
        raise ValueError(f'{label}请输入' + ('整数' if integer else '有限数字'))
    if minimum is not None and result < minimum:
        raise ValueError(f'{label}不能小于{minimum}')
    if maximum is not None and result > maximum:
        raise ValueError(f'{label}不能大于{maximum}')
    return int(result) if integer else result


@dataclass(frozen=True)
class FilterSpec:
    query: str = ''
    voice_mode: str = 'all'
    voice_min: object = None
    voice_max: object = None
    level_min: object = None
    level_max: object = None
    height_min: object = None
    height_max: object = None
    weight_min: object = None
    weight_max: object = None

    @classmethod
    def build(cls, query='', voice_mode='all', **bounds):
        if voice_mode not in VOICE_OPTIONS.values():
            raise ValueError('未知的声音筛选类型')
        result = {'query': query.strip(), 'voice_mode': voice_mode}
        for key, label in [('voice','声音'), ('level','等级'), ('height','身高百分比'), ('weight','体重百分比')]:
            if key == 'voice' and voice_mode != 'custom':
                continue
            for side, suffix in [('min','下限'), ('max','上限')]:
                name = f'{key}_{side}'
                result[name] = number(bounds.get(name), label+suffix, integer=key in ('voice','level'),
                                      minimum=-100 if key == 'voice' else 0,
                                      maximum=100 if key in ('voice','height','weight') else None)
            low, high = result.get(f'{key}_min'), result.get(f'{key}_max')
            if low is not None and high is not None and low > high:
                raise ValueError(f'{label}下限不能大于上限')
        return cls(**result)

    def matches(self, entity):
        if self.query:
            haystack = ' '.join(str(entity.get(k,'')) for k in ('name','cfg_id','unique_id')).casefold()
            if self.query.casefold() not in haystack:
                return False
        value = entity.get('voice')
        if self.voice_mode != 'all':
            if not valid_voice(value):
                return False
            if self.voice_mode == 'melodic' and not 96 <= value <= 100:
                return False
            if self.voice_mode == 'hoarse' and not -100 <= value <= -96:
                return False
            if self.voice_mode == 'special' and not (value >= 96 or value <= -96):
                return False
            if self.voice_mode == 'custom':
                if self.voice_min is not None and value < self.voice_min:
                    return False
                if self.voice_max is not None and value > self.voice_max:
                    return False
        for key in ('level','height','weight'):
            low, high = getattr(self, key+'_min'), getattr(self, key+'_max')
            if low is None and high is None:
                continue
            if key in ('height','weight'):
                percent_key=key+'_pct'
                value=entity.get(percent_key) if percent_key in entity else enrich(entity).get(percent_key)
            else:
                value = entity.get(key)
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
                return False
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
        return True

    def apply(self, rows):
        return [row for row in rows if self.matches(row)]

    def to_dict(self):
        return asdict(self)


def filtered_report(snapshot, spec):
    result = dict(snapshot)
    result['filtered_nearby'] = [dict(e, voice_label=voice_label(e.get('voice'))) for e in spec.apply(snapshot.get('nearby',[]))]
    result['filtered_entities'] = [dict(e, voice_label=voice_label(e.get('voice'))) for e in spec.apply(snapshot.get('entities',[]))]
    result['display_filter'] = dict(spec.to_dict(),body_units='percent',
        nearby_matches=len(result['filtered_nearby']), nearby_total=len(snapshot.get('nearby',[])),
        cache_matches=len(result['filtered_entities']), cache_total=len(snapshot.get('entities',[])))
    return result
