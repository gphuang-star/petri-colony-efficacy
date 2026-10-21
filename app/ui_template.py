# -*- coding: utf-8 -*-
"""v3 annotation page: circle / polygon / FREEHAND BRUSH, plus a one-click
'满皿' shortcut for the hairy near-full plates."""
from __future__ import annotations
import argparse, base64, csv, html, io, json, sys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from measure_colony_image import imread_unicode, detect_dish

PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
 body{margin:0;background:#151515;color:#eaeaea;font-family:'Microsoft YaHei',system-ui,sans-serif}
 header{position:sticky;top:0;z-index:30;background:#20242b;padding:8px 14px;box-shadow:0 2px 8px #000a;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 header b{color:#8cf}button{background:#555;color:#fff;border:0;border-radius:5px;padding:6px 11px;cursor:pointer;font-size:13px}
 button.on{background:#2f6fd0}button.green{background:#2e8b57}
 input[type=range]{width:110px}input[type=file]{display:none}
 .grid{display:flex;flex-wrap:wrap;gap:12px;padding:12px}
 .card{background:#232323;border-radius:8px;padding:8px;width:430px;border:2px solid #333}
 .card.done{border-color:#2e8b57}.card.issue{border-color:#b22222}
 .imgwrap{position:relative;line-height:0;cursor:crosshair;user-select:none;touch-action:none}
 .imgwrap img{width:100%;display:block;border-radius:5px;pointer-events:none}
 .imgwrap svg{position:absolute;left:0;top:0;width:100%;height:100%;touch-action:none}
 .nm{font-size:12px;color:#ffd;word-break:break-all;margin:6px 0 2px;line-height:1.4}
 .mt{font-size:12px;color:#9f9;line-height:1.6}.or{font-size:12px;color:#8cf}
 .tags{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
 .tag{font-size:11px;padding:3px 7px;border-radius:10px;background:#3a3a3a;color:#ccc;cursor:pointer;border:1px solid #444}
 .tag.on{background:#b22222;color:#fff}.tag.ok.on{background:#2e8b57}
 textarea{width:100%;box-sizing:border-box;background:#1b1b1b;color:#eee;border:1px solid #444;border-radius:5px;padding:6px;font-size:12px;min-height:38px}
 .tools{display:flex;gap:4px;margin:4px 0;flex-wrap:wrap}.tools button{padding:3px 8px;font-size:12px}
 .tools button.hot{background:#d4a017;color:#000}
 #err{display:none;margin:12px;padding:10px;background:#5a1010;border:1px solid #f66;border-radius:6px}
</style></head><body>
<header>
 <b>__TITLE__</b>
 <span id="cnt" style="color:#fc6;font-weight:bold">加载中…</span>
 <button class="green" onclick="exportCsv()">导出CSV</button>
 <button onclick="document.getElementById('imp').click()">导入CSV</button>
 <input type="file" id="imp" accept=".csv" onchange="importCsv(this)">
 <button onclick="clearAll()">清空</button>
 <span>模式：</span>
 <button id="m-circle" class="on" onclick="setMode('circle')">圆形</button>
 <button id="m-poly" onclick="setMode('poly')">多边形</button>
 <button id="m-brush" onclick="setMode('brush')">✏️涂抹</button>
 <span id="brushctl" style="display:none">笔刷 <input id="bsize" type="range" min="6" max="120" value="34"> <button id="eraser" onclick="toggleEraser()">橡皮：关</button></span>
 <button onclick="setFilter('all')">全部</button><button onclick="setFilter('todo')">未处理</button>
 <span style="font-size:12px;color:#999">涂抹=按住拖着画；多边形=点击加点、点首点闭合</span>
</header>
<div id="err"></div>
<div class="grid" id="grid"></div>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
"use strict";
try{
 var DATA=JSON.parse(document.getElementById('data').textContent);
 var TAGS=__TAGS__;
 var KEY='colonyDrawV3::'+__TITLEJSON__;
 var STORE={}, FILTER='all', MODE='circle', ERASE=false;
 try{ STORE=JSON.parse(localStorage.getItem(KEY)||'{}')||{}; }catch(e){ STORE={}; }
 function save(){ try{ localStorage.setItem(KEY,JSON.stringify(STORE)); }catch(e){} }
 function st(k){ if(!STORE[k]) STORE[k]={shape:null,tags:[],note:''}; if(!STORE[k].tags) STORE[k].tags=[]; return STORE[k]; }
 function has(s){ return !!(s.shape)||s.tags.length||s.note; }
 function isIssue(s){ return s.tags.some(function(t){return t!=='边界正确';}); }
 function brushR(){ var el=document.getElementById('bsize'); return el? +el.value : 34; }
 function rasterize(card, shape){
   var cv=document.createElement('canvas'); cv.width=card.w; cv.height=card.h;
   var ctx=cv.getContext('2d'); ctx.lineCap='round'; ctx.lineJoin='round'; ctx.strokeStyle='#fff'; ctx.fillStyle='#fff';
   shape.strokes.forEach(function(s){
     ctx.globalCompositeOperation = s.erase ? 'destination-out' : 'source-over';
     ctx.lineWidth = Math.max(2, s.r*2);
     if(s.pts.length===1){ ctx.beginPath(); ctx.arc(s.pts[0][0],s.pts[0][1],Math.max(1,s.r),0,6.2832); ctx.fill(); return; }
     ctx.beginPath(); s.pts.forEach(function(p,i){ i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]); }); ctx.stroke();
   });
   var im=ctx.getImageData(0,0,card.w,card.h).data;
   var n=0,sx=0,sy=0;
   for(var y=0;y<card.h;y++) for(var x=0;x<card.w;x++){ if(im[(y*card.w+x)*4+3]>128){ n++; sx+=x; sy+=y; } }
   if(!n) return null;
   var cx=sx/n, cy=sy/n;
   var y0=Math.round(cy)|0, x0=Math.round(cx)|0;
   function run(get){ var a=get(); if(!a) return null; }
   function spanX(y){ var l=-1,r=-1; for(var x=0;x<card.w;x++) if(im[(y*card.w+x)*4+3]>128){ if(l<0)l=x; r=x; } return [l,r]; }
   function spanY(x){ var t=-1,b=-1; for(var y=0;y<card.h;y++) if(im[(y*card.w+x)*4+3]>128){ if(t<0)t=y; b=y; } return [t,b]; }
   var sx1=spanX(y0), sy1=spanY(x0);
   if(!sx1||!sy1||sx1[0]<0||sy1[0]<0) return null;
   var mmx=card.ow/card.w*card.mmpp, mmy=card.oh/card.h*card.mmpp;
   return {area:n, cx:cx, cy:cy, m1:(sx1[1]-sx1[0]+1)*mmx, m2:(sy1[1]-sy1[0]+1)*mmy, area_mm: n*mmx*mmy};
 }
 function measure(card,shape){
   if(!shape||!card.mmpp) return null;
   var fx=card.ow/card.w*card.mmpp, fy=card.oh/card.h*card.mmpp;
   if(shape.type==='circle'){ var r=shape.r*fx; return {m1:2*r,m2:2*r}; }
   if(shape.type==='brush'){ var m=rasterize(card,shape); return m?{m1:m.m1,m2:m.m2,area_mm:m.area_mm}:null; }
   var P=shape.pts.map(function(p){return [p[0]*fx,p[1]*fy];}), n=P.length; if(n<3) return null;
   var A=0,cx=0,cy=0;
   for(var i=0;i<n;i++){ var x0=P[i][0],y0=P[i][1],x1=P[(i+1)%n][0],y1=P[(i+1)%n][1],cr=x0*y1-x1*y0; A+=cr; cx+=(x0+x1)*cr; cy+=(y0+y1)*cr; }
   if(Math.abs(A)<1e-9) return null; A/=2; cx/=6*A; cy/=6*A;
   function chord(ax,val){ var h=[]; for(var i=0;i<n;i++){ var a=P[i],b=P[(i+1)%n];
     var u=(ax==='h')?a[1]:a[0], v=(ax==='h')?b[1]:b[0];
     if((u<=val&&v>val)||(v<=val&&u>val)){ var t=(val-u)/(v-u); h.push((ax==='h')?(a[0]+t*(b[0]-a[0])):(a[1]+t*(b[1]-a[1]))); } }
     if(h.length<2) return 0; return Math.max.apply(null,h)-Math.min.apply(null,h); }
   return {m1:chord('h',cy),m2:chord('v',cx)};
 }
 function colorFor(c){ var s=st(c.stem); return 'card'+(has(s)?' done':'')+(isIssue(s)?' issue':''); }
 function updateCount(){ var n=0; DATA.forEach(function(c){ if(has(st(c.stem))) n++; });
   document.getElementById('cnt').textContent='已处理 '+n+'/'+DATA.length; }
 function buildCard(c){
   var s=st(c.stem), d=document.createElement('div'); d.className=colorFor(c);
   var tags=TAGS.map(function(t){ return '<span class="tag'+(s.tags.indexOf(t)>=0?' on':'')+(t==='边界正确'?' ok':'')+'">'+esc(t)+'</span>'; }).join('');
   d.innerHTML='<div class="imgwrap"><img src="data:image/jpeg;base64,'+c.b64+'"><svg viewBox="0 0 '+c.w+' '+c.h+'"></svg></div>'+
     '<div class="nm">'+esc(c.stem)+' <span style="color:#8cf">[原图 '+c.ow+'x'+c.oh+' · '+(c.mmpp?c.mmpp.toFixed(4)+' mm/px':'-')+']</span></div>'+
     '<div class="mt">自动='+(c.mean==null?'—':c.mean.toFixed(2))+' mm ｜ <span class="mmline"></span></div>'+
     '<div class="tools"><button class="act hot" data-act="full">一键满皿('+(c.plate_mm?c.plate_mm.toFixed(0):'?')+'mm=皿径)</button>'+
     '<button class="act" data-act="undo">撤销</button><button class="act" data-act="clear">清除</button></div>'+
     '<div class="tags">'+tags+'</div><textarea placeholder="备注（可选）">'+esc(s.note||'')+'</textarea>';
   var svg=d.querySelector('svg'), mmline=d.querySelector('.mmline'), ta=d.querySelector('textarea');
   var drag=null;
   function pt(ev){ var r=svg.getBoundingClientRect(); return [(ev.clientX-r.left)/r.width*c.w,(ev.clientY-r.top)/r.height*c.h]; }
   function paint(){
     while(svg.firstChild) svg.removeChild(svg.firstChild);
     var s2=st(c.stem), NS='http://www.w3.org/2000/svg';
     function mk(n,a){ var e=document.createElementNS(NS,n); for(var k in a) e.setAttribute(k,a[k]); return e; }
     var sh=s2.shape;
     if(sh){
       if(sh.type==='circle'){ svg.appendChild(mk('circle',{cx:sh.cx,cy:sh.cy,r:sh.r,fill:'rgba(255,255,0,.12)',stroke:'#ff0','stroke-width':3})); svg.appendChild(mk('circle',{cx:sh.cx,cy:sh.cy,r:5,fill:'#ff0'})); }
       else if(sh.type==='poly'){ var P=sh.pts;
         if(P.length>2) svg.appendChild(mk('polygon',{points:P.map(function(p){return p.join(',');}).join(' '),fill:'rgba(255,255,0,.12)',stroke:'#ff0','stroke-width':3}));
         P.forEach(function(p,j){ svg.appendChild(mk('circle',{cx:p[0],cy:p[1],r:7,fill:'#ff0','data-v':j,style:'cursor:move'})); }); }
       else if(sh.type==='brush'){ sh.strokes.forEach(function(stk){
           var d2=stk.pts.map(function(p,i){return (i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' ');
           svg.appendChild(mk('path',{d:d2,fill:'none',stroke:stk.erase?'#f33':'#ff0','stroke-width':Math.max(2,stk.r*2),'stroke-linecap':'round','stroke-linejoin':'round','stroke-opacity':stk.erase?'0.55':'0.45'})); }); }
     }
     var mm=measure(c,s2.shape);
     mmline.innerHTML = s2.shape ? (mm? ('人工框取 m1='+mm.m1.toFixed(2)+' m2='+mm.m2.toFixed(2)+' 均径=<b style="color:#ff0">'+((mm.m1+mm.m2)/2).toFixed(2)+' mm</b>') : '已框取（无效或缺标定）') : '尚未框取';
     d.className=colorFor(c);
   }
   svg.addEventListener('pointerdown',function(ev){
     ev.preventDefault(); ev.stopPropagation();
     var p=pt(ev), s2=st(c.stem), t=ev.target;
     if(MODE!=='brush' && t&&t.getAttribute&&t.getAttribute('data-v')!==null){ drag=parseInt(t.getAttribute('data-v'),10); try{svg.setPointerCapture(ev.pointerId);}catch(e){} return; }
     if(MODE==='circle'){ drag='new'; s2.shape={type:'circle',cx:p[0],cy:p[1],r:2}; }
     else if(MODE==='poly'){
       if(!s2.shape||s2.shape.type!=='poly') s2.shape={type:'poly',pts:[]};
       var P=s2.shape.pts;
       if(P.length>2 && Math.hypot(P[0][0]-p[0],P[0][1]-p[1])<22){ drag=null; save(); paint(); updateCount(); return; }
       P.push(p);
     } else {
       if(!s2.shape||s2.shape.type!=='brush') s2.shape={type:'brush',strokes:[]};
       drag=s2.shape.strokes.length; s2.shape.strokes.push({r:brushR(),pts:[p],erase:ERASE});
     }
     try{ svg.setPointerCapture(ev.pointerId); }catch(e){}
     save(); paint();
   });
   svg.addEventListener('pointermove',function(ev){
     if(drag===null) return; ev.preventDefault();
     var p=pt(ev), s2=st(c.stem); if(!s2.shape) return;
     if(s2.shape.type==='circle'&&drag==='new') s2.shape.r=Math.max(2,Math.hypot(p[0]-s2.shape.cx,p[1]-s2.shape.cy));
     else if(s2.shape.type==='poly'&&typeof drag==='number') s2.shape.pts[drag]=p;
     else if(s2.shape.type==='brush'&&typeof drag==='number'){ var stk=s2.shape.strokes[drag]; if(stk){ var q=stk.pts[stk.pts.length-1]; if(!q||Math.hypot(q[0]-p[0],q[1]-p[1])>2) stk.pts.push(p); } }
     save(); paint();
   });
   function end(){ if(drag!==null){ drag=null; save(); paint(); updateCount(); } }
   svg.addEventListener('pointerup',end); svg.addEventListener('pointercancel',end);
   Array.prototype.forEach.call(d.querySelectorAll('.act'),function(b){ b.onclick=function(){
     var s2=st(c.stem), act=b.getAttribute('data-act');
     if(act==='clear') s2.shape=null;
     else if(act==='undo'){ if(!s2.shape) return; if(s2.shape.type==='poly'){ s2.shape.pts.pop(); if(!s2.shape.pts.length) s2.shape=null; }
       else if(s2.shape.type==='brush'){ s2.shape.strokes.pop(); if(!s2.shape.strokes.length) s2.shape=null; } else s2.shape=null; }
     else if(act==='full'){ s2.shape={type:'circle',cx:c.dcx,cy:c.dcy,r:c.dr}; }
     save(); paint(); updateCount(); }; });
   Array.prototype.forEach.call(d.querySelectorAll('.tag'),function(el){ el.onclick=function(){
     var s2=st(c.stem), t=el.textContent;
     if(t==='边界正确'){ s2.tags=(s2.tags.indexOf(t)>=0)?[]:['边界正确']; }
     else { s2.tags=s2.tags.filter(function(x){return x!=='边界正确';}); if(s2.tags.indexOf(t)>=0) s2.tags=s2.tags.filter(function(x){return x!==t;}); else s2.tags.push(t); }
     save(); Array.prototype.forEach.call(d.querySelectorAll('.tag'),function(e2){ e2.className='tag'+(s2.tags.indexOf(e2.textContent)>=0?' on':'')+(e2.textContent==='边界正确'?' ok':''); });
     d.className=colorFor(c); updateCount(); }; });
   ta.addEventListener('input',function(){ st(c.stem).note=ta.value; save(); updateCount(); });
   paint(); return d;
 }
 function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
 function render(){ var g=document.getElementById('grid'); g.innerHTML='';
   DATA.forEach(function(c){ var s=st(c.stem); if(FILTER==='todo'&&has(s)) return; g.appendChild(buildCard(c)); }); updateCount(); }
 window.setMode=function(m){ MODE=m; ['circle','poly','brush'].forEach(function(k){ document.getElementById('m-'+k).className=(k===m?'on':''); });
   document.getElementById('brushctl').style.display=(m==='brush')?'inline':'none'; };
 window.toggleEraser=function(){ ERASE=!ERASE; document.getElementById('eraser').textContent='橡皮：'+(ERASE?'开':'关'); };
 window.setFilter=function(f){ FILTER=f; render(); };
 function csq(x){ return '"'+String(x==null?'':x).replace(/"/g,'""')+'"'; }
 window.exportCsv=function(){
  var NL=String.fromCharCode(10), out='\uFEFF图片名,原图文件,原图宽,原图高,mm每像素,模式,人工直径mm,人工m1mm,人工m2mm,形状JSON,问题标签,备注,自动直径mm'+NL;
  DATA.forEach(function(c){ var s=st(c.stem), mm=measure(c,s.shape), shape='';
    if(s.shape) shape=JSON.stringify(normalise(c,s.shape));
    out+=[csq(c.stem),csq(c.file||''),csq(c.ow),csq(c.oh),csq(c.mmpp),csq(s.shape?s.shape.type:''),
          csq(mm?((mm.m1+mm.m2)/2).toFixed(3):''),csq(mm?mm.m1.toFixed(3):''),csq(mm?mm.m2.toFixed(3):''),
          csq(shape),csq(s.tags.join('|')),csq(s.note),csq(c.mean==null?'':c.mean)].join(',')+NL; });
  var b=new Blob([out],{type:'text/csv;charset=utf-8'}), a=document.createElement('a');
  a.href=URL.createObjectURL(b); a.download='人工框取.csv'; document.body.appendChild(a); a.click();
  setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},1000);
 };
 function normalise(c,sh){ if(sh.type==='circle') return {type:'circle',cx:sh.cx/c.w,cy:sh.cy/c.h,r:sh.r/c.w};
   if(sh.type==='poly') return {type:'poly',pts:sh.pts.map(function(p){return [p[0]/c.w,p[1]/c.h];})};
   return {type:'brush',strokes:sh.strokes.map(function(s){ return {r:s.r/c.w,erase:!!s.erase,pts:s.pts.map(function(p){return [p[0]/c.w,p[1]/c.h];})}; })}; }
 function denorm(c,sh){ if(sh.type==='circle') return {type:'circle',cx:sh.cx*c.w,cy:sh.cy*c.h,r:sh.r*c.w};
   if(sh.type==='poly') return {type:'poly',pts:sh.pts.map(function(p){return [p[0]*c.w,p[1]*c.h];})};
   return {type:'brush',strokes:sh.strokes.map(function(s){ return {r:s.r*c.w,erase:!!s.erase,pts:s.pts.map(function(p){return [p[0]*c.w,p[1]*c.h];})}; })}; }
 function parseCsv(t){var rows=[],row=[],cur='',q=false;t=t.replace(/^\uFEFF/,'');
  for(var i=0;i<t.length;i++){var ch=t[i];
   if(q){ if(ch==='"'){ if(t[i+1]==='"'){cur+='"';i++;} else q=false; } else cur+=ch; }
   else { if(ch==='"')q=true; else if(ch===','){row.push(cur);cur='';} else if(ch==='\n'){row.push(cur);rows.push(row);row=[];cur='';} else if(ch!=='\r')cur+=ch; } }
  if(cur!==''||row.length){row.push(cur);rows.push(row);} return rows; }
 window.importCsv=function(inp){ var f=inp.files[0]; if(!f) return; var rd=new FileReader();
  rd.onload=function(){ var rows=parseCsv(rd.result), by={}; DATA.forEach(function(c){by[c.stem]=c;});
   for(var i=1;i<rows.length;i++){ var c=rows[i], key=(c[0]||'').trim(); if(!key||!by[key]) continue;
     var s=st(key); s.tags=(c[10]||'').split('|').filter(Boolean); s.note=c[11]||'';
     try{ var sh=JSON.parse(c[9]||'null'); if(sh) s.shape=denorm(by[key],sh); }catch(e){} }
   save(); render(); alert('导入完成'); }; rd.readAsText(f,'utf-8'); };
 window.clearAll=function(){ if(confirm('确定清空所有标注？')){ STORE={}; save(); render(); } };
 render();
}catch(e){ var b=document.getElementById('err'); b.style.display='block'; b.textContent='页面出错：'+e.message+' '+(e.stack||'').split('\n')[1]; }
})();
</script></body></html>"""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--res-dir", required=True); ap.add_argument("--mapping", required=True)
    ap.add_argument("--out-html", required=True); ap.add_argument("--title", default="人工框取")
    ap.add_argument("--max-px", type=int, default=760)
    ap.add_argument("--tags", default="边界正确,圈太大,圈太小,细毛满皿,长条形,中心偏移,抑制型=菌饼")
    a=ap.parse_args()
    res=Path(a.res_dir); qa=res/"qa"
    rows=list(csv.DictReader(open(res/"measurements_auto.csv",encoding="utf-8-sig",newline="")))
    mp={}
    for r in csv.DictReader(open(a.mapping,encoding="utf-8-sig",newline="")):
        mp[r.get("qa_key") or Path(r["filename"]).name]=(int(r["slide"]),r["topic"],r["treat"])
    cards=[]
    for r in rows:
        p=Path(r["image_path"]); key=r.get("qa_key") or p.stem
        f=qa/f"{key}_qa.jpg"
        if not f.exists(): continue
        try: mmpp=float(r.get("scale_mm_per_px") or 0)
        except Exception: mmpp=0
        try: mean=float(r.get("equivalent_diameter_mm") or "")
        except Exception: mean=None
        try:
            with Image.open(p) as o: ow,oh=o.size
        except Exception: ow,oh=1000,1000
        im=Image.open(f).convert("RGB"); im.thumbnail((a.max_px,a.max_px))
        buf=io.BytesIO(); im.save(buf,format="JPEG",quality=82)
        b64=base64.b64encode(buf.getvalue()).decode()
        # dish geometry (for the one-click full-plate button)
        dcx=dcy=dr=0.0; plate_mm=0.0
        try:
            full=imread_unicode(p); cx,cy,rad,_,_,_=detect_dish(full)
            if rad>0:
                sc = im.width/full.shape[1]
                dcx,dcy,dr = cx*sc, cy*sc, rad*sc
                plate_mm = 86.0
        except Exception: pass
        sl,tp,tr=mp.get(key,(None,"",""))
        cards.append(dict(stem=key,topic=tp,treat=tr,b64=b64,w=im.width,h=im.height,ow=ow,oh=oh,
                          mmpp=mmpp,mean=mean,dcx=dcx,dcy=dcy,dr=dr,plate_mm=plate_mm))
    data=json.dumps(cards,ensure_ascii=False).replace("</","<\\/")
    title=a.title
    page=(PAGE.replace("__TAGS__",json.dumps(a.tags.split(","),ensure_ascii=False))
              .replace("__DATA__",data).replace("__TITLEJSON__",json.dumps(title,ensure_ascii=False))
              .replace("__TITLE__",html.escape(title)))
    Path(a.out_html).write_text(page,encoding="utf-8")
    print("wrote",a.out_html,"cards",len(cards))
if __name__=="__main__": main()
