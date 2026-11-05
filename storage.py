"""Incremental SQLite cache. Missing observations never delete creatures."""
import json
import math
import sqlite3
import uuid
from pathlib import Path
from lifecycle import actor_id_text
from presentation import favorite_tags


def read_map_data(path,profile,scene):
    """Read committed coverage without opening a schema-writing Store on the UI thread."""
    from contextlib import closing
    empty={'token':'empty','radius_m':100,'sample_cell_m':5,'points':(),'count':0}
    path=Path(path)
    if not path.is_file():return profile or '',empty,None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)) as db:
        db.execute('BEGIN')
        profiles=[r[0] for r in db.execute('SELECT profile FROM creatures UNION SELECT profile FROM exploration_points ORDER BY profile')]
        if not profile:profile='live-default' if 'live-default' in profiles else profiles[-1] if profiles else ''
        epoch=db.execute('SELECT epoch FROM exploration_epochs WHERE profile=?',(profile,)).fetchone()
        points=tuple(db.execute('SELECT x,y FROM exploration_points WHERE profile=? AND scene=? ORDER BY rowid',(profile,str(scene))))
        last=db.execute('SELECT scene,x,y FROM exploration_points WHERE profile=? ORDER BY ts DESC,rowid DESC LIMIT 1',(profile,)).fetchone()
    exploration=dict(empty,token=f'{profile}:{scene}:{epoch[0] if epoch else "empty"}',points=points,count=len(points))
    historical={'x':last[1],'y':last[2]} if last and str(last[0])==str(scene) else None
    return profile,exploration,historical


class Store:
    def __init__(self, path,rare_only=False):
        self.rare_only=rare_only;self.entity_revision=0;self._rare_cache={}
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT OR IGNORE INTO meta VALUES ('schema_version','2');
        CREATE TABLE IF NOT EXISTS creatures (
            profile TEXT NOT NULL, scene TEXT NOT NULL, identity TEXT NOT NULL,
            cfg_id INTEGER NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
            observations INTEGER NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY (profile,scene,identity));
        CREATE TABLE IF NOT EXISTS observations (
            event_id TEXT PRIMARY KEY, profile TEXT NOT NULL, scene TEXT NOT NULL,
            identity TEXT NOT NULL, ts REAL NOT NULL, session TEXT NOT NULL,
            source TEXT NOT NULL, opcode INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS obs_identity ON observations(profile,scene,identity);
        CREATE TABLE IF NOT EXISTS conflicts (
            event_id TEXT PRIMARY KEY, profile TEXT, scene TEXT, identity TEXT,
            ts REAL, reason TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS scene_actor_bindings (
            profile TEXT NOT NULL, source TEXT NOT NULL, session TEXT NOT NULL,
            scene TEXT NOT NULL, actor_id TEXT NOT NULL, identity TEXT NOT NULL,
            observed_ts REAL NOT NULL,
            PRIMARY KEY(profile,source,session,scene,actor_id,identity));
        CREATE TABLE IF NOT EXISTS capture_events (
            event_id TEXT PRIMARY KEY, profile TEXT NOT NULL, source TEXT NOT NULL,
            session TEXT NOT NULL, scene TEXT NOT NULL, actor_id TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS capture_actor ON capture_events(profile,source,session,scene,actor_id);
        CREATE TABLE IF NOT EXISTS exploration_points (
            profile TEXT NOT NULL, scene TEXT NOT NULL, gx INTEGER NOT NULL, gy INTEGER NOT NULL,
            x REAL NOT NULL, y REAL NOT NULL, ts REAL NOT NULL,
            PRIMARY KEY(profile,scene,gx,gy));
        CREATE TABLE IF NOT EXISTS exploration_epochs (profile TEXT PRIMARY KEY, epoch TEXT NOT NULL);
        ''')
        version = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        if version != '2':
            raise RuntimeError('缓存版本不兼容，请选择新的数据库文件。')
        self._exploration_cache={}

    def record_exploration(self,profile,scene,pos,ts):
        """One real received center per 5m cell. Never interpolate teleport gaps."""
        if not scene or not pos:return False
        try:
            x,y=float(pos['x']),float(pos['y'])
            if not all(math.isfinite(v) for v in (x,y,ts)):return False
        except (TypeError,ValueError,KeyError):return False
        self.db.execute('INSERT OR IGNORE INTO exploration_epochs VALUES (?,?)',(profile,uuid.uuid4().hex))
        cur=self.db.execute('INSERT OR IGNORE INTO exploration_points VALUES (?,?,?,?,?,?,?)',
            (profile,str(scene),math.floor(x/500),math.floor(y/500),x,y,ts))
        if cur.rowcount:self._exploration_cache.pop((profile,str(scene)),None)
        return bool(cur.rowcount)

    def exploration(self,profile,scene):
        key=(profile,str(scene))
        if key not in self._exploration_cache:
            epoch=self.db.execute('SELECT epoch FROM exploration_epochs WHERE profile=?',(profile,)).fetchone()
            points=tuple((r[0],r[1]) for r in self.db.execute(
                'SELECT x,y FROM exploration_points WHERE profile=? AND scene=? ORDER BY rowid',key))
            self._exploration_cache[key]={'token':f'{profile}:{scene}:{epoch[0] if epoch else "empty"}',
                'radius_m':100,'sample_cell_m':5,'points':points,'count':len(points)}
        return self._exploration_cache[key]

    def clear_exploration(self,profile):
        count=self.db.execute('SELECT COUNT(*) FROM exploration_points WHERE profile=?',(profile,)).fetchone()[0]
        with self.db:
            self.db.execute('DELETE FROM exploration_points WHERE profile=?',(profile,))
            self.db.execute('INSERT OR REPLACE INTO exploration_epochs VALUES (?,?)',(profile,uuid.uuid4().hex))
        self._exploration_cache={key:value for key,value in self._exploration_cache.items() if key[0]!=profile}
        return count

    def exploration_last(self,profile):
        row=self.db.execute('SELECT scene,x,y,ts FROM exploration_points WHERE profile=? ORDER BY ts DESC,rowid DESC LIMIT 1',
            (profile,)).fetchone()
        return dict(scene=row['scene'],pos={'x':row['x'],'y':row['y']},ts=row['ts']) if row else None

    def clear_ordinary(self,profile):
        self.entity_revision+=1
        """Remove ordinary records and dedup history; keep rare collections and their bindings."""
        targets=[(profile,r['scene'],r['identity']) for r in self.db.execute(
            'SELECT scene,identity,data FROM creatures WHERE profile=?',(profile,))
            if not favorite_tags(json.loads(r['data']))]
        with self.db:
            for table in ('observations','scene_actor_bindings','conflicts','creatures'):
                self.db.executemany(f'DELETE FROM {table} WHERE profile=? AND scene=? AND identity=?',targets)
            self.db.execute('''DELETE FROM capture_events AS e WHERE profile=? AND NOT EXISTS
                (SELECT 1 FROM scene_actor_bindings AS b WHERE b.profile=e.profile AND b.source=e.source
                AND b.session=e.session AND b.scene=e.scene AND b.actor_id=e.actor_id)''',(profile,))
        return len(targets)

    def observe(self, profile, scene, entity, ts, event_id, session, source):
        """Returns (new observation, identity conflict). Zero is a valid field value."""
        uid = entity.get('unique_id')
        identity = f'uid:{uid}' if uid else f'temporary:{event_id}'
        old = self.db.execute('SELECT * FROM creatures WHERE profile=? AND scene=? AND identity=?',
                              (profile, scene, identity)).fetchone()
        if self.rare_only:
            from rareview import interesting
            merged=dict(json.loads(old['data']) if old else {})
            merged.update({k:v for k,v in entity.items() if v is not None})
            if not interesting(merged):return False,False
        self.entity_revision+=1
        conflict = old is not None and old['cfg_id'] != entity['cfg_id']
        if conflict:
            original = identity
            identity += f":cfg:{entity['cfg_id']}"
            self.db.execute('INSERT OR IGNORE INTO conflicts VALUES (?,?,?,?,?,?,?)',
                            (event_id, profile, scene, original, ts, 'same uid, different cfg_id',
                             json.dumps(entity, ensure_ascii=False)))
            old = self.db.execute('SELECT * FROM creatures WHERE profile=? AND scene=? AND identity=?',
                                  (profile, scene, identity)).fetchone()
        cur = self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                              (event_id, profile, scene, identity, ts, session, source,
                               entity.get('source_opcode', 330),
                               json.dumps(entity, ensure_ascii=False)))
        if not cur.rowcount:
            # Old v0.4 imports lack scene_actor_id. Replaying them must bind the new
            # metadata without counting the same observation a second time.
            incoming_actor = actor_id_text(entity.get('scene_actor_id'))
            if old is not None and incoming_actor and ts >= old['last_seen']:
                data = json.loads(old['data'])
                data['scene_actor_id'] = incoming_actor
                data['actor_type'] = entity.get('actor_type', data.get('actor_type'))
                data['favorite_tags'] = favorite_tags(dict(entity,favorite_tags=data.get('favorite_tags',[])))
                if (data.get('captured') and incoming_actor != data.get('capture_actor_id')
                        and ts > data.get('captured_at', ts)):
                    for key in ('captured_at','capture_actor_id','capture_event_id'):
                        data.pop(key,None)
                    data['captured'] = False
                self.db.execute('UPDATE creatures SET data=? WHERE profile=? AND scene=? AND identity=?',
                                (json.dumps(data,ensure_ascii=False), profile, scene, identity))
            self._bind_actor(profile, scene, identity, entity, ts, session, source)
            return False, conflict
        if old is None:
            data = dict(entity)
            first, last, count = ts, ts, 1
        else:
            data = json.loads(old['data'])
            first, last, count = min(ts, old['first_seen']), max(ts, old['last_seen']), old['observations'] + 1
            if ts >= old['last_seen']:
                data.update({k: v for k, v in entity.items() if v is not None})
                incoming_actor = actor_id_text(entity.get('scene_actor_id'))
                if (data.get('captured') and incoming_actor
                        and incoming_actor != data.get('capture_actor_id')
                        and ts > data.get('captured_at', ts)):
                    # A later, genuinely different scene instance may reuse the spawn UID.
                    for key in ('captured_at','capture_actor_id','capture_event_id'):
                        data.pop(key,None)
                    data['captured'] = False
        sources = set(data.get('source_opcodes', [data.get('source_opcode', 330)]))
        previous_tags = favorite_tags(json.loads(old['data'])) if old is not None else []
        data['favorite_tags'] = favorite_tags(dict(entity,favorite_tags=previous_tags+favorite_tags(data)))
        if old is not None:
            previous = json.loads(old['data'])
            sources.update(previous.get('source_opcodes', [previous.get('source_opcode', 330)]))
        sources.add(entity.get('source_opcode', 330))
        data['source_opcodes'] = sorted(sources)
        self.db.execute('INSERT OR REPLACE INTO creatures VALUES (?,?,?,?,?,?,?,?)',
                        (profile, scene, identity, entity['cfg_id'], first, last, count,
                         json.dumps(data, ensure_ascii=False)))
        self._bind_actor(profile, scene, identity, entity, ts, session, source)
        return True, conflict

    def _bind_actor(self, profile, scene, identity, entity, ts, session, source):
        identifier = actor_id_text(entity.get('scene_actor_id'))
        if not identifier:
            return
        self.db.execute('''INSERT INTO scene_actor_bindings VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(profile,source,session,scene,actor_id,identity)
            DO UPDATE SET observed_ts=MAX(observed_ts,excluded.observed_ts)''',
                        (profile,source,session,scene,identifier,identity,ts))
        event = self.db.execute('''SELECT ts,event_id FROM capture_events WHERE
            profile=? AND source=? AND session=? AND scene=? AND actor_id=? ORDER BY ts DESC LIMIT 1''',
                               (profile,source,session,scene,identifier)).fetchone()
        if event:
            self._apply_capture(profile,source,session,scene,identifier,event['ts'],event['event_id'])

    def mark_captured(self, profile, source, session, scene, identifier, ts, event_id):
        if self.rare_only:
            from rareview import interesting
            candidates=self.db.execute('SELECT c.data FROM creatures c JOIN scene_actor_bindings b ON c.profile=b.profile AND c.scene=b.scene AND c.identity=b.identity WHERE b.profile=? AND b.source=? AND b.session=? AND b.scene=? AND b.actor_id=?',
                (profile,source,session,scene,str(identifier))).fetchall()
            if not any(interesting(json.loads(r[0])) for r in candidates):return [],'unmatched'
        if self.rare_only and not self.db.execute('SELECT 1 FROM scene_actor_bindings WHERE profile=? AND source=? AND session=? AND scene=? AND actor_id=?',
                (profile,source,session,scene,str(identifier))).fetchone():return [],'unmatched'
        self.entity_revision+=1
        identifier = actor_id_text(identifier)
        if not identifier:
            return [], 'invalid_actor_id'
        self.db.execute('INSERT OR IGNORE INTO capture_events VALUES (?,?,?,?,?,?,?)',
                        (event_id,profile,source,session,scene,identifier,ts))
        return self._apply_capture(profile,source,session,scene,identifier,ts,event_id)

    def _apply_capture(self, profile, source, session, scene, identifier, ts, event_id):
        bindings = self.db.execute('''SELECT identity FROM scene_actor_bindings WHERE
            profile=? AND source=? AND session=? AND scene=? AND actor_id=?''',
                                  (profile,source,session,scene,identifier)).fetchall()
        if len(bindings) != 1:
            return [], 'unmatched' if not bindings else 'ambiguous'
        identity = bindings[0]['identity']
        row = self.db.execute('SELECT data FROM creatures WHERE profile=? AND scene=? AND identity=?',
                              (profile,scene,identity)).fetchone()
        if row is None:
            return [], 'unmatched'
        data = json.loads(row['data'])
        if data.get('scene_actor_id') != identifier:
            return [], 'different_current_instance'
        if data.get('captured') and data.get('capture_actor_id') == identifier:
            return [], 'already_captured'
        data.update(captured=True,captured_at=ts,capture_actor_id=identifier,capture_event_id=event_id)
        self.db.execute('UPDATE creatures SET data=? WHERE profile=? AND scene=? AND identity=?',
                        (json.dumps(data,ensure_ascii=False),profile,scene,identity))
        return [{'identity':identity,'unique_id':data.get('unique_id'),'name':data.get('name')}], 'marked'

    def rows(self, profile, scene=None, player=None, radius=None):
        sql, args = 'SELECT * FROM creatures WHERE profile=?', [profile]
        if scene is not None:
            sql += ' AND scene=?'
            args.append(scene)
        rows = []
        for row in self.db.execute(sql, args):
            ent = json.loads(row['data'])
            if radius is not None and ent.get('captured'):
                continue
            ent.update({k: row[k] for k in ('scene', 'identity', 'first_seen', 'last_seen', 'observations')})
            pos = ent.get('pos')
            distance = None
            if player and pos and scene is not None and row['scene'] == scene:
                distance = math.hypot(pos['x'] - player['x'], pos['y'] - player['y']) / 100.0
            ent['distance_m'] = distance
            if radius is None or (distance is not None and distance <= radius):
                rows.append(ent)
        rows.sort(key=lambda e: (e['distance_m'] is None,
                                e['distance_m'] if e['distance_m'] is not None else math.inf,
                                e.get('cfg_id', 0), e['identity']))
        return rows

    def rare_rows(self,profile):
        key=(profile,self.entity_revision)
        if key not in self._rare_cache:
            from rareview import prepare
            self._rare_cache={key:prepare(self.rows(profile))}
        return self._rare_cache[key]

    def knows(self, profile, scene, uid, cfg_id):
        if not uid:
            return False
        return self.db.execute('SELECT 1 FROM creatures WHERE profile=? AND scene=? AND identity=? AND cfg_id=?',
                               (profile, scene, f'uid:{uid}', cfg_id)).fetchone() is not None

    def scenes(self, profile):
        return [r[0] for r in self.db.execute('SELECT DISTINCT scene FROM creatures WHERE profile=? ORDER BY scene', (profile,))]

    def profiles(self):
        return [r[0] for r in self.db.execute('SELECT profile FROM creatures UNION SELECT profile FROM exploration_points ORDER BY profile')]

    def flush(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
