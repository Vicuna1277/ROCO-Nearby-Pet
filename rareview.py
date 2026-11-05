"""Rare-only persistence policy and a bounded live distance set."""
from dataclasses import dataclass,asdict
import math
from body import enrich
from presentation import favorite_tags,height_extreme,direction8,row_key
from filters import voice_label

def interesting(e):
    return bool(favorite_tags(e) or voice_label(e.get('voice')) in ('婉转声','粗嗓门') or height_extreme(e))

@dataclass(frozen=True)
class RareFilter:
    query:str=''
    full:bool=False
    melodic:bool=False
    hoarse:bool=False
    large:bool=False
    small:bool=False
    @classmethod
    def build(cls,query='',**values):
        return cls(str(query).strip(),**{k:values.get(k) is True for k in ('full','melodic','hoarse','large','small')})
    def to_dict(self):return asdict(self)
    def matches(self,e):
        if not interesting(e):return False
        if self.query.casefold() not in str(e.get('name','')).casefold():return False
        v=e.get('voice');h=height_extreme(e)
        voice_ok=(not (self.full or self.melodic or self.hoarse) or
            (self.full and v in (-100,100)) or
            (self.melodic and voice_label(v)=='婉转声') or
            (self.hoarse and voice_label(v)=='粗嗓门'))
        height_ok=(not (self.large or self.small) or
            (self.large and h=='large') or (self.small and h=='small'))
        return bool(voice_ok and height_ok)
    def apply(self,rows):return [e for e in rows if self.matches(e)]

def prepare(rows):
    result=[dict(enrich(e),distance_m=None,direction=None,live_distance=False) for e in rows if interesting(e)]
    result.sort(key=lambda e:(e.get('last_seen',0),str(e.get('identity',''))),reverse=True)
    return result

def view(snapshot,spec,radius):
    # Input is already prepared and sorted by Store when its creature revision changes.
    rows=snapshot.get('entities',[]);scene=snapshot.get('scene');player=snapshot.get('player_position')
    selected=spec.apply(rows)
    active=set(list(row_key(e) for e in selected if not e.get('captured'))[:20])
    entities=[];nearby=[];favorites=[];records=[]
    for original in rows:
        key=row_key(original)
        if key in active:
            e=dict(original,distance_m=None,direction=None,live_distance=True,other_scene=bool(scene and original.get('scene')!=scene))
            if player and str(e.get('scene'))==str(scene) and e.get('pos'):
                dx=(e['pos']['x']-player['x'])/100;dy=(e['pos']['y']-player['y'])/100
                e.update(distance_m=math.hypot(dx,dy),direction=direction8(dx,dy),dx_m=dx,dy_m=dy)
            if not e.get('captured') and str(e.get('scene'))==str(scene) and (e['distance_m'] is None or e['distance_m']<=radius):nearby.append(e)
        else:e=original
        entities.append(e)
        if favorite_tags(e):favorites.append(e)
        if key not in {row_key(n) for n in nearby} and spec.matches(e):records.append(e)
    return dict(snapshot,entities=entities,nearby=nearby,favorites=favorites,ordinary_cache=records,radius_m=radius,
        summary={'cached_entities':len(rows),'captured_entities_in_cache':sum(bool(e.get('captured')) for e in rows)})
