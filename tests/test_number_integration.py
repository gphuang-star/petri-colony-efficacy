import csv
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import label_ocr
import label_workflow
import app
import naming_http
from PIL import Image
import openpyxl


class NumberIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.srv = app.HTTPServer(('127.0.0.1', 0), app.Handler)
        self.url = 'http://127.0.0.1:' + str(self.srv.server_port)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        app.STATE.update(folder=str(self.folder), cards=[], files=[], measures=[])
        app.METADATA = {}
        naming_http.initialize(app)
        naming_http.SESSION = None
        naming_http.JOB.update(running=False,done=0,total=0,error='',kind='')

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()

    def post(self, path, value):
        req = urllib.request.Request(self.url+path, json.dumps(value).encode(), {'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def test_ocr_abstains_on_unknown_ambiguity_and_zero_replicate(self):
        self.assertEqual(label_ocr.select_candidate([('11',.99)], ['1','11'])['code'],'11')
        self.assertEqual(label_ocr.select_candidate([('12',.99)], ['1','2'])['code'],'')
        self.assertEqual(label_ocr.select_candidate([('1',.99),('2',.98)], ['1','2'])['code'],'')
        self.assertEqual(label_ocr.select_candidate([('1-0',.99)], ['1'])['code'],'')
        self.assertEqual(label_ocr.select_candidate([('11',.2)], ['11'])['code'],'')
        self.assertEqual(label_ocr.parse_text('０１-２'), ('01','2'))

    def test_measurement_manifest_keeps_selected_parameters(self):
        Image.new('RGB',(40,40),'white').save(self.folder/'camera.jpg')
        naming_http.SESSION = label_workflow.NamingSession(self.folder)
        ss=naming_http.SESSION
        ss.configure([{'code':'11','treatment':'CK'}], 'batch', 'strain')
        ss.assign(ss.state()['images'][0]['id'],'11','1',True)
        got=[]
        def loaded(folder,files,metadata,progress):
            got.append(metadata); progress(len(files))
        with patch.object(app,'MODE','fast'), patch.object(app,'load_folder',loaded):
            result=self.post('/naming/measure',{'dish_mm':90,'plug_mm':8})
            self.assertTrue(result['ok'],result)
            for _ in range(100):
                if not naming_http.JOB['running']: break
                time.sleep(.02)
        self.assertEqual(naming_http.JOB['error'],'')
        with Path(ss.manifest_path).open(encoding='utf-8-sig',newline='') as f:
            row=next(csv.DictReader(f))
        self.assertEqual(float(row['dish_diameter_mm']),90)
        self.assertEqual(float(row['plug_diameter_mm']),8)
        self.assertEqual(next(iter(got[0].values()))['treatment'],'CK')

    def test_direct_rename_undo_is_available_after_reopening(self):
        Image.new('RGB',(40,40),'white').save(self.folder/'camera.jpg')
        ss=label_workflow.NamingSession(self.folder)
        ss.configure([{'code':'11','treatment':'CK'}],'batch','strain')
        ss.assign(ss.state()['images'][0]['id'],'11','1',True)
        naming_http.SESSION=ss
        preview=self.post('/naming/preview',{'mode':'rename'})
        result=self.post('/naming/apply',{'mode':'rename','token':preview['token'],'dish_mm':90,'plug_mm':8})
        self.assertTrue(result['ok'],result)
        naming_http.SESSION=label_workflow.NamingSession(self.folder)
        restored=naming_http.state()['last_result']
        self.assertEqual(restored['transaction_id'],result['transaction_id'])
        self.assertTrue(restored['can_undo'])
        self.assertTrue(self.post('/naming/undo',{'transaction_id':restored['transaction_id']})['ok'])
        self.assertIsNone(naming_http.state()['last_result'])
        self.assertTrue((self.folder/'camera.jpg').is_file())

    def test_bad_image_does_not_shift_manual_or_excel_binding(self):
        bad=self.folder/'bad.jpg'; bad.write_bytes(b'broken')
        good=self.folder/'good.jpg'; Image.new('RGB',(60,60),'white').save(good)
        measures=[]
        for path,ok in ((bad,False),(good,True)):
            m=app.Measure.__new__(app.Measure)
            m.path=path; m.ok=ok; m.error='bad image' if not ok else ''; m.note=''
            m.manual_mm=m.manual_m=m.manual_cross=m.manual_center=None
            m.mmpp=1; m.cx=m.cy=m.r=0
            if ok:
                import numpy as np
                m.img=np.zeros((60,60,3),dtype='uint8'); m.w=m.h=60
                m.diam=m.m1=m.m2=20; m.mask=None
            measures.append(m)
        app.METADATA={str(good.resolve()):dict(treatment='药物全名',treatment_code='11',strain='菌株',experiment_id='批次',replicate='2')}
        cards=app.build_cards([bad,good],measures)
        self.assertEqual(len(cards),2)
        self.assertEqual(cards[1]['measure_index'],1)
        app.STATE.update(files=[bad,good],measures=measures,cards=cards)
        result=self.post('/manual',{'key':cards[1]['key'],'points':[[0,0],[10,0],[0,0],[0,20]]})
        self.assertTrue(result['ok'],result)
        self.assertEqual(measures[1].manual_mm,15)
        self.assertIsNone(measures[0].manual_mm)
        result=self.post('/export',[dict(key=cards[1]['key'],mm=15,m1=10,m2=20,status='人工')])
        self.assertTrue(result['ok'],result)
        wb=openpyxl.load_workbook(io.BytesIO(Path(result['path']).read_bytes()))
        row=list(wb['测量结果'].values)[1]
        self.assertEqual(row[1],'good.jpg')
        self.assertEqual(row[13],'药物全名')
        self.assertEqual(row[12],'11')
        wb.close()

    def test_explicit_treatments_group_camera_names_against_correct_ck(self):
        import excel_export
        items=[]
        for name,drug,mm in [('random123.jpg','CK',60),('IMG999.jpg','吡噻菌胺',20)]:
            class It: pass
            it=It();it.path=self.folder/name;it.treatment=drug;it.strain='菌株';it.experiment_id='实验'
            it.final_mm=lambda val=mm:val;it.final_m=lambda val=mm:(val,val);it.status=lambda:'自动'
            items.append(it)
        path=self.folder/'results.xlsx';excel_export.write_xlsx(path,items,86,8)
        wb=openpyxl.load_workbook(io.BytesIO(path.read_bytes()))
        rows=list(wb['处理汇总'].values)[1:]
        row=next(r for r in rows if r[1]=='吡噻菌胺')
        self.assertAlmostEqual(row[5],76.92,places=2)
        wb.close()

if __name__=='__main__': unittest.main()
