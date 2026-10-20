import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import engine
import run

class ParserTests(unittest.TestCase):
    def test_cnsp_dates_and_duplicate(self):
        h='''<table><tr><td><a href="/project/view.do?seq=9&pn=1">충남 창업 지원사업 모집 공고</a></td><td>신청기간 : 2026-10-01 ~ 2026-10-30</td><td><a href="/project/view.do?seq=9">공고보기</a></td></tr></table>'''
        r=engine.parse_notices(h,'https://cnsp.or.kr/project/list.do',{'adapter':'cnsp'})
        self.assertEqual(len(r),1);self.assertEqual(r[0]['deadline'],'2026-10-30')
        self.assertEqual(r[0]['published'],None)
    def test_navigation_not_notice(self):
        h='<a href="/main/cms/?no=603">소상공인 화재보험료 지원</a><a href="javascript:alert(1)">충남 지원사업 모집 공고</a>'
        self.assertEqual(engine.parse_notices(h,'https://www.asan.go.kr/',{}),[])
    def test_posting_period_not_application(self):
        h='<table><tr><td><a href="?mgt_no=99">청년 지원사업 모집공고</a></td><td title="게시일자">2026-10-01</td><td>2026-10-01 ~ 2026-10-20</td></tr></table>'
        r=engine.parse_notices(h,'https://example.org/notice',{})[0]
        self.assertIsNone(r['deadline']);self.assertEqual(r['published'],'2026-10-01')
    def test_menu_without_date_dropped(self):
        h='<li><a href="/main/cms/?no=118">시정 비전과 목표</a></li>'
        self.assertEqual(engine.parse_notices(h,'https://www.asan.go.kr/',{}),[])
    def test_dated_row_without_detail_pattern(self):
        h='<li><a href="/board/content.do?id=9">소상공인 시설개선 안내</a><span>2026-10-01</span></li>'
        self.assertEqual(len(engine.parse_notices(h,'https://example.org/list',{})),1)
    def test_plain_notice_kept(self):
        h='<table><tr><td><a href="/board/view.do?seq=3">도로 점용 허가 안내</a></td></tr></table>'
        r=engine.parse_notices(h,'https://example.org/list',{})
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['kind'],'기타 공고')
    def test_same_site_www(self):
        h='<a href="https://asan.go.kr/?mgt_no=2">청년 창업 지원 모집 공고</a>'
        self.assertEqual(len(engine.parse_notices(h,'https://www.asan.go.kr/',{})),1)
    def test_bad_dates(self):
        self.assertIsNone(engine.dates('2026-13-01 ~ 2026-13-30')[1])
    def test_api_uses_recruit_dates(self):
        r=engine.parse_cnsp_api({'datas':[dict(project_seq=3,project_title='지원사업',project_startdate='2026-01-01',project_enddate='2026-12-31',recruit_startdate='2026-03-01',recruit_enddate='2026-03-10',project_link='')]})[0]
        self.assertEqual(r['deadline'],'2026-03-10');self.assertIn('seq=3',r['url'])
    def test_tracking_dedup(self):
        self.assertEqual(engine.canonical('https://a.org/view?seq=1&pn=2'),engine.canonical('https://a.org/view?pn=5&seq=1'))
    def test_block_does_not_retry_bypass(self):
        f=unittest.mock.Mock();f.get.side_effect=engine.Blocked('robots')
        rows,status,_,_=engine.collect_source({'id':'s','url':'https://x.org/'},f)
        self.assertEqual(status,'blocked');self.assertEqual(f.get.call_count,1)

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=engine.DB
        engine.DB=Path(self.tmp.name)/'db.sqlite';engine.init_db()
        self.src={'id':'s5'}
        self.row=dict(title='지원사업 모집',url='https://example.org/view?id=1',excerpt='모집',published=None,starts=None,deadline=None,kind='지원·모집')
    def tearDown(self):engine.DB=self.old;self.tmp.cleanup()
    def test_upsert_and_failed_run_keep_data(self):
        engine.persist(self.src,[self.row],'ok','ok',1)
        engine.persist(self.src,[self.row],'ok','ok',1)
        with engine.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM notices').fetchone()[0],1)
        self.row['title']='수정 지원사업 모집';engine.persist(self.src,[self.row],'ok','ok',1)
        engine.persist(self.src,[],'error','HTTP 503',0)
        self.assertEqual(engine.listing()[0]['title'],'수정 지원사업 모집')
        with engine.connect() as c:
            s=c.execute('SELECT * FROM source_status').fetchone()
            self.assertEqual(s['status'],'error');self.assertIsNotNone(s['succeeded'])
    def test_period_unknown(self):
        engine.persist(self.src,[self.row],'ok','ok',1)
        self.assertEqual(engine.listing()[0]['period_status'],'기간 미확인')
    def test_http_api_and_post_token(self):
        run.APP=engine.Engine();run.ARGS=types.SimpleNamespace(interval=6)
        server=run.ThreadingHTTPServer(('127.0.0.1',0),run.Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/api/state') as r:self.assertEqual(len(json.load(r)['sources']),51)
            with urlopen(base+'/') as r:self.assertIn('수집함',r.read().decode())
            with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/collect',data=b'{}'))
            self.assertEqual(e.exception.code,403)
            with patch.object(run.APP,'start',return_value=True) as start:
                req=Request(base+'/api/collect',data=b'{"ids":["s5"]}',headers={'X-Collector-Token':run.TOKEN,'Content-Type':'application/json'})
                with urlopen(req) as r:self.assertEqual(r.status,202)
                start.assert_called_once_with(['s5'])
            with urlopen(base+'/api/export.csv') as r:self.assertTrue(r.read().startswith(b'\xef\xbb\xbf'))
        finally:server.shutdown();server.server_close();t.join()

if __name__=='__main__':unittest.main()
