"""Local HTTP adapter for numbering, OCR and the existing measurement engine."""
from __future__ import annotations
import json
import math
import threading
from pathlib import Path
from urllib.parse import parse_qs
import label_ocr
from label_workflow import NamingSession

SESSION = None
LOCK = threading.RLock()
JOB = dict(running=False, done=0, total=0, error='', kind='')
APP = None


def initialize(app, folder=None):
    global APP, SESSION
    APP = app
    if folder:
        SESSION = NamingSession(folder)


def session():
    if SESSION is None:
        raise ValueError('请先选择照片文件夹')
    return SESSION


def state():
    with LOCK:
        data = session().state() if SESSION is not None else dict(folder='', images=[], mapping=[], experiment_id='', strain='')
        data['last_result'] = None
        if SESSION is not None:
            # Restore the most recent reversible direct rename after reopening.
            for ident, filename in reversed(list(SESSION._transactions.items())):
                try:
                    path = Path(filename).resolve()
                    path.relative_to(SESSION.output_root.resolve())
                    record = json.loads(path.read_text(encoding='utf-8'))
                    if record.get('mode') == 'rename' and record.get('status') == 'complete':
                        data['last_result'] = dict(ok=True, mode='rename', transaction_id=ident,
                            output_dir=str(path.parent), manifest_path=record.get('manifest_path',''),
                            count=len(record.get('rows', [])), can_undo=True)
                        break
                except (OSError, ValueError, TypeError):
                    continue
        available, message = label_ocr.availability()
        data.update(ok=True, ocr_available=available, ocr_message=message)
        return data


def busy_check():
    if JOB['running']:
        raise ValueError('当前任务正在运行，请等待完成')


def start_job(kind, total, fn):
    with LOCK:
        busy_check()
        JOB.update(running=True, done=0, total=total, error='', kind=kind)
    def run():
        try:
            fn()
        except Exception as exc:
            JOB['error'] = str(exc)
        finally:
            JOB['running'] = False
    threading.Thread(target=run, daemon=True).start()


def handle_get(handler, parsed):
    if parsed.path == '/naming/state':
        handler._json(state()); return True
    if parsed.path == '/naming/job':
        handler._json(dict(ok=True, **JOB)); return True
    if parsed.path == '/naming/image':
        try:
            ident = parse_qs(parsed.query).get('id', [''])[0]
            row = next(r for r in session().state()['images'] if r['id'] == ident)
            from PIL import Image, ImageOps
            import io
            with Image.open(row['image_path']) as src:
                im = ImageOps.exif_transpose(src).convert('RGB')
                im.thumbnail((2200, 2200))
                buf = io.BytesIO(); im.save(buf, 'JPEG', quality=92)
            data = buf.getvalue()
            handler.send_response(200); handler.send_header('Content-Type', 'image/jpeg')
            handler.send_header('Content-Length', str(len(data))); handler.end_headers(); handler.wfile.write(data)
        except Exception as exc:
            handler._json(dict(ok=False, error=str(exc)))
        return True
    return False


def handle_post(handler, path):
    if not path.startswith('/naming/'):
        return False
    global SESSION
    try:
        origin = handler.headers.get('Origin')
        if origin and origin != 'http://' + handler.headers.get('Host', ''):
            raise ValueError('仅允许本机软件页面发起操作')
        size = int(handler.headers.get('Content-Length', '0'))
        if size > 2_000_000:
            raise ValueError('请求过大')
        req = json.loads(handler.rfile.read(size).decode('utf-8') or '{}')
        with LOCK:
            busy_check()
            if path == '/naming/pick':
                folder = APP.pick_folder()
                if folder:
                    SESSION = NamingSession(folder)
                result = state()
            elif path == '/naming/configure':
                session().configure(req.get('mapping', []), req.get('experiment_id', ''), req.get('strain', ''))
                result = state()
            elif path == '/naming/assign':
                session().assign(req['id'], req.get('code', ''), req.get('replicate', ''), req.get('confirmed', False))
                result = dict(ok=True)
            elif path == '/naming/preview':
                result = session().preview(req.get('mode', 'copy'))
                result['ok'] = True
            elif path == '/naming/apply':
                result = session().apply(req['token'], req.get('mode', 'copy'),
                                         dish_mm=req.get('dish_mm'), plug_mm=req.get('plug_mm'))
                result['ok'] = True
            elif path == '/naming/undo':
                result = session().undo(req['transaction_id'])
                if result is None: result = {}
                result['ok'] = True
            elif path == '/naming/ocr':
                snap = session().state()
                codes = [r['code'] for r in snap['mapping']]
                if not codes: raise ValueError('请先保存编号对应表')
                ids = req.get('ids')
                rows = [r for r in snap['images'] if not ids or r['id'] in ids]
                roi = req.get('roi')
                if roi is not None and len(rows) != 1:
                    raise ValueError('框选识别一次只能处理一张图片')
                available, message = label_ocr.availability()
                if not available: raise ValueError(message)
                def ocr_work():
                    for idx, row in enumerate(rows):
                        if row.get('confirmed'):
                            JOB['done'] = idx + 1; continue
                        try:
                            suggestion = label_ocr.recognize(row['image_path'], codes, roi)
                        except Exception as exc:
                            suggestion = dict(code='',replicate='',ocr_text='识别失败：'+str(exc),ocr_score=None)
                        with LOCK:
                            session().suggest(row['id'], **{k:suggestion[k] for k in ('code','replicate','ocr_text','ocr_score')})
                        JOB['done'] = idx + 1
                start_job('ocr', len(rows), ocr_work)
                result = dict(ok=True)
            elif path == '/naming/measure':
                snap = session().state()
                if not snap['images']: raise ValueError('当前文件夹没有照片')
                dish_mm = float(req.get('dish_mm', 86))
                plug_mm = float(req.get('plug_mm', 8))
                if not math.isfinite(dish_mm) or not math.isfinite(plug_mm) or not 0 < plug_mm < dish_mm:
                    raise ValueError('皿径须大于菌饼直径，且均为正数')
                metadata = {}
                if not req.get('use_original_names', False):
                    session().preview('copy')  # validates every assignment without modifying photos
                    session().write_manifest(dish_mm=dish_mm, plug_mm=plug_mm)
                    mapping = {r['code']:r['treatment'] for r in snap['mapping']}
                    for row in snap['images']:
                        metadata[str(Path(row['image_path']).resolve())] = dict(
                            treatment=mapping[row['code']], treatment_code=row['code'],
                            replicate=row.get('replicate',''), experiment_id=snap['experiment_id'],
                            strain=snap['strain'], original_name=row.get('original_name', row['name']))
                def measure_work():
                    APP.DISH_MM = dish_mm; APP.PLUG_MM = plug_mm
                    if APP.MODE == 'hq' and APP.engine_sam is not None:
                        APP.engine_sam.load_models(log=print)
                    APP.load_folder(snap['folder'], files=[Path(r['image_path']) for r in snap['images']],
                                    metadata=metadata, progress=lambda n: JOB.update(done=n))
                start_job('measure', len(snap['images']), measure_work)
                result = dict(ok=True)
            else:
                raise ValueError('未知操作')
        handler._json(result)
    except Exception as exc:
        handler._json(dict(ok=False, error=str(exc)))
    return True
