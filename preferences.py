"""Portable, atomic local preferences. Filter units are explicitly percent."""
import json
import math
from pathlib import Path


class Preferences:
    def __init__(self,path):self.path=Path(path)
    def load(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8-sig'))
            return data if isinstance(data,dict) and data.get('schema')==1 and data.get('body_filter_units')=='percent' else {}
        except (OSError,ValueError):return {}
    def save(self,data):
        payload=dict(data,schema=1,body_filter_units='percent')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.path)


def preferred_device(devices,identifier):
    if identifier:return next((i for i,device in enumerate(devices) if device.get('name')==identifier),-1)
    return 0 if devices else -1
