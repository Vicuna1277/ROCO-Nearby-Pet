"""Range, variant badges and north-up directions; no packet assumptions here."""
import math
from filters import voice_label,valid_voice,number
from body import enrich,pct_text


def radius_value(value):
    value=number(value,'候选距离',minimum=0.01)
    if value is None:
        raise ValueError('请输入候选距离，单位米')
    return value


def variant_tags(entity):
    mutation=entity.get('mutation')
    mutation=mutation if isinstance(mutation,int) and not isinstance(mutation,bool) and mutation>=0 else 0
    tags=[]
    if mutation&1:tags.append('异色')
    if mutation&8 or entity.get('glass_type') in (1,2):tags.append('炫彩')
    return tags


def favorite_tags(entity):
    found=set(entity.get('favorite_tags') or []) | set(variant_tags(entity))
    return [tag for tag in ('异色','炫彩') if tag in found]


def direction8(dx,dy):
    if math.hypot(dx,dy)<1e-9:return '原地'
    # World +X east, +Y south. Fixed map north, never camera-relative.
    angle=math.degrees(math.atan2(dx,-dy))%360
    return ('北','东北','东','东南','南','西南','西','西北')[int((angle+22.5)//45)%8]


def voice_text(value):
    if not valid_voice(value):return '未知'
    return f'{value:+d} dB' if value else '0 dB'


def voice_color(value):
    return '#15803d' if value==100 else '#c2410c' if value==-100 else '#475569'


def height_extreme(entity):
    value=(entity if 'height_pct' in entity else enrich(entity)).get('height_pct')
    if isinstance(value,bool) or not isinstance(value,(int,float)):return None
    if value==0:return 'small'
    if value==100:return 'large'
    return None


def height_text(entity):
    entity=entity if 'height_pct' in entity else enrich(entity)
    extreme=height_extreme(entity)
    if extreme:return '0% 小不点' if extreme=='small' else '100% 大块头'
    return pct_text(entity.get('height_pct'))


def row_key(entity):return (str(entity.get('scene')),str(entity.get('identity')))


def cache_filter(spec=None):
    from filters import FilterSpec
    spec=spec or FilterSpec()
    return FilterSpec(voice_mode='special') if spec==FilterSpec() else spec


def radar_cache(snapshot,candidates,spec=None):
    """Review matching ordinary records; rare records always belong to favorites."""
    spec=cache_filter(spec)
    visible={row_key(e) for e in candidates}
    return [e for e in snapshot.get('entities',[]) if not e.get('captured')
        and not favorite_tags(e) and row_key(e) not in visible and spec.matches(e)]


def display_subset(snapshot,spec,radius):
    """Discard irrelevant history from the GUI workload, never from SQLite or the source snapshot."""
    cached_spec=cache_filter(spec);scene=snapshot.get('scene');player=snapshot.get('player_position')
    rows=[]
    for e in snapshot.get('entities',[]):
        if favorite_tags(e):rows.append(e);continue
        if e.get('captured'):continue
        if cached_spec.matches(e):rows.append(e);continue
        if not scene or e.get('scene')!=scene:continue
        if player and e.get('pos'):
            dx=e['pos']['x']-player['x'];dy=e['pos']['y']-player['y']
            nearby=dx*dx+dy*dy<=(radius*100)**2
        else:nearby=e.get('observed_this_session',not snapshot.get('history_only',False))
        if nearby and spec.matches(e):rows.append(e)
    return dict(snapshot,entities=rows)


def display_name(entity):
    tags=favorite_tags(entity)
    if entity.get('captured'):tags.append('已捕获')
    return str(entity.get('name','未知'))+(' ['+' / '.join(tags)+']' if tags else '')


def location_text(entity):
    if entity.get('live_distance') is False:return '记录'
    distance=entity.get('distance_m')
    if distance is None:return '其他场景' if entity.get('other_scene') else '待坐标'
    return f"{distance:.1f} m · {entity.get('direction','待坐标')}"


def project(dx_m,dy_m,radius,width,height,padding=30):
    size=max(1,min(width,height)/2-padding)
    return width/2+dx_m/radius*size,height/2+dy_m/radius*size,size


def build_view(snapshot,radius):
    """Rebuild from the complete cache; expanding range never needs a fresh packet."""
    radius=radius_value(radius)
    result=dict(snapshot)
    scene,player=snapshot.get('scene'),snapshot.get('player_position')
    cache=[];current=[];nearby=[];favorites=[];ordinary=[]
    for original in snapshot.get('entities',[]):
        e=dict(enrich(original),distance_m=None,direction=None)
        e['favorite_tags']=favorite_tags(e)
        e['other_scene']=bool(scene and e.get('scene')!=scene)
        if scene and e.get('scene')==scene:
            if player and e.get('pos'):
                dx=(e['pos']['x']-player['x'])/100
                dy=(e['pos']['y']-player['y'])/100
                e.update(distance_m=math.hypot(dx,dy),direction=direction8(dx,dy),dx_m=dx,dy_m=dy)
            current.append(e)
            received=e.get('observed_this_session',not snapshot.get('history_only',False))
            in_range=(e['distance_m']<=radius if e['distance_m'] is not None else received)
            if not e.get('captured') and in_range:
                nearby.append(e)
        cache.append(e)
        if e['favorite_tags']:favorites.append(e)
        else:ordinary.append(e)
    candidate_keys={row_key(e) for e in nearby}
    ordinary=[e for e in ordinary if row_key(e) not in candidate_keys]
    sort=lambda e:(e.get('distance_m') is None,e.get('distance_m') if e.get('distance_m') is not None else math.inf,e.get('name',''),str(e.get('identity','')))
    for rows in (cache,current,nearby,favorites,ordinary):rows.sort(key=sort)
    result.update(entities=cache,scene_entities=current,nearby=nearby,favorites=favorites,
                  ordinary_cache=ordinary,radius_m=radius)
    result['summary']=dict(snapshot.get('summary',{}),cached_entities=len(cache),current_scene_entities=len(current),
        nearby_within_radius=len(nearby),nearby_within_100m=sum(not e.get('captured') and e.get('distance_m') is not None and e['distance_m']<=100 for e in current),
        favorite_entities=len(favorites),captured_entities_in_cache=sum(bool(e.get('captured')) for e in cache))
    return result
