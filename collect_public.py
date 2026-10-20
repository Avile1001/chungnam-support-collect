"""Collect public-page boards and refresh the notice list.

Runs only from 09:00 through 21:59. A second start within 90 minutes exits
so a missed morning does not turn into several back-to-back crawls.
"""
import json
import os
import sys
from pathlib import Path
import datetime as dt
import engine

HERE = Path(__file__).resolve().parent

def _www():
    folder = HERE.parents[1] / 'Server' / 'www'
    if folder.is_dir():
        return folder
    return None

def _pick(env_name, filename):
    override = os.environ.get(env_name)
    if override:
        return Path(override)
    www = _www()
    if www:
        return www / filename
    return HERE / filename

PUBLIC = _pick('CHUNGNAM_PUBLIC_JSON', 'chungnam_support_data.json')
OUT = _pick('CHUNGNAM_NOTICES_JSON', 'chungnam_support_notices.json')
STAMP = engine.DATA / 'last_public_collect.txt'
GAP = dt.timedelta(minutes=90)

def public_ids():
    return [row['id'] for row in json.loads(PUBLIC.read_text(encoding='utf-8'))]

def allowed(now):
    return 9 <= now.hour < 22

def recent(now):
    if not STAMP.exists():
        return False
    try:
        last = dt.datetime.fromisoformat(STAMP.read_text(encoding='utf-8').strip())
    except ValueError:
        return False
    return now - last < GAP

def export(ids):
    catalog = {row['id']: row for row in engine.sources()}
    items = []
    for row in engine.listing():
        if row['source_id'] not in ids:
            continue
        items.append({
            'id': row['id'],
            'sourceId': row['source_id'],
            'sourceName': row.get('source_name') or catalog.get(row['source_id'], {}).get('name', ''),
            'region': row.get('region') or '',
            'title': row.get('title') or '',
            'url': row.get('url') or '',
            'excerpt': (row.get('excerpt') or '')[:400],
            'published': row.get('published'),
            'starts': row.get('starts'),
            'deadline': row.get('deadline'),
        })
    states = {row['id']: row for row in engine.source_listing()}
    got = {}
    for row in items:
        got[row['sourceId']] = got.get(row['sourceId'], 0) + 1
    sources = []
    for source_id in ids:
        src = catalog.get(source_id, {})
        col = (states.get(source_id) or {}).get('collection') or {}
        sources.append({
            'id': source_id,
            'name': src.get('name', source_id),
            'status': col.get('status') or 'pending',
            'count': got.get(source_id, 0),
            'message': col.get('message') or '',
        })
    OUT.write_text(json.dumps({
        'collectedAt': engine.now(),
        'sources': sources,
        'items': items,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'notices {len(items)}', flush=True)

def collect():
    now = dt.datetime.now(engine.KST)
    if '--force' not in sys.argv and not allowed(now):
        print('밖 시간입니다. 오전 9시부터 밤 10시 전에만 수집합니다.', flush=True)
        return 0
    if '--force' not in sys.argv and recent(now):
        print('최근 수집이 있어 이번 회차는 건너뜁니다.', flush=True)
        return 0
    ids = public_ids()
    app = engine.Engine()
    if not app.start(ids):
        print('이미 수집 중입니다.', flush=True)
        return 0
    while app.progress['running']:
        app.stop.wait(0.5)
    STAMP.write_text(engine.now(), encoding='utf-8')
    export(ids)
    return 0

if __name__ == '__main__':
    raise SystemExit(collect())
