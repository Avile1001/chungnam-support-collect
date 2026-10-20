"""Run one local server process. Python 3.10+."""
import argparse
import csv
import io
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs
import webbrowser
import engine

TOKEN=secrets.token_urlsafe(32)
APP=None
ALLOWED={'127.0.0.1','localhost','[::1]'}

class Handler(BaseHTTPRequestHandler):
    def respond(self,status,data,mime='application/json; charset=utf-8',extra=None):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def host_ok(self):
        host=urlsplit('//'+self.headers.get('Host','')).hostname
        return host in ALLOWED
    def do_GET(self):
        if not self.host_ok():return self.respond(403,{'error':'허용되지 않는 Host'})
        path=urlsplit(self.path).path
        if path=='/api/state': return self.respond(200,{'progress':APP.progress,'sources':engine.source_listing(),'token':TOKEN,'interval_hours':ARGS.interval})
        if path=='/api/notices':return self.respond(200,{'items':engine.listing()})
        if path=='/api/export.csv':
            f=io.StringIO(newline='');w=csv.writer(f);w.writerow(['기관','제목','등록일','시작일','마감일','기간 상태','원문','마지막 확인'])
            for r in engine.listing():
                vals=[r[k] or '' for k in ['source_name','title','published','starts','deadline','period_status','url','last_seen']]
                w.writerow(["'"+s if s.startswith(('=','+','-','@','\t','\r')) else s for s in vals])
            return self.respond(200,('\ufeff'+f.getvalue()).encode(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="chungnam-notices.csv"'})
        names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/directory.html':'directory.html'}
        if path in names:
            p=engine.ROOT/'static'/names[path]
            mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[p.suffix]
            # Original standalone directory has inline JS; served only as a downloadable file.
            return self.respond(200,p.read_bytes(),mime,{'Content-Disposition':'attachment; filename="directory.html"'} if path=='/directory.html' else None)
        return self.respond(404,{'error':'찾을 수 없음'})
    def do_POST(self):
        if not self.host_ok():return self.respond(403,{'error':'Host 거부'})
        if self.headers.get('X-Collector-Token')!=TOKEN:return self.respond(403,{'error':'화면을 새로고침해 주세요.'})
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.respond(403,{'error':'Origin 거부'})
        if urlsplit(self.path).path!='/api/collect':return self.respond(404,{'error':'찾을 수 없음'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n>4096 or n<0:raise ValueError()
            body=json.loads(self.rfile.read(n) or b'{}');ids=body.get('ids')
            if ids is not None and (not isinstance(ids,list) or any(not isinstance(x,str) for x in ids)):raise ValueError()
            ok=APP.start(ids)
            self.respond(202 if ok else 409,{'started':ok})
        except (ValueError,TypeError,AttributeError):self.respond(400,{'error':'올바르지 않은 요청'})

def main():
    global APP,ARGS
    p=argparse.ArgumentParser();    p.add_argument('--port',type=int,default=8000);p.add_argument('--bind',default='127.0.0.1')
    p.add_argument('--interval',type=float,default=6);p.add_argument('--no-open',action='store_true');p.add_argument('--no-auto',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--source',action='append')
    ARGS=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(engine.DATA/'collector.log',encoding='utf-8')])
    APP=engine.Engine()
    if ARGS.once:
        APP.start(ARGS.source)
        while APP.progress['running']:APP.stop.wait(.2)
        print(json.dumps(engine.source_listing(),ensure_ascii=False,indent=2));return
    server=ThreadingHTTPServer((ARGS.bind,ARGS.port),Handler)
    if not ARGS.no_auto: APP.schedule(ARGS.interval)
    url=f'http://127.0.0.1:{ARGS.port}'
    print('\n충남 공고 수집기: '+url+'\n종료: Ctrl+C. '+('자동 수집은 꺼져 있습니다. 화면의 ‘지금 수집’으로 실행합니다.\n' if ARGS.no_auto else '서버가 켜진 동안 자동 수집됩니다.\n'), flush=True)
    if not ARGS.no_open:threading.Timer(1,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:APP.stop.set();server.server_close()

if __name__=='__main__':main()
