import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('SPR_DATA', ROOT / 'var')).resolve()
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
DATABASE = os.environ.get('DATABASE_URL', 'sqlite:///' + str(DATA / 'metadata.sqlite'))
BASE_URL = os.environ.get('BASE_URL', 'http://127.0.0.1:8765')
MATERIALS = next(ROOT.glob('ХАК*/ПАКЕТ/ЗАДАНИЕ'), None)
