import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from labdesk import Store, csv_data, make_server


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'records.sqlite3'
        self.s = Store(self.path)
        self.asset = {'id':'lab2-pc07','name':'Lab computer','room':'Lab 2','kind':'Desktop','notes':'Sample only'}

    def add(self):
        self.s.asset(self.asset)
        return self.s.incident({'asset_id':'LAB2-PC07','title':'No network','priority':'High','due':'2026-10-20','note':'Cable checked'})

    def test_persistence_and_complete_workflow(self):
        ident = self.add()
        for status,note in [('In progress','Changed test cable'),('Resolved','Replaced damaged patch cable'),('Open','Fault returned')]:
            i = self.s.snapshot()['incidents'][0]
            self.s.progress({'id':ident,'status':status,'note':note,'updated':i['updated']})
        saved = Store(self.path).snapshot()
        self.assertEqual(saved['incidents'][0]['status'],'Open')
        self.assertEqual([e['status'] for e in saved['events']],['Open','In progress','Resolved','Open'])
        self.assertEqual(saved['assets'][0]['id'],'LAB2-PC07')

    def test_duplicate_ids_case_insensitive(self):
        self.s.asset(self.asset)
        with self.assertRaisesRegex(ValueError,'already exists'):
            self.s.asset({**self.asset,'id':'LAB2-PC07'})
        self.assertEqual(len(self.s.snapshot()['assets']),1)

    def test_asset_edit_keeps_history(self):
        self.add()
        self.s.asset({**self.asset,'room':'Lab 3'},True)
        self.assertEqual(self.s.snapshot()['assets'][0]['room'],'Lab 3')
        self.assertEqual(len(self.s.snapshot()['events']),1)

    def test_orphan_invalid_priority_and_dates(self):
        good={'asset_id':'LAB2-PC07','title':'Fault','priority':'High','due':'','note':'Check'}
        with self.assertRaisesRegex(ValueError,'existing asset'):
            self.s.incident(good)
        self.s.asset(self.asset)
        for extra in [{'priority':'Urgent'},{'due':'2026-02-30'},{'due':'20261020'},{'note':' '},{'title':5}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.s.incident({**good,**extra})
        self.assertEqual(self.s.snapshot()['incidents'],[])

    def test_stale_update_is_rejected(self):
        self.add()
        i=self.s.snapshot()['incidents'][0]
        obj={'id':i['id'],'updated':i['updated'],'status':'Resolved','note':'Fixed'}
        self.s.progress(obj)
        with self.assertRaisesRegex(ValueError,'another tab'):
            self.s.progress(obj)
        self.assertEqual(len(self.s.snapshot()['events']),2)

    def test_invalid_status_missing_id_and_blank_update(self):
        self.add()
        i=self.s.snapshot()['incidents'][0]
        for extra in [{'status':'Done'},{'note':''},{'id':'1'},{'id':999}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.s.progress({'id':i['id'],'updated':i['updated'],'status':'Resolved','note':'Fixed',**extra})
        self.assertEqual(len(self.s.snapshot()['events']),1)

    def test_import_is_atomic(self):
        self.s.asset(self.asset)
        with self.assertRaisesRegex(ValueError,'No rows'):
            self.s.import_assets({'csv':'id,name,room,kind,notes\nPC08,Spare,Lab 2,Desktop,\nlab2-pc07,Duplicate,Lab 1,PC,'})
        self.assertEqual(len(self.s.snapshot()['assets']),1)
        for raw in ['name,id\nWrong,1','id,name,room,kind,notes\nPC08,Missing','id,name,room,kind,notes\n']:
            with self.assertRaises(ValueError):
                self.s.import_assets({'csv':raw})
        self.s.import_assets({'csv':'\ufeffid,name,room,kind,notes\nPC08,"Spare, computer",Lab 2,Desktop,"line one\nline two"'})
        self.assertEqual(self.s.snapshot()['assets'][1]['notes'],'line one\nline two')

    def test_backup_restore_and_nonempty_protection(self):
        self.add()
        original=self.s.snapshot()
        other=Store(Path(self.tmp.name)/'restored.sqlite3')
        other.restore({'backup':json.loads(json.dumps(original))})
        self.assertEqual(other.snapshot(),original)
        with self.assertRaisesRegex(ValueError,'empty database'):
            other.restore({'backup':original})
        self.assertEqual(other.snapshot(),original)

    def test_corrupt_restore_rolls_back(self):
        self.add()
        backup=self.s.snapshot()
        for mutation in ['orphan','missing_history','bad_date','wrong_status','missing_key','bad_record','huge_id']:
            bad=json.loads(json.dumps(backup))
            if mutation=='orphan': bad['incidents'][0]['asset_id']='MISSING'
            if mutation=='missing_history': bad['events']=[]
            if mutation=='bad_date': bad['incidents'][0]['due']='not-a-date'
            if mutation=='wrong_status': bad['events'][0]['status']='Done'
            if mutation=='missing_key': del bad['assets'][0]['name']
            if mutation=='bad_record': bad['assets'][0]=[]
            if mutation=='huge_id': bad['incidents'][0]['id']=2**80
            dest=Store(Path(self.tmp.name)/(mutation+'.sqlite3'))
            with self.subTest(case=mutation),self.assertRaises(ValueError): dest.restore({'backup':bad})
            self.assertEqual(dest.snapshot()['assets'],[])

    def test_unicode_and_csv_safety(self):
        self.s.asset({**self.asset,'name':'کمپیوٹر','notes':'=HYPERLINK("https://example.com")'})
        out=csv_data(self.s.snapshot()['assets'],['id','name','room','kind','notes']).decode('utf-8-sig')
        row=list(csv.DictReader(io.StringIO(out)))[0]
        self.assertEqual(row['name'],'کمپیوٹر')
        self.assertTrue(row['notes'].startswith("'="))
        self.assertTrue(self.s.snapshot()['assets'][0]['notes'].startswith('='))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.server=make_server(self.store,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        with urlopen(self.base+'/api/state') as r:self.token=json.load(r)['token']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def req(self,path,body=None,headers=None):
        h={'X-LabDesk-Token':self.token,'Content-Type':'application/json'}
        h.update(headers or {})
        raw=None if body is None else (body if isinstance(body,bytes) else json.dumps(body).encode())
        try:
            r=urlopen(Request(self.base+path,data=raw,headers=h),timeout=5)
        except HTTPError as e:r=e
        with r:return r.status,r.read(),r.headers

    def test_http_journey_and_exports(self):
        self.assertEqual(self.req('/api/assets',{'id':'PC1','name':'Demo','room':'Lab','kind':'PC'})[0],200)
        self.assertEqual(self.req('/api/incidents',{'asset_id':'PC1','title':'No display','priority':'Low','note':'Checked power'})[0],200)
        state=json.loads(self.req('/api/state')[1]);i=state['incidents'][0]
        self.assertEqual(self.req('/api/progress',{'id':i['id'],'updated':i['updated'],'status':'Resolved','note':'Replaced test cable'})[0],200)
        backup=json.loads(self.req('/api/backup')[1]);self.assertEqual(backup['incidents'][0]['status'],'Resolved');self.assertNotIn('token',backup)
        for path in ['/api/assets.csv','/api/incidents.csv']:
            code,body,h=self.req(path);self.assertEqual(code,200);self.assertIn('attachment',h['Content-Disposition']);self.assertIn(b'PC1',body)

    def test_reject_cross_origin_host_and_token(self):
        for headers in [{'Origin':'https://evil.example'},{'Host':'evil.example'},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.req('/api/state',headers=headers)[0],403)
        for headers in [{'X-LabDesk-Token':''},{'X-LabDesk-Token':'bad'},{'Origin':'https://evil.example'}]:
            self.assertEqual(self.req('/api/assets',{'id':'bad'},headers)[0],403)

    def test_bad_requests_and_static_allowlist(self):
        for value in [b'{',b'[]',b'null',b'\xff']:
            self.assertEqual(self.req('/api/assets',value)[0],400)
        self.assertEqual(self.req('/api/assets',{}, {'Content-Type':'text/plain'})[0],415)
        for path in ['/labdesk.py','/../labdesk.py','/data/labdesk.sqlite3','/missing']:
            self.assertEqual(self.req(path)[0],404)
        for path in ['/','/app.js','/style.css']:
            code,body,headers=self.req(path);self.assertEqual(code,200);self.assertGreater(len(body),100);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])


if __name__=='__main__':unittest.main()
