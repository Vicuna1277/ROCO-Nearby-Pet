"""Portable single-world snapshots, including creatures, collections and exploration."""
from datetime import datetime,timezone
from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import uuid
from zipfile import ZipFile,ZIP_DEFLATED,BadZipFile

FORMAT='roco-world-save'
LEGACY_FORMAT='roco-account-save'
MAX_DB=512*1024*1024
MAX_TOTAL=2*1024*1024*1024


def hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        while block:=source.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def validate_database(path):
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('存档数据库损坏')
            objects=db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if any(kind in ('view','trigger') or 'VIRTUAL TABLE' in (sql or '').upper() for kind,_,sql in objects):
                raise ValueError('存档数据库包含不支持的结构')
            version=db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if not version or version[0]!='2':raise ValueError('不支持该存档数据版本')
            required={'creatures':{'profile','scene','identity','cfg_id','data','first_seen','last_seen','observations'},
                'observations':{'event_id','profile','scene','identity','data','ts','session','source','opcode'},
                'scene_actor_bindings':{'profile','source','session','scene','actor_id','identity','observed_ts'},
                'capture_events':{'event_id','profile','source','session','scene','actor_id','ts'},
                'conflicts':{'event_id','profile','scene','identity','ts','reason','data'},
                'exploration_points':{'profile','scene','gx','gy','x','y','ts'},
                'exploration_epochs':{'profile','epoch'}}
            for table,columns in required.items():
                found={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
                if not columns<=found:raise ValueError('存档缺少必要的精灵或涂色数据结构')
    except sqlite3.Error as error:raise ValueError('无法读取存档数据库') from error


def inspect_bundle(path):
    try:
        with ZipFile(path) as bundle:
            if len(bundle.infolist())>257 or bundle.getinfo('manifest.json').file_size>256*1024:
                raise ValueError('存档包目录过大')
            manifest=json.loads(bundle.read('manifest.json').decode('utf-8'))
            if not isinstance(manifest,dict):raise ValueError('存档包说明无效')
            if manifest.get('format') not in (FORMAT,LEGACY_FORMAT) or manifest.get('format_version')!=1:
                raise ValueError('不是支持的 RoCo 世界存档包')
            worlds=manifest['worlds']
            if not isinstance(worlds,list) or not 1<=len(worlds)<=256:raise ValueError('存档包世界列表无效')
            if manifest['format']==FORMAT and len(worlds)!=1:raise ValueError('世界存档包应只包含一个世界')
            names=set();members=set();total=0
            for world in worlds:
                name=world['name'];member=world['member'];info=bundle.getinfo(member)
                if not isinstance(name,str) or not name.strip() or name!=name.strip() or len(name)>32 or any(ord(c)<32 for c in name):
                    raise ValueError('存档包名称无效')
                if name.casefold() in names or member in members:raise ValueError('存档包有重复世界或文件')
                # Members are never extracted to their supplied paths.
                if not isinstance(member,str) or not re.fullmatch(r'worlds/[0-9]{4}\.sqlite3',member):
                    raise ValueError('存档包文件目录无效')
                if not 0<info.file_size<=MAX_DB or info.file_size!=world['size']:raise ValueError('存档包文件大小无效')
                if not isinstance(world['sha256'],str) or len(world['sha256'])!=64:raise ValueError('存档包校验信息无效')
                total+=info.file_size;names.add(name.casefold());members.add(member)
            if total>MAX_TOTAL:raise ValueError('存档包解压后过大')
            listed=[info.filename for info in bundle.infolist()]
            if len(set(listed))!=len(listed) or set(listed)!=members|{'manifest.json'}:
                raise ValueError('存档包包含重复或未声明的文件')
            return manifest
    except (BadZipFile,KeyError,TypeError,UnicodeError,json.JSONDecodeError) as error:
        raise ValueError('存档包不完整或格式错误') from error


def export_world(archives,path,identifier=None):
    identifier=identifier or archives.current
    return _export_entries(archives,path,[archives.get(identifier)],identifier)


def _export_entries(archives,path,worlds,active):
    from engine import VERSION
    from storage import Store
    if not worlds:raise ValueError('没有可导出的世界')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if any(path.resolve()==archives.database(e['id']).resolve() for e in archives.entries):
        raise ValueError('导出目标不能是正在使用的数据库文件')
    manifest={'format':FORMAT if len(worlds)==1 else LEGACY_FORMAT,'format_version':1,'app_version':VERSION,
        'created_at':datetime.now(timezone.utc).isoformat(),
        'active_world':archives.get(active)['name'],'worlds':[]}
    # Destination is replaced only after every snapshot and ZIP member succeeds.
    with tempfile.TemporaryDirectory(prefix='.roco-export-',dir=path.parent) as td:
        folder=Path(td);pending=folder/'world.rocosave'
        with ZipFile(pending,'w',ZIP_DEFLATED) as bundle:
            for index,entry in enumerate(worlds):
                snapshot=folder/f'{index}.sqlite3';source=archives.database(entry['id'])
                if source.exists():
                    if source.stat().st_size>MAX_DB:raise ValueError('单个世界数据过大')
                    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(snapshot)) as dst:
                        src.backup(dst)
                else:
                    store=Store(snapshot);store.close()
                validate_database(snapshot)
                member=f'worlds/{index:04d}.sqlite3'
                manifest['worlds'].append({'name':entry['name'],'member':member,'size':snapshot.stat().st_size,
                    'sha256':hash_file(snapshot)})
                bundle.write(snapshot,member)
            bundle.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        inspect_bundle(pending)
        os.replace(pending,path)
    return manifest


def import_worlds(archives,path,name=None):
    manifest=inspect_bundle(path)
    if name is not None:
        if len(manifest['worlds'])!=1:raise ValueError('多个世界请分别保留原名称导入')
        name=archives.validate_name(name)
    directory=archives.base/'data';directory.mkdir(parents=True,exist_ok=True)
    additions=[];installed=[];used={entry['name'].casefold() for entry in archives.entries}
    with tempfile.TemporaryDirectory(prefix='.roco-import-',dir=directory) as td:
        folder=Path(td)
        with ZipFile(path) as bundle:
            for index,world in enumerate(manifest['worlds']):
                staged=folder/f'{index}.sqlite3';digest=hashlib.sha256();size=0
                with bundle.open(world['member']) as src,staged.open('wb') as dst:
                    while block:=src.read(1024*1024):
                        size+=len(block)
                        if size>MAX_DB:raise ValueError('存档数据超出大小限制')
                        digest.update(block);dst.write(block)
                if digest.hexdigest()!=world['sha256'] or size!=world['size']:raise ValueError('存档校验失败，文件可能已损坏')
                validate_database(staged)
                identifier=uuid.uuid4().hex
                label=name or archives.unique_name(world['name'],used);used.add(label.casefold())
                additions.append({'id':identifier,'name':label,
                    'database':f'data/saves/{identifier}.sqlite3'})
        old=list(archives.entries)
        try:
            for index,entry in enumerate(additions):
                target=archives.base/entry['database'];target.parent.mkdir(parents=True,exist_ok=True)
                os.replace(folder/f'{index}.sqlite3',target);installed.append(target)
            archives.entries.extend(additions);archives.save()
        except Exception:
            archives.entries=old
            for target in installed:target.unlink(missing_ok=True)
            raise
    active=next((i for i,world in enumerate(manifest['worlds']) if world['name']==manifest.get('active_world')),0)
    return additions[active]['id']


def import_legacy_directory(archives,path):
    """Read every old world, retaining each database independently."""
    from archives import Archives
    folder=Path(path).resolve()
    if (folder/'data').is_dir():folder=folder/'data'
    catalog=folder/'archives.json'
    if catalog.is_file():
        data=json.loads(catalog.read_text('utf-8-sig'));entries=data['entries'];current=data['current']
        entries=[dict(e) for e in entries]
    else:entries=[{'id':'legacy','name':'默认世界','database':'data/nearby.sqlite3'}];current='legacy'
    legacy=Archives.__new__(Archives);legacy.base=folder.parent;legacy.current=current;legacy.entries=[]
    used=set()
    for entry in entries:
        raw=Path(entry['database'])
        if raw.is_absolute():
            candidates=[folder/'saves'/raw.name,folder/raw.name]
            candidate=next((p for p in candidates if p.is_file()),None)
        else:
            parts=raw.parts
            candidate=folder.joinpath(*(parts[1:] if parts and parts[0]=='data' else parts))
        if candidate is None or not candidate.is_file():raise ValueError('旧版数据文件缺失，请选择包含完整 data 的目录')
        candidate=candidate.resolve()
        if folder!=candidate.parent and folder not in candidate.parents:raise ValueError('旧版目录引用了外部文件，请先复制回 data 目录')
        label=Archives.unique_name(entry['name'],used);used.add(label.casefold())
        entry.pop('account',None);entry.update(name=label,database=str(candidate));legacy.entries.append(entry)
    directory=archives.base/'data';directory.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.legacy-',dir=directory) as td:
        package=Path(td)/'legacy.rocosave'
        _export_entries(legacy,package,legacy.entries,current)
        return import_worlds(archives,package)
