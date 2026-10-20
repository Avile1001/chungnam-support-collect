"""Public notice collector. No browser automation, logins or access-control bypass."""
from __future__ import annotations
import concurrent.futures as futures
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from urllib import error, parse, request, robotparser
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
# Optional local configuration. Never evaluates shell expressions.
if (ROOT / '.env').exists():
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            if key.strip() in {'CNSP_API_KEY','DATA_DIR'}: os.environ.setdefault(key.strip(),value.strip().strip('"\''))
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'notices.sqlite3'
KST = dt.timezone(dt.timedelta(hours=9))
UA = 'ChungnamNoticeCollector/1.0'
LOG = logging.getLogger('collector')
SUPPORT = re.compile(r'지원|모집|공모|창업|사업화|참가|교육|입주|바우처|보증|자금|판로|컨설팅|멘토링')
OTHER = re.compile(r'채용|합격|입찰|낙찰|직원.*공개|인사위원|공시송달|분묘|도로명|과태료|선정\s*결과|선정자|평가위원|용역|입법예고|성능점검 테스트')
SKIP_TITLE = re.compile(r'^(더보기|이전글|다음글|이전|다음|목록|홈|로그인|공고보기|상세보기|바로가기|새창|첨부|첨부파일|다운로드|조회)$')
DETAIL = re.compile(r'wr_id=|/view(?:[./?]|$)|/read(?:[./?]|$)|detail|[?&](?:seq|pds_no|boardNo|nttId|notAncmtMgtNo|mgt_no|not_ancmt_mgt_no)=|/bbs/[^/]+/\d+|/board/[^/]+/\d+|[?&]mode=V', re.I)
DATE = r'(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})'
RANGE = re.compile(DATE + r'\.?\s*(?:\([^)]*\))?\s*[~∼～–]\s*' + DATE)
NAV = re.compile(r'사업공고|지원사업|모집공고|일반공고|고시.?공고|공지사항|공모.?입찰|공고.?소식|창업지원프로그램')
TRACKING = {'pn','page','pageIndex','pageNo','PageNo','GotoPage','cpage','utm_source','utm_medium','utm_campaign'}

def now(): return dt.datetime.now(KST).isoformat(timespec='seconds')
def sources(): return json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS notices(
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
          excerpt TEXT, published TEXT, starts TEXT, deadline TEXT, kind TEXT,
          first_seen TEXT, last_seen TEXT, updated TEXT, fingerprint TEXT);
        CREATE INDEX IF NOT EXISTS notices_source ON notices(source_id);
        CREATE INDEX IF NOT EXISTS notices_dates ON notices(deadline,published);
        CREATE TABLE IF NOT EXISTS source_status(source_id TEXT PRIMARY KEY,
          status TEXT, message TEXT, attempted TEXT, succeeded TEXT, count INTEGER, pages INTEGER);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
          started TEXT, finished TEXT, report TEXT);
        ''')

def canonical(url):
    u = parse.urlsplit(url)
    q = sorted((k,v) for k,v in parse.parse_qsl(u.query, keep_blank_values=True) if k not in TRACKING)
    return parse.urlunsplit((u.scheme,u.netloc.lower(),u.path,parse.urlencode(q),''))

def clean(text): return re.sub(r'\s+',' ',text).strip()
def iso(parts):
    try: return dt.date(*map(int,parts)).isoformat()
    except (ValueError,TypeError): return None

def dates(text):
    """Keep registration date distinct from application period; never use an arbitrary date as deadline."""
    m = RANGE.search(text)
    start,end = (iso(m.groups()[:3]),iso(m.groups()[3:])) if m else (None,None)
    pub = re.search(r'(?:등록일|작성일|게시일|등록날짜)\s*[:：]?\s*'+DATE,text)
    published = iso(pub.groups()) if pub else None
    # One date in a list row is usually publication, but don't assume in unknown adapters.
    return published,start,end

def link_url(a, base, src):
    href = a.get('href','').strip()
    if not href or href.startswith(('#','javascript:')):
        js = (a.get('onclick','')+' '+href)
        # Resolve only explicitly configured functions. Never execute page JavaScript.
        for rule in src.get('js_rules',[]):
            m = re.search(rule['pattern'],js)
            if m:
                href = rule['url'].format(*[parse.quote(x,safe='') for x in m.groups()])
                break
        else: return None
    url = parse.urljoin(base,href)
    u,b = parse.urlsplit(url),parse.urlsplit(base)
    if u.scheme not in ('http','https') or u.hostname.removeprefix('www.') != b.hostname.removeprefix('www.'): return None
    return url

def parse_notices(html, base, src):
    soup = BeautifulSoup(html,'html.parser')
    for node in soup.select('script,style,nav,header,footer'): node.decompose()
    found = {}
    for a in soup.select(src.get('link_selector','a[href]')):
        title = clean(a.get_text(' ',strip=True))
        if len(title)<4 or len(title)>400 or SKIP_TITLE.fullmatch(title): continue
        if not re.search(r'[가-힣A-Za-z]', title): continue
        url = link_url(a,base,src)
        if not url: continue
        if not DETAIL.search(url):
            parent = a.find_parent(['tr','li','article'])
            blob = clean(parent.get_text(' ', strip=True)) if parent is not None else ''
            if parent is None or len(title) < 8 or not re.search(DATE, blob): continue
        if any(x in url.lower() for x in ('download','logout','filedown')): continue
        row = a.find_parent('tr')
        if row is None:
            row = a.find_parent('li') or a.parent
        excerpt = clean(row.get_text(' ',strip=True))[:1200]
        published,start,end = dates(excerpt)
        if src.get('adapter') not in ('cnsp','ctp') and not re.search(r'(신청|접수|모집)\s*기간',excerpt):
            start=end=None
        dated=row.select_one('[title=게시일자], [title=등록일], .td_datetime')
        if dated:
            m=re.search(DATE,dated.get_text(' ',strip=True))
            if m: published=iso(m.groups())
        if src.get('adapter')=='gnuboard' and not published:
            d = row.select_one('.td_datetime, .bo_date, .date')
            if d:
                m = re.search(DATE,d.get_text(' ',strip=True))
                if m: published=iso(m.groups())
        url = canonical(url)
        if url not in found:
            found[url] = dict(title=title,url=url,excerpt=excerpt,published=published,
                              starts=start,deadline=end,kind='기타 공고' if OTHER.search(title) or not SUPPORT.search(title) else '지원·모집')
    return list(found.values())

class FetchError(Exception): pass
class Blocked(FetchError): pass

def validate_url(url):
    u=parse.urlsplit(url)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:
        raise Blocked('허용되지 않는 주소')
    try:
        addresses=socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM)
    except socket.gaierror:
        if not request.getproxies(): raise
        addresses=[]  # The configured system proxy resolves public hostnames.
    if u.hostname.lower()=='localhost' or u.hostname.lower().endswith(('.local','.internal')): raise Blocked('내부 주소 차단')
    try:
        if not ipaddress.ip_address(u.hostname).is_global: raise Blocked('내부 주소 차단')
    except ValueError: pass
    for info in addresses:
        if not ipaddress.ip_address(info[4][0]).is_global: raise Blocked('내부 네트워크 주소 차단')
    return u

class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        # Fetcher follows redirects explicitly so robots is checked at every hop.
        return None

class Fetcher:
    def __init__(self):
        self.lock=threading.Lock(); self.hostlocks={}; self.robots={}; self.last={}
        self.opener=request.build_opener(SafeRedirect())

    def raw(self,url,data=None,headers=None):
        validate_url(url)
        req=request.Request(url,data=data,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,application/json','Accept-Encoding':'identity',**(headers or {})})
        try:
            with self.opener.open(req,timeout=15) as r:
                content=r.read(3_000_001)
                if len(content)>3_000_000: raise FetchError('응답 크기 제한 초과')
                charset=r.headers.get_content_charset()
                if not charset:
                    m=re.search(br'charset\s*=\s*["\']?([\w-]+)',content[:4096],re.I)
                    charset=m.group(1).decode('ascii') if m else 'utf-8'
                try: text=content.decode(charset)
                except (LookupError,UnicodeDecodeError): text=content.decode('cp949',errors='replace')
                return text
        except error.HTTPError as e:
            raise e

    def policy(self,url):
        u=parse.urlsplit(url); origin=f'{u.scheme}://{u.netloc}'
        if origin not in self.robots:
            rp=robotparser.RobotFileParser()
            try:
                text=self.raw(origin+'/robots.txt')
                if '<html' in text[:1000].lower(): raise FetchError('robots.txt 대신 HTML 응답')
                rp.parse(text.splitlines())
            except error.HTTPError as e:
                if e.code in (404,410): rp.parse([])
                elif e.code in (401,403): raise Blocked('robots.txt 접근 거부')
                else: raise FetchError(f'robots.txt 확인 실패 HTTP {e.code}')
            self.robots[origin]=rp
        rp=self.robots[origin]
        if not rp.can_fetch(UA,url): raise Blocked('robots.txt 수집 비허용')
        return max(1.0,float(rp.crawl_delay(UA) or 0))

    def get(self,url,data=None):
        for hop in range(6):
            host=parse.urlsplit(url).netloc
            with self.lock: lock=self.hostlocks.setdefault(host,threading.Lock())
            with lock:
                delay=self.policy(url)
                time.sleep(max(0,delay-(time.monotonic()-self.last.get(host,0))))
                try:
                    text=self.raw(url,data)
                    if any(w in text[:10000].lower() for w in ('cf-chl-','verify you are human','captcha-container','<title>site unavailable</title>')):
                        raise Blocked('자동접근 확인 화면: 수동 열람 필요')
                    return text,url
                except error.HTTPError as e:
                    if e.code in (301,302,303,307,308) and e.headers.get('Location'):
                        if e.code in (301,302,303): data=None
                        url=parse.urljoin(url,e.headers['Location'])
                        continue
                    raise FetchError(f'HTTP {e.code}') from e
                finally: self.last[host]=time.monotonic()
        raise FetchError('리다이렉트 횟수 초과')

def with_page(url,param,page):
    u=parse.urlsplit(url); q=dict(parse.parse_qsl(u.query,keep_blank_values=True));q[param]=str(page)
    return parse.urlunsplit((u.scheme,u.netloc,u.path,parse.urlencode(q),''))

def discover(html,base,src):
    soup=BeautifulSoup(html,'html.parser'); links=[]
    for a in soup.select('a[href]'):
        if NAV.search(clean(a.get_text(' ',strip=True))):
            u=link_url(a,base,src)
            if u and canonical(u)!=canonical(base) and u not in links: links.append(u)
    # Embedded public boards on the same origin only.
    for frame in soup.select('iframe[src]'):
        u=parse.urljoin(base,frame['src'])
        if parse.urlsplit(u).hostname==parse.urlsplit(base).hostname and u not in links: links.append(u)
    return links[:3]

def collect_source(src,fetcher):
    items={}; visited=set(); errors=[]; pages=0
    try:
        if src.get('adapter')=='cnsp' and os.getenv('CNSP_API_KEY'):
            # Documented, authenticated official API; never forward the key through redirects.
            raw=fetcher.raw('https://cnsp.or.kr/api/v1/supportproject.do?format=json',headers={'cnsp-api-key':os.environ['CNSP_API_KEY']})
            data=json.loads(raw)
            if data.get('return_code') not in (None,'0000'): raise FetchError('공식 API 오류: '+str(data.get('return_message','응답 확인 필요')))
            api_items=parse_cnsp_api(data)
            return api_items,'ok' if api_items else 'needs_adapter','공식 API 응답: '+str(len(api_items))+'건',1
        if src.get('adapter')=='ccei':
            # Public JSON endpoint used by the official homepage. robots policy is still enforced.
            raw,base=fetcher.get('https://ccei.creativekorea.or.kr/chungnam/main/mainContent.json',b'sPtime=now&kind=my')
            data=json.loads(raw); result=[]
            for row in data.get('program_list',[]):
                title=clean(str(row.get('PROGRAM_TITLE','')))
                if not title: continue
                _,start,end=dates(str(row.get('C_SDATE',''))+' ~ '+str(row.get('C_EDATE','')))
                result.append(dict(title=title,url='https://ccei.creativekorea.or.kr/chungnam/service/program_view.do?'+parse.urlencode({'no':row['SEQ'],'sMenuType':row.get('MENU_TYPE','')}),excerpt=title,published=None,starts=start,deadline=end,kind='지원·모집'))
            return result,'partial' if result else 'needs_adapter','홈페이지 프로그램 요약 피드만 수집; 전체 과거 목록 아님',1
        html,base=fetcher.get(src['url']); pages+=1;visited.add(base)
        items.update({r['url']:r for r in parse_notices(html,base,src)})
        queue=[]
        if src.get('mode')=='discover' or not items: queue=discover(html,base,src)
        if src.get('mode')=='board':
            queue += [with_page(base,src.get('page_param','page'),p) for p in range(2,min(10,src.get('pages',2))+1)]
        for url in queue[:5]:
            if url in visited: continue
            visited.add(url)
            try:
                page,actual=fetcher.get(url);pages+=1
                items.update({r['url']:r for r in parse_notices(page,actual,src)})
            except Exception as e: errors.append(str(e)[:180])
        if items:
            return list(items.values()),'partial' if errors else 'ok','; '.join(errors) or '공고 추출 완료',pages
        return [],'needs_adapter','공고 0건: 빈 게시판·동적 렌더링·링크 규칙 변경 확인 필요'+('; '+'; '.join(errors) if errors else ''),pages
    except Blocked as e: return list(items.values()),'blocked',str(e),pages
    except Exception as e: return list(items.values()),'error',f'{type(e).__name__}: {e}'[:400],pages

def parse_cnsp_api(data):
    out=[]
    if not isinstance(data.get('datas'),list): raise FetchError('공식 API datas 형식 변경')
    for r in data['datas']:
        title=clean(str(r.get('project_title','')))
        if not title: continue
        url=r.get('project_link') or 'https://cnsp.or.kr/project/view.do?'+parse.urlencode({'seq':r['project_seq']})
        if parse.urlsplit(url).scheme not in ('http','https'): continue
        def dateval(value):
            m=re.fullmatch(DATE,str(value or ''))
            return iso(m.groups()) if m else None
        out.append(dict(title=title,url=canonical(url),excerpt=str(r.get('project_gubun','')),
          published=None,starts=dateval(r.get('recruit_startdate')),deadline=dateval(r.get('recruit_enddate')),
          kind='기타 공고' if OTHER.search(title) else '지원·모집'))
    return out

def persist(src,items,status,message,pages):
    stamp=now()
    with connect() as c:
        for r in items:
            uid=hashlib.sha256((src['id']+'|'+r['url']).encode()).hexdigest()[:24]
            fingerprint=hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            old=c.execute('SELECT fingerprint,updated,first_seen FROM notices WHERE id=?',(uid,)).fetchone()
            updated=stamp if not old or old['fingerprint']!=fingerprint else old['updated']
            c.execute('''INSERT INTO notices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET title=excluded.title,excerpt=excluded.excerpt,
              published=excluded.published,starts=excluded.starts,deadline=excluded.deadline,
              kind=excluded.kind,last_seen=excluded.last_seen,updated=excluded.updated,fingerprint=excluded.fingerprint''',
              (uid,src['id'],r['title'],r['url'],r['excerpt'],r['published'],r['starts'],r['deadline'],r['kind'],old['first_seen'] if old else stamp,stamp,updated,fingerprint))
        # SQLite column count is kept explicit in tests.
        c.execute('''INSERT INTO source_status VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id)
          DO UPDATE SET status=excluded.status,message=excluded.message,attempted=excluded.attempted,
          succeeded=COALESCE(excluded.succeeded,source_status.succeeded),count=excluded.count,pages=excluded.pages''',
          (src['id'],status,message,stamp,stamp if status in ('ok','partial') else None,len(items),pages))

class Engine:
    def __init__(self):
        init_db();self.lock=threading.Lock();self.progress={'running':False,'done':0,'total':0};self.stop=threading.Event()
    def start(self,ids=None):
        if not self.lock.acquire(blocking=False): return False
        targets=[s for s in sources() if s.get('enabled',True) and (ids is None or s['id'] in ids)]
        self.progress={'running':True,'done':0,'total':len(targets),'started':now()}
        threading.Thread(target=self.run,args=(targets,),daemon=True).start();return True
    def run(self,targets):
        started=now(); report=[]
        try:
            fetcher=Fetcher()
            with futures.ThreadPoolExecutor(max_workers=4) as pool:
                jobs={pool.submit(collect_source,s,fetcher):s for s in targets}
                for job in futures.as_completed(jobs):
                    s=jobs[job]
                    try:
                        items,status,msg,pages=job.result();persist(s,items,status,msg,pages)
                    except Exception as e:
                        status,msg,items='error',str(e),[]
                        LOG.exception('저장 실패 %s',s['id'])
                        persist(s,[],status,msg,0)
                    report.append({'id':s['id'],'status':status,'count':len(items),'message':msg})
                    self.progress['done']+=1
                    LOG.info('%s %s %s건 %s',s['name'],status,len(items),msg)
            with connect() as c:
                c.execute('INSERT INTO runs(started,finished,report) VALUES(?,?,?)',(started,now(),json.dumps(report,ensure_ascii=False)))
                c.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 100)')
        finally:
            self.progress['running']=False;self.progress['finished']=now();self.lock.release()
    def schedule(self,hours=6):
        def loop():
            self.start()
            while not self.stop.wait(max(0.25,hours)*3600): self.start()
        threading.Thread(target=loop,daemon=True).start()

def listing():
    catalog={s['id']:s for s in sources()}
    today=dt.datetime.now(KST).date().isoformat()
    with connect() as c: rows=[dict(r) for r in c.execute('SELECT * FROM notices ORDER BY COALESCE(published,first_seen) DESC')]
    for r in rows:
        src=catalog.get(r['source_id'],{})
        r.update(source_name=src.get('name',r['source_id']),category=src.get('category',''),region=src.get('region',''))
        r['period_status']='기간 미확인'
        if r['starts'] and r['deadline']:
            r['period_status']='기간 종료' if r['deadline']<today else '시작 전' if r['starts']>today else '기간 내'
        r['stale']=(dt.datetime.now(KST)-dt.datetime.fromisoformat(r['last_seen'])).total_seconds()>7*86400
        r.pop('fingerprint',None)
    return rows

def source_listing():
    with connect() as c: states={r['source_id']:dict(r) for r in c.execute('SELECT * FROM source_status')}
    return [{**s,'collection':states.get(s['id'],{'status':'pending' if s.get('enabled',True) else 'disabled','message':s.get('note','아직 수집 전')})} for s in sources()]
