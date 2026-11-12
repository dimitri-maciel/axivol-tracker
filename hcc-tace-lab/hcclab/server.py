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
    '/segmentation-editor.js':('segmentation-editor.js','text/javascript; charset=utf-8'),
    '/pre-mask-editor.js':('pre-mask-editor.js','text/javascript; charset=utf-8'),
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

        def do_POST(self):
            """Only small, same-origin JSON requests can create local candidates."""
            try:
                host = self.headers.get('Host', '')
                if urlsplit('//' + host).hostname not in ('127.0.0.1', 'localhost', '::1'):
                    return self.json(403, {'error': 'This viewer only accepts local requests.'})
                origin = self.headers.get('Origin')
                if ((origin is not None and origin != 'http://' + host)
                        or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                    return self.json(403, {'error': 'Use the local app to create a candidate.'})
                operations = {'/api/candidates': lab.create_candidate, '/api/pre-edits': lab.create_pre_edit,
                              '/api/pre-refinements': lab.create_pre_refinement}
                if self.path not in operations:
                    return self.json(404, {'error': 'Route not found.'})
                if self.headers.get_content_type() != 'application/json':
                    return self.json(415, {'error': 'Send an application/json request.'})
                if self.headers.get('Transfer-Encoding'):
                    return self.json(400, {'error': 'Chunked requests are not supported.'})
                size = int(self.headers.get('Content-Length', '0'))
                limit = 131072 if self.path == '/api/pre-edits' else 8192
                if not 0 < size <= limit:
                    return self.json(413, {'error': f'Parameters must fit in {limit // 1024} KB.'})
                payload = json.loads(self.rfile.read(size))
                return self.json(201, operations[self.path](payload))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except KeyError as error:
                self.json(404, {'error': str(error).strip("'")})
            except (GeometryError, ValueError, UnicodeError) as error:
                self.json(422, {'error': str(error)})
            except (AttributeError, OSError, RuntimeError, InvalidDicomError) as error:
                self.log_error('Candidate generation failed: %s', type(error).__name__)
                self.json(422, {'error': 'The candidate could not be generated or saved. Check the acquisition and annotation storage.'})

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
            if path == '/api/candidates':
                return self.json(200, lab.list_candidates(q.get('case', [''])[0], q.get('series', [''])[0],
                                                         integer(q, 'stack', 0, 0, 999)))
            if path == '/api/pre-edits':
                segment = integer(q, 'segment', None, 1, 65535)
                if segment is None:
                    raise ValueError('Select a source segment.')
                return self.json(200, lab.list_pre_edits(q.get('case', [''])[0], q.get('series', [''])[0],
                                                       integer(q, 'stack', 0, 0, 999), segment))
            if m := re.fullmatch(r'/api/candidates/([a-f0-9]{32})(/export)?', path):
                candidate_id, export = m.groups()
                document = lab.candidates.metadata(candidate_id)
                lab.candidate_for_stack(candidate_id, document['series_uid'], document['stack_index'])
                if export:
                    return self.send_data(200, lab.candidates.export(candidate_id), 'application/zip',
                                          Content_Disposition=f'attachment; filename="{document["case_id"]}_{document["stage"]}_candidate_{candidate_id[:8]}.zip"')
                return self.json(200, document)
            if m:=re.fullmatch(r'/api/series/([\d.]{3,100})(?:/(slice|volume|voxel|mask))?',path):
                uid,kind = m.groups()
                if kind is None:
                    return self.json(200,lab.series(uid))
                stack = integer(q,'stack',0,0,999)
                if kind == 'voxel':
                    return self.json(200, lab.voxel(uid, stack, integer(q, 'index', 0, 0, 10000),
                                                   integer(q, 'row', 0, 0, 10000), integer(q, 'column', 0, 0, 10000)))
                segment = integer(q,'segment',None,1,65535)
                if kind=='volume':
                    if segment is None:
                        raise ValueError('Select a segment.')
                    return self.json(200,lab.measure(uid,stack,segment))
                n = integer(q,'index',0,0,10000)
                if kind == 'mask':
                    return self.send_data(200, lab.mask_image(uid, stack, n, segment, q.get('candidate', [None])[0]), 'image/png')
                level = integer(q,'level',50,-1000,3000)
                width = integer(q,'width',350,2,3000)
                return self.send_data(200,lab.image(uid,stack,n,level,width,segment,q.get('candidate',[None])[0],
                    q.get('mask_style', ['fill'])[0], integer(q, 'mask_opacity', 45, 0, 100)), 'image/png')
            if path=='/api/report':
                case = q.get('case',[''])[0]
                report = lab.report(case,q.get('pre',[''])[0],integer(q,'preStack',0,0,999),
                                    q.get('post',[''])[0],integer(q,'postStack',0,0,999),
                                    integer(q,'segment',None,1,65535),q.get('candidate',[None])[0],
                                    q.get('pre_candidate',[None])[0])
                report['exported_utc'] = datetime.now(timezone.utc).isoformat()
                return self.json(200,report,Content_Disposition=f'attachment; filename="{case}_report.json"')
            raise KeyError('Route not found.')
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=Path('/data'))
    p.add_argument('--annotations',type=Path,default=Path('/annotations'))
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8000)
    a = p.parse_args()
    with ThreadingHTTPServer((a.host,a.port),handler(Lab(a.data,a.annotations))) as server:
        print(f'HCC-TACE Lab · http://{a.host}:{a.port} · {a.data}',flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
