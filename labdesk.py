"""LabDesk: a dependency-free, local school IT maintenance notebook."""
import argparse
import csv
import io
import json
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATUSES = ('Open', 'In progress', 'Resolved')
ROOT = Path(__file__).resolve().parent


def text(obj, key, maximum=200, required=True):
    if not isinstance(obj, dict):
        raise ValueError('Expected a record object.')
    value = obj.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{key} must be text.')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError(f'{key} is required and must be at most {maximum} characters.' if required else f'{key} must be at most {maximum} characters.')
    return value


def day(value):
    if not isinstance(value, str):
        raise ValueError('due must be a date or blank.')
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ValueError('due must use YYYY-MM-DD.') from None
    return value


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY COLLATE NOCASE, name TEXT NOT NULL, room TEXT NOT NULL,
              kind TEXT NOT NULL, notes TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS incidents (
              id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
              title TEXT NOT NULL, priority TEXT NOT NULL, due TEXT NOT NULL,
              status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incidents(id),
              at TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self):
        with self.connect() as db:
            return {'version': 1, 'assets': [dict(r) for r in db.execute('SELECT * FROM assets ORDER BY id')],
                    'incidents': [dict(r) for r in db.execute('SELECT * FROM incidents ORDER BY id DESC')],
                    'events': [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id')]}

    def asset(self, obj, update=False):
        row = [text(obj, 'id', 60).upper(), text(obj, 'name'), text(obj, 'room', 100),
               text(obj, 'kind', 100), text(obj, 'notes', 3000, False)]
        with self.connect() as db:
            if update:
                cur = db.execute('UPDATE assets SET name=?,room=?,kind=?,notes=? WHERE id=?', row[1:] + row[:1])
                if not cur.rowcount:
                    raise ValueError('Asset not found.')
            else:
                try:
                    db.execute('INSERT INTO assets VALUES (?,?,?,?,?)', row)
                except sqlite3.IntegrityError:
                    raise ValueError('Asset ID already exists.') from None

    def incident(self, obj):
        asset_id, title = text(obj, 'asset_id', 60).upper(), text(obj, 'title')
        priority = text(obj, 'priority', 20)
        if priority not in ('Low', 'Medium', 'High'):
            raise ValueError('Choose Low, Medium or High priority.')
        due = day(obj.get('due', ''))
        note = text(obj, 'note', 5000)
        now = stamp()
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM assets WHERE id=?', (asset_id,)).fetchone():
                raise ValueError('Choose an existing asset.')
            cur = db.execute('INSERT INTO incidents(asset_id,title,priority,due,status,created,updated) VALUES (?,?,?,?,?,?,?)',
                             (asset_id, title, priority, due, 'Open', now, now))
            db.execute('INSERT INTO events(incident_id,at,status,note) VALUES (?,?,?,?)', (cur.lastrowid, now, 'Open', note))
            return cur.lastrowid

    def progress(self, obj):
        if type(obj.get('id')) is not int:
            raise ValueError('Incident ID must be an integer.')
        status = text(obj, 'status', 20)
        if status not in STATUSES:
            raise ValueError('Invalid status.')
        note, now = text(obj, 'note', 5000), stamp()
        with self.connect() as db:
            row = db.execute('SELECT updated FROM incidents WHERE id=?', (obj['id'],)).fetchone()
            if not row:
                raise ValueError('Incident not found.')
            if obj.get('updated') != row['updated']:
                raise ValueError('This incident changed in another tab. Refresh before updating.')
            db.execute('UPDATE incidents SET status=?,updated=? WHERE id=?', (status, now, obj['id']))
            db.execute('INSERT INTO events(incident_id,at,status,note) VALUES (?,?,?,?)', (obj['id'], now, status, note))

    def import_assets(self, obj):
        raw = text(obj, 'csv', 500000)
        reader = csv.DictReader(io.StringIO(raw.lstrip('\ufeff')))
        if reader.fieldnames != ['id', 'name', 'room', 'kind', 'notes']:
            raise ValueError('CSV columns must be id,name,room,kind,notes in that order.')
        rows = []
        for number, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'CSV row {number} has the wrong number of columns.')
            rows.append([text(row, 'id', 60).upper(), text(row, 'name'), text(row, 'room', 100), text(row, 'kind', 100), text(row, 'notes', 3000, False)])
            if len(rows) > 2000:
                raise ValueError('Import at most 2000 assets at once.')
        if not rows:
            raise ValueError('CSV contains no assets.')
        try:
            with self.connect() as db:
                db.executemany('INSERT INTO assets VALUES (?,?,?,?,?)', rows)
        except sqlite3.IntegrityError:
            raise ValueError('Import cancelled: duplicate asset ID. No rows were added.') from None

    def restore(self, obj):
        backup = obj.get('backup')
        if not isinstance(backup, dict) or backup.get('version') != 1:
            raise ValueError('Expected a LabDesk version 1 JSON backup.')
        # Restore into empty storage only; validate everything before committing.
        try:
            with self.connect() as db:
                if db.execute('SELECT 1 FROM assets UNION SELECT 1 FROM incidents LIMIT 1').fetchone():
                    raise ValueError('Restore requires an empty database. Start with a new --db path.')
                assets, incidents, events = (backup[k] for k in ('assets', 'incidents', 'events'))
                if not all(isinstance(x, list) and len(x) <= 50000 for x in (assets, incidents, events)):
                    raise ValueError('Invalid backup tables or size.')
                for a in assets:
                    db.execute('INSERT INTO assets VALUES (?,?,?,?,?)', (text(a,'id',60).upper(),text(a,'name'),text(a,'room',100),text(a,'kind',100),text(a,'notes',3000,False)))
                for i in incidents:
                    if type(i['id']) is not int or not 1 <= i['id'] < 2**63 or i['status'] not in STATUSES or i['priority'] not in ('Low','Medium','High'):
                        raise ValueError('Invalid incident in backup.')
                    for key in ('created','updated'):
                        datetime.fromisoformat(i[key])
                    db.execute('INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?)', (i['id'],text(i,'asset_id',60).upper(),text(i,'title'),i['priority'],day(i['due']),i['status'],i['created'],i['updated']))
                for e in events:
                    if type(e['id']) is not int or not 1 <= e['id'] < 2**63 or type(e['incident_id']) is not int or not 1 <= e['incident_id'] < 2**63 or e['status'] not in STATUSES:
                        raise ValueError('Invalid history in backup.')
                    datetime.fromisoformat(e['at'])
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (e['id'],e['incident_id'],e['at'],e['status'],text(e,'note',5000)))
                for i in incidents:
                    history = db.execute('SELECT status FROM events WHERE incident_id=? ORDER BY id DESC LIMIT 1', (i['id'],)).fetchone()
                    if not history or history['status'] != i['status']:
                        raise ValueError('Backup history is missing or inconsistent.')
        except (KeyError, TypeError, sqlite3.IntegrityError):
            raise ValueError('Invalid backup structure or references. Nothing was restored.') from None


def csv_data(rows, columns):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        # Neutralize spreadsheet formulas in exported user text.
        writer.writerow([("'" + str(row[c])) if str(row[c]).lstrip().startswith(('=', '+', '-', '@')) else row[c] for c in columns])
    return out.getvalue().encode('utf-8-sig')


def make_server(store, port=8768):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, kind='application/json; charset=utf-8', filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            return host == expected and self.headers.get('Origin', f'http://{expected}') == f'http://{expected}' and self.headers.get('Sec-Fetch-Site') != 'cross-site'

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {'error':'Open LabDesk using its 127.0.0.1 address.'})
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.send(200, {**store.snapshot(), 'token':token, 'today':date.today().isoformat()})
            if path == '/api/backup':
                return self.send(200, json.dumps(store.snapshot(), ensure_ascii=False, indent=2).encode(), filename='labdesk-backup.json')
            if path in ('/api/assets.csv', '/api/incidents.csv'):
                table = 'assets' if 'assets.csv' in path else 'incidents'
                cols = ['id','name','room','kind','notes'] if table == 'assets' else ['id','asset_id','title','priority','due','status','created','updated']
                return self.send(200, csv_data(store.snapshot()[table], cols), 'text/csv; charset=utf-8', f'labdesk-{table}.csv')
            files = {'/':('index.html','text/html; charset=utf-8'), '/app.js':('app.js','text/javascript; charset=utf-8'), '/style.css':('style.css','text/css; charset=utf-8')}
            if path in files:
                file, kind = files[path]
                return self.send(200, (ROOT/'web'/file).read_bytes(), kind)
            self.send(404, {'error':'Not found.'})

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-LabDesk-Token',''), token):
                return self.send(403, {'error':'Refresh LabDesk before saving.'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 8_000_000:
                    return self.send(413, {'error':'Request must be between 1 byte and 8 MB.'})
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                    return self.send(415, {'error':'Expected JSON.'})
                obj = json.loads(self.rfile.read(length))
                if not isinstance(obj, dict):
                    raise ValueError('Expected a JSON object.')
                routes = {'/api/assets':store.asset, '/api/assets/edit':lambda x:store.asset(x, True), '/api/incidents':store.incident,
                          '/api/progress':store.progress, '/api/import':store.import_assets, '/api/restore':store.restore}
                if self.path not in routes:
                    return self.send(404, {'error':'Not found.'})
                routes[self.path](obj)
                self.send(200, {'ok':True})
            except (ValueError, UnicodeDecodeError) as exc:
                self.send(400, {'error':str(exc)})
            except sqlite3.Error:
                self.send(503, {'error':'Storage is unavailable. Check disk space and database permissions, then retry.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--db', type=Path, default=ROOT/'data'/'labdesk.sqlite3')
    args = parser.parse_args()
    try:
        server = make_server(Store(args.db), args.port)
    except (OSError, sqlite3.Error) as exc:
        print(f'Cannot start LabDesk: {exc}. Try another --port or writable --db path.', file=sys.stderr)
        return 1
    print(f'LabDesk: http://127.0.0.1:{server.server_port}\nDatabase: {args.db.resolve()}\nPress Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
