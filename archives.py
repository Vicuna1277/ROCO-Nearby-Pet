"""Named independent world saves; each owns one SQLite file with creatures and paint."""
import json
from pathlib import Path
import uuid


class Archives:
    def __init__(self,base,legacy_database):
        self.base=Path(base).resolve();self.path=self.base/'data/archives.json'
        if self.path.exists():
            value=json.loads(self.path.read_text('utf-8'))
            self.entries=value['entries'];self.current=value['current']
            if value.get('schema')!=1 or not isinstance(self.entries,list) or not self.entries:
                raise ValueError('存档目录格式无效')
            if len({e['id'] for e in self.entries})!=len(self.entries):raise ValueError('存档标识重复')
            self.get(self.current)
        else:
            local=self.base/'data/nearby.sqlite3'
            legacy=Path(legacy_database)
            if local.is_file() and (not legacy.is_file() or (legacy.name=='nearby.sqlite3' and legacy.parent.name=='data')):
                legacy_database=local
            self.current='default'
            self.entries=[{'id':'default','name':'默认世界','database':self.encode(legacy_database)}]
            self.save()
        # Flatten the v0.12 grouping without merging or moving any SQLite file.
        changed=False;used=set()
        for entry in self.entries:
            name=self.unique_name(entry['name'],used)
            if name!=entry['name']:entry['name']=name;changed=True
            if 'account' in entry:entry.pop('account');changed=True
            used.add(name.casefold())
        if changed:self.save()

    def worlds(self):return list(self.entries)

    @staticmethod
    def unique_name(name,used):
        name=str(name).strip()[:32] or '导入世界';candidate=name;index=2
        while candidate.casefold() in used:
            suffix=f'（{index}）';candidate=name[:32-len(suffix)]+suffix;index+=1
        return candidate

    def encode(self,path):
        path=Path(path).resolve()
        try:return path.relative_to(self.base).as_posix()
        except ValueError:return str(path)

    def database(self,identifier=None):
        path=Path(self.get(identifier or self.current)['database'])
        return (self.base/path).resolve() if not path.is_absolute() else path

    def get(self,identifier):
        return next(e for e in self.entries if e['id']==identifier)

    def validate_name(self,name,exclude=None):
        name=str(name).strip()
        if not name or len(name)>32 or any(ord(c)<32 for c in name):raise ValueError('请输入 1～32 字的存档名称')
        if any(e['id']!=exclude and e['name'].casefold()==name.casefold() for e in self.entries):
            raise ValueError('已有同名存档，请换一个名称')
        return name

    def create(self,name):
        name=self.validate_name(name);identifier=uuid.uuid4().hex
        self.entries.append({'id':identifier,'name':name,'database':f'data/saves/{identifier}.sqlite3'})
        self.save();return identifier

    def rename(self,identifier,name):
        entry=self.get(identifier)
        entry['name']=self.validate_name(name,identifier);self.save()

    def select(self,identifier):
        self.get(identifier);self.current=identifier;self.save()

    def repoint(self,identifier,path):
        path=Path(path).resolve()
        if any(e['id']!=identifier and self.database(e['id'])==path for e in self.entries):
            raise ValueError('这个文件已属于另一存档，请直接切换到该存档')
        self.get(identifier)['database']=self.encode(path);self.save()

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schema':1,'current':self.current,'entries':self.entries},
            ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.path)
