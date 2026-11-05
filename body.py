"""Body percentages from the user-provided toolbox's creature form tables."""
import json
import math
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def tables():
    folder=Path(__file__).resolve().parent/'data_tables'
    def read(name):
        path=folder/name
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return read('creature_size_ranges.json'),read('npc_to_form.json')


def percentage(value,low,high):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (value,low,high)):
        return None,'missing_value'
    if high<=low:return None,'invalid_range'
    if not low<=value<=high:return None,'out_of_range'
    return (value-low)/(high-low)*100,'ok'


def enrich(entity):
    result=dict(entity,height_pct=None,weight_pct=None,size_form_id=None,
                height_pct_status='missing_mapping',weight_pct_status='missing_mapping')
    ranges,mapping=tables()
    form=mapping.get(str(entity.get('cfg_id')))
    record=ranges.get(str(form))
    if not record:return result
    result['size_form_id']=form
    if record.get('default_like_range'):
        result.update(height_pct_status='default_range',weight_pct_status='default_range')
        return result
    for key in ('height','weight'):
        low,high=record.get(key+'_min_raw'),record.get(key+'_max_raw')
        value,status=percentage(entity.get(key),low,high)
        result[key+'_pct']=value;result[key+'_pct_status']=status
        result[key+'_range_raw']=[low,high]
    return result


def pct_text(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return '—'
    return f'{value:.1f}%'
