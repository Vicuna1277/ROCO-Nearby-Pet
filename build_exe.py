"""Build the portable Windows executable; never includes user data/captures."""
import os
import argparse
import sys
from pathlib import Path
import PyInstaller.__main__

BASE=Path(__file__).resolve().parent
WORK=BASE/'build'
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',default='ROCO附近精灵')
    options=parser.parse_args()
    arguments=[
        str(BASE/'exe_entry.py'),'--name',options.name,'--onefile','--windowed','--noconfirm',
        '--distpath',str(BASE),'--workpath',str(WORK/'build'),'--specpath',str(WORK),
        '--paths',str(BASE),
        '--exclude-module','numpy','--exclude-module','matplotlib','--exclude-module','pandas',
    ]
    for name in ('data_tables','map_assets','assets'):
        if (BASE/name).is_dir():arguments+=['--add-data',str(BASE/name)+os.pathsep+name]
    if (BASE/'species_names.json').is_file():arguments+=['--add-data',str(BASE/'species_names.json')+os.pathsep+'.']
    if (BASE/'assets/roco.ico').is_file():arguments+=['--icon',str(BASE/'assets/roco.ico')]
    PyInstaller.__main__.run(arguments)
