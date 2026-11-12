"""Small local HTTP adapter. The browser never receives paths on the host."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from pydicom.errors import InvalidDicomError

from .geometry import GeometryError
from .lab import Lab

STATIC = {
    '/':('index.html','text/html; charset=utf-8'),
    '/styles.css':('styles.css','text/css; charset=utf-8'),
    '/app.js':('app.js','text/javascript; charset=utf-8'),
    '/frame-loader.js':('frame-loader.js','text/javascript; charset=utf-8'),
    '/html2canvas.min.js':('html2canvas.min.js','text/javascript; charset=utf-8'),
}
WEB = Path(__file__).resolve().parents[1]/'web'


def integer(query,key,default,low,high):
    raw = query.get(key,[default])[0]
    if raw is None:
        return None
    try:
        n = int(raw)
    except (ValueError,TypeError) as e:
        raise ValueError(f'{key} must be an integer.') from e
    if not low<=n<=high:
        raise ValueError(f'{key} is out of range.')
    return n


def handler(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'HCC-TACE-Lab'

        def send_data(self, code, data, mime, **headers):
            self.send_response(code)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            for k,v in headers.items():
                self.send_header(k.replace('_','-'),v)
            self.end_headers()
            self.wfile.write(data)

        def json(self, code, value, **headers):
            self.send_data(code,json.dumps(value,ensure_ascii=False,allow_nan=False).encode(),
                           'application/json; charset=utf-8',**headers)

        def do_GET(self):
            path = urlsplit(self.path)
            try:
                host = urlsplit('//'+self.headers.get('Host','')).hostname
                if host not in ('127.0.0.1','localhost','::1'):
                    return self.json(403,{'error':'This viewer only accepts local requests.'})
                self.route(path.path,parse_qs(path.query,max_num_fields=12))
            except (BrokenPipeError,ConnectionResetError):
                pass  # Scrubbing slices cancels obsolete browser requests.
            except KeyError as e:
                self.json(404,{'error':str(e).strip("'")})
            except (GeometryError,ValueError) as e:
                self.json(422,{'error':str(e)})
            except (AttributeError,OSError,RuntimeError,InvalidDicomError) as e:
                self.log_error('DICOM read failed: %s',type(e).__name__)
                self.json(422,{'error':'This acquisition or its DICOM encoding could not be read.'})

        def route(self,path,q):
            if path in STATIC:
                name,mime = STATIC[path]
                return self.send_data(200,(WEB/name).read_bytes(),mime,
                    Content_Security_Policy="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; object-src 'none'")
            if path=='/health':
                return self.json(200,{'status':'ok','catalog_loaded':bool(lab.catalog.rows)})
            if path=='/api/cases':
                return self.json(200,lab.catalog.overview())
            if m:=re.fullmatch(r'/api/cases/(HCC_\d+)',path):
                return self.json(200,lab.catalog.case(m[1]))
            if m:=re.fullmatch(r'/api/series/([\d.]{3,100})(?:/(slice|volume))?',path):
                uid,kind = m.groups()
                if kind is None:
                    return self.json(200,lab.series(uid))
                stack = integer(q,'stack',0,0,999)
                segment = integer(q,'segment',None,1,65535)
                if kind=='volume':
                    if segment is None:
                        raise ValueError('Select a segment.')
                    return self.json(200,lab.measure(uid,stack,segment))
                n = integer(q,'index',0,0,10000)
                level = integer(q,'level',50,-1000,3000)
                width = integer(q,'width',350,2,3000)
                return self.send_data(200,lab.image(uid,stack,n,level,width,segment),'image/png')
            if path=='/api/report':
                case = q.get('case',[''])[0]
                report = lab.report(case,q.get('pre',[''])[0],integer(q,'preStack',0,0,999),
                                    q.get('post',[''])[0],integer(q,'postStack',0,0,999),
                                    integer(q,'segment',None,1,65535))
                report['exported_utc'] = datetime.now(timezone.utc).isoformat()
                return self.json(200,report,Content_Disposition=f'attachment; filename="{case}_report.json"')
            raise KeyError('Route not found.')
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=Path('/data'))
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8000)
    a = p.parse_args()
    with ThreadingHTTPServer((a.host,a.port),handler(Lab(a.data))) as server:
        print(f'HCC-TACE Lab · http://{a.host}:{a.port} · {a.data}',flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
