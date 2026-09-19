import ctypes
import os
import runpy
import traceback
from pathlib import Path

APP_DIR=Path(__file__).resolve().parent
LOG_DIR=Path(os.environ.get('LOCALAPPDATA',APP_DIR))/'AcolyteFortnite'
LOG_FILE=LOG_DIR/'launcher.log'

def log(text):
    try:
        LOG_DIR.mkdir(parents=True,exist_ok=True)
        with LOG_FILE.open('a',encoding='utf-8') as f:
            f.write(text+'\n')
    except Exception:
        pass

def fatal(message):
    log(message)
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            message+'\n\nJournal : '+str(LOG_FILE),
            'Acolyte Fortnite - erreur de lancement',
            0x10
        )
    except Exception:
        pass

if __name__=='__main__':
    try:
        os.chdir(APP_DIR)
        log('--- lancement Acolyte ---')
        runpy.run_path(str(APP_DIR/'coach.py'),run_name='__main__')
    except SystemExit:
        raise
    except Exception:
        fatal(traceback.format_exc())
