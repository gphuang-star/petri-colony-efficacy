# -*- coding: utf-8 -*-
"""面向最终用户的“测量结果”页面（结果页，不是标注页）。

要点：
  · 没有任何训练相关元素（标签/备注/导出CSV/导入CSV）
  · 图片上只有两种线：绿色菌落边界圈 + 黄色十字量取线
  · 人工十字测量走【大图弹窗】，一次只针对一张图，可撤销/重画
  · 卡片之间用唯一 key 区分（同一文件夹里重名文件也不会串图）
"""
from __future__ import annotations
import html, json
from typing import List, Dict

PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 body{margin:0;background:#f3f5f8;color:#1f2430;font-family:'Microsoft YaHei',system-ui,sans-serif}
 header{position:sticky;top:0;z-index:9;background:#fff;border-bottom:1px solid #e3e8ef;padding:10px 16px;
        display:flex;gap:12px;align-items:center;flex-wrap:wrap;box-shadow:0 1px 6px #0000000d}
 header b{font-size:16px;color:#1f4e79}
 .path{font-size:12px;color:#7a8699;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .stat{font-size:13px;color:#41506b;background:#f0f4fa;border-radius:14px;padding:4px 12px}
 button{border:0;border-radius:6px;padding:7px 13px;font-size:13px;cursor:pointer;background:#e8edf5;color:#243447}
 button:hover{filter:brightness(.96)}
 button.primary{background:#2f6fd0;color:#fff}
 button.ok{background:#2e8b57;color:#fff}
 button.ghost{background:#fff;border:1px solid #cfd8e6}
 input[type=text],select{border:1px solid #d3dae5;border-radius:6px;padding:6px 9px;font-size:13px;background:#fff}
 .grid{display:flex;flex-wrap:wrap;gap:14px;padding:16px}
 .card{background:#fff;border-radius:10px;padding:10px;width:300px;box-shadow:0 1px 5px #0000000f;border:1px solid #e6ebf2}
 .card.rev{border-color:#e6a23c;box-shadow:0 0 0 2px #fdf3e3}
 .imgbox{position:relative;width:100%;height:210px;background:#141821;border-radius:6px;overflow:hidden}
 .imgbox img{width:100%;height:100%;object-fit:contain;display:block}
 .nm{font-size:12px;color:#7a8699;margin:8px 0 2px;word-break:break-all}
 .big{font-size:24px;font-weight:700;color:#1f4e79;letter-spacing:.3px}
 .big small{font-size:13px;font-weight:400;color:#7a8699;margin-left:4px}
 .hv{font-size:12.5px;color:#1f4e79;margin-top:3px;font-variant-numeric:tabular-nums}
 .hv i{font-style:normal;color:#7a8699;margin-right:5px}
 .st{font-size:12px;color:#6b7893;margin-top:3px}
 .st.rev{color:#c47d12}.st.fix{color:#2e8b57}
 .note{font-size:11.5px;color:#b07d1a;margin-top:4px;line-height:1.4}
 .act{display:flex;gap:6px;margin-top:9px;flex-wrap:wrap}
 .act button{font-size:12px;padding:5px 9px}
 .empty{padding:40px;color:#8b96a8;text-align:center;width:100%}
 .panel{background:#fff;margin:16px 16px 0;border-radius:10px;box-shadow:0 1px 5px #0000000f;overflow:hidden}
 .panel h3{margin:0;padding:10px 14px;font-size:14px;color:#1f4e79;background:#f7f9fc;border-bottom:1px solid #eaeff6}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{padding:7px 12px;text-align:left;border-bottom:1px solid #eef2f7}
 th{background:#fafcff;color:#41506b;font-weight:600}
 tr:last-child td{border-bottom:0}
 td.num{text-align:right;font-variant-numeric:tabular-nums}
 .eff{font-weight:700;color:#1f7a3d}
 .ck{color:#8b96a8}
 .ghead{padding:12px 18px 2px;font-size:14px;color:#1f4e79;font-weight:700;width:100%}
 .ghead span{font-weight:400;color:#7a8699;font-size:12px;margin-left:8px}

 /* ---------- 人工十字测量弹窗 ---------- */
 .modal{position:fixed;inset:0;background:#0b1017e8;z-index:99;display:none;align-items:center;justify-content:center;padding:14px}
 .modal.on{display:flex}
 .mbox{background:#fff;border-radius:12px;width:min(95vw,1400px);display:flex;flex-direction:column;
       max-height:97vh;overflow:hidden;box-shadow:0 24px 70px #0009}
 .mhead{display:flex;gap:12px;align-items:center;padding:10px 14px;border-bottom:1px solid #e6ebf2;flex-wrap:wrap}
 .mhead b{color:#1f4e79;font-size:15px}
 .mtitle{font-size:12.5px;color:#7a8699;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .mstep{font-size:13px;color:#2f6fd0;font-weight:700}
 .mvals{font-size:14.5px;color:#1f4e79;font-variant-numeric:tabular-nums;margin-left:auto}
 .mvals b{color:#c47d12;font-weight:700}
 .mstage{position:relative;height:74vh;background:#0f141c;flex:0 0 auto}
 .mstage img{width:100%;height:100%;object-fit:contain;display:block;pointer-events:none}
 .mstage svg{position:absolute;left:0;top:0;width:100%;height:100%;display:block;cursor:crosshair}
 .mfoot{padding:8px 14px;font-size:12.5px;color:#5b6a86;background:#f7f9fc;border-top:1px solid #eaeff6}
 .mfoot b{color:#2f6fd0}
</style></head><body>
<header>
  <b>菌落直径测量结果</b>
  <span class="path" id="path" title="__FOLDER__">__FOLDER__</span>
  <span class="stat" id="stats">…</span>
  <input type="text" id="q" placeholder="按文件名筛选…" style="width:160px">
  <select id="flt">
    <option value="all">全部</option>
    <option value="review">仅看需复核</option>
    <option value="fixed">仅看已修正</option>
  </select>
  <button onclick="pickFolder()">换个文件夹</button>
  <button class="ok" onclick="exportExcel()">导出 Excel 表格</button>
</header>
<div class="panel" id="panel" style="display:none"><h3>处理汇总（自动以 CK 为基准计算防效）</h3><div id="sumwrap"></div></div>
<div class="grid" id="grid"></div>

<div class="modal" id="modal">
  <div class="mbox">
    <div class="mhead">
      <b>人工十字测量</b>
      <span class="mtitle" id="mtitle"></span>
      <span class="mstep" id="mstep"></span>
      <span class="mvals" id="mvals"></span>
      <button class="ghost" id="mundo">撤销上一点</button>
      <button class="ghost" id="mclear">重画</button>
      <button class="ghost" id="mcancel">取消 (Esc)</button>
    </div>
    <div class="mstage">
      <img id="mimg" alt="">
      <svg id="msvg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet"></svg>
    </div>
    <div class="mfoot" id="mfoot"></div>
  </div>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
 "use strict";
 var DATA = JSON.parse(document.getElementById('data').textContent);
 var PLATE = __PLATE__, PLUG = __PLUG__;
 var OVER = {};    /* 满皿 / 手填直径： key -> mm */
 var MAN  = {};    /* 人工十字： key -> {m1,m2,mm,b64} */
 var MODAL = null; /* 正在弹窗测量： {key, c, pts:[[x,y],...]} */
 var Q = '', FLT = 'all';

 function find(k){ for(var i=0;i<DATA.length;i++) if(DATA[i].key===k) return DATA[i]; return null; }
 function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
 function attr(s){ return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

 function val(c){
   var m = MAN[c.key]; if(m) return m.mm;
   var o = OVER[c.key]; return (typeof o === 'number') ? o : c.mm;
 }
 function pairOf(c){
   var m = MAN[c.key];
   if(m) return [m.m1, m.m2];
   var o = OVER[c.key];
   if(typeof o === 'number') return [o, o];
   if(c.m1==null && c.m2==null) return [null, null];
   return [c.m1, c.m2];
 }
 function isFixed(c){
   if(MAN[c.key]) return true;
   return typeof OVER[c.key]==='number' && Math.abs(OVER[c.key]-(c.mm==null?0:c.mm))>1e-6;
 }
 function isReview(c){ return !isFixed(c) && !!c.note; }

 function refresh(){
   renderSummary();
   var n=DATA.length, rev=0, fix=0, fail=0;
   DATA.forEach(function(c){
     if(val(c)==null) fail++;
     else if(isFixed(c)) fix++;
     else if(isReview(c)) rev++;
   });
   document.getElementById('stats').textContent =
     '共 '+n+' 张 ｜ 自动 '+ (n-rev-fix-fail) +' ｜ 需复核 '+rev+' ｜ 已修正 '+fix + (fail? (' ｜ 失败 '+fail):'');
 }

 /* ---------------- 卡片 ---------------- */
 function cardNode(c){
   var mm = val(c), pr = pairOf(c), fixed = isFixed(c);
   var rev = (mm==null) || isReview(c);
   var st = (mm==null) ? '识别失败，请人工十字测量' : (fixed ? '已人工修正' : (c.note ? '需复核' : '自动'));
   var d = document.createElement('div');
   d.className = 'card' + (rev ? ' rev' : '');

   var box = document.createElement('div'); box.className = 'imgbox';
   var img = document.createElement('img');
   img.src = 'data:image/jpeg;base64,' + ((MAN[c.key] && MAN[c.key].b64) ? MAN[c.key].b64 : c.b64);
   box.appendChild(img);
   d.appendChild(box);

   var hv = '';
   if(pr[0]!=null && pr[1]!=null){
     hv = '<div class="hv"><i>横\\竖</i>' + pr[0].toFixed(2) + ' \\ ' + pr[1].toFixed(2) + ' mm</div>';
   }
   var k = attr(c.key);
   var acts = '<div class="act">'
     + '<button class="primary" data-act="man" data-k="'+k+'">✚ 人工十字测量</button>'
     + '<button data-act="plate" data-k="'+k+'">按满皿('+PLATE+'mm)</button>'
     + '<button data-act="ask" data-k="'+k+'">输入直径…</button>'
     + (fixed ? '<button data-act="reset" data-k="'+k+'">恢复自动</button>' : '')
     + '</div>';

   var meta = document.createElement('div');
   meta.innerHTML =
     '<div class="nm">' + esc(c.name) + '</div>' +
     '<div class="big">' + (mm==null ? '—' : mm.toFixed(2)) + '<small>mm</small></div>' +
     hv +
     '<div class="st' + (fixed?' fix':(rev?' rev':'')) + '">' + st + '</div>' +
     (c.note && !MAN[c.key] ? '<div class="note">' + esc(c.note) + '</div>' : '') +
     acts;
   d.appendChild(meta);
   return d;
 }

 function render(){
   var g = document.getElementById('grid'); g.innerHTML = '';
   var shown = 0, lastStrain = null;
   var list = DATA.slice().sort(function(a,b){
     var pa=parseName(a.name), pb=parseName(b.name);
     var sa=pa?pa.strain:'~', sb=pb?pb.strain:'~';
     if(sa!==sb) return sa.localeCompare(sb);
     var ta=pa?pa.treat:a.name, tb=pb?pb.treat:b.name;
     if(ta!==tb) return ta.localeCompare(tb);
     return a.name.localeCompare(b.name, undefined, {numeric:true});
   });
   list.forEach(function(c){
     if(Q && c.name.toLowerCase().indexOf(Q)<0) return;
     if(FLT==='review' && !isReview(c)) return;
     if(FLT==='fixed'  && !isFixed(c))  return;
     shown++;
     var pr = parseName(c.name);
     if(pr && pr.strain !== lastStrain){
       var gh = document.createElement('div'); gh.className='ghead';
       gh.innerHTML = esc(pr.strain) + '<span>' + esc(c.folder) + '</span>';
       g.appendChild(gh); lastStrain = pr.strain;
     }
     g.appendChild(cardNode(c));
   });
   if(!shown){
     var e = document.createElement('div'); e.className='empty'; e.textContent='没有符合条件的图片';
     g.appendChild(e);
   }
   refresh();
 }

 /* ---------------- 人工十字测量（大图弹窗） ---------------- */
 var mstage, msvg, mimg;
 function lineTag(a,b,w){
   return '<line x1="'+a[0].toFixed(1)+'" y1="'+a[1].toFixed(1)+'" x2="'+b[0].toFixed(1)+'" y2="'+b[1].toFixed(1)+
          '" stroke="#ffff00" stroke-width="'+w+'" stroke-linecap="round"/>';
 }
 function mpt(){ return (MODAL && MODAL.c && MODAL.c.mpt) ? MODAL.c.mpt : 0; }
 function distMM(a,b){ var dx=b[0]-a[0], dy=b[1]-a[1]; return Math.sqrt(dx*dx+dy*dy)*mpt(); }

 function drawModal(){
   if(!MODAL) return;
   var c = MODAL.c, p = MODAL.pts, w = c.ywt || 5, s = '';
   if(p.length>=2) s += lineTag(p[0], p[1], w);
   if(p.length>=4) s += lineTag(p[2], p[3], w);
   for(var i=0;i<p.length;i++){
     s += '<circle cx="'+p[i][0].toFixed(1)+'" cy="'+p[i][1].toFixed(1)+'" r="'+(w*1.4).toFixed(1)+
          '" fill="#ffff00" stroke="#1f2430" stroke-width="'+(w*0.3).toFixed(1)+'"/>';
   }
   msvg.innerHTML = s;
   var st = document.getElementById('mstep'), vs = document.getElementById('mvals');
   if(p.length<2) st.textContent = '第 1 步：在图上门点「横线」的两个端点';
   else if(p.length<4) st.textContent = '第 2 步：再点「竖线」的两个端点';
   else st.textContent = '正在计算…';
   var t = '';
   if(p.length>=2) t += '横 <b>' + distMM(p[0],p[1]).toFixed(2) + '</b> mm';
   if(p.length>=4){
     var a = distMM(p[0],p[1]), b = distMM(p[2],p[3]);
     t += '　竖 <b>' + b.toFixed(2) + '</b> mm　平均 <b>' + ((a+b)/2).toFixed(2) + '</b> mm';
   }
   vs.innerHTML = t;
 }

 function openManual(k){
   var c = find(k); if(!c) return;
   MODAL = {key:k, c:c, pts:[]};
   mimg.src = 'data:image/jpeg;base64,' + c.b64;
   msvg.setAttribute('viewBox', '0 0 ' + c.w + ' ' + c.h);
   document.getElementById('mtitle').textContent = c.name;
   document.getElementById('mfoot').innerHTML =
     '用鼠标在图上点 <b>4 个点</b>：先点菌落上「横线」的两端，再点「竖线」的两端。'
     + '点错了可以【撤销上一点】或【重画】。算完后图片上的黄十字会更新成你量的这两条线。';
   document.getElementById('modal').classList.add('on');
   drawModal();
 }
 function closeManual(){
   MODAL = null;
   document.getElementById('modal').classList.remove('on');
 }
 function modalClick(ev){
   if(!MODAL) return;
   var ctm = msvg.getScreenCTM(); if(!ctm) return;
   var pt = msvg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
   var q = pt.matrixTransform(ctm.inverse());
   MODAL.pts.push([q.x, q.y]);
   if(MODAL.pts.length < 4){ drawModal(); return; }
   var pts = MODAL.pts.slice(), key = MODAL.key, c = MODAL.c;
   fetch('/manual', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({key:key, points:pts})})
     .then(function(r){ return r.json(); })
     .then(function(j){
        if(!j.ok){ alert('人工测量失败：' + (j.error || '未知错误')); return; }
        MAN[key] = {m1:j.m1, m2:j.m2, mm:j.mm, b64:j.b64};
        delete OVER[key];
        closeManual(); render();
     })
     .catch(function(err){ alert('人工测量失败：' + err); });
 }

 window.startManual = function(k){ openManual(k); };
 window.setPlate = function(k){ delete MAN[k]; OVER[k] = PLATE; render(); };
 window.askMM = function(k){
   var c = find(k); if(!c) return;
   var cur = (val(c)==null ? '' : val(c).toFixed(2));
   var v = prompt('请输入该菌落的直径（mm）：', cur);
   if(v===null) return;
   var x = parseFloat(v);
   if(isNaN(x) || x<=0){ alert('请输入大于 0 的数字'); return; }
   delete MAN[k]; OVER[k] = x; render();
 };
 window.resetMM = function(k){ delete OVER[k]; delete MAN[k]; render(); };

 document.getElementById('grid').addEventListener('click', function(e){
   var t = e.target;
   while(t && t !== this && !(t.tagName === 'BUTTON' && t.getAttribute('data-act'))) t = t.parentNode;
   if(!t || t === this) return;
   var k = t.getAttribute('data-k'), act = t.getAttribute('data-act');
   if(act==='man') window.startManual(k);
   else if(act==='plate') window.setPlate(k);
   else if(act==='ask') window.askMM(k);
   else if(act==='reset') window.resetMM(k);
 });

 function parseName(name){
   var base = String(name).replace(/\.[^.]+$/, '');
   var parts = base.split(/[_\-\s]+/).filter(Boolean);
   if (parts.length < 2) return null;
   var strain = parts[0];
   var end = /^r?\d+$/i.test(parts[parts.length-1]) ? parts.length-1 : parts.length;
   if (end < 2) return null;
   return { strain: strain, treat: parts.slice(0, end).join('_') };
 }
 function isCK(t){ return /(^|_)ck($|_)/i.test(t) || String(t).trim().toUpperCase()==='CK'; }
 function groups(){
   var g = {};
   DATA.forEach(function(c){
     var p = parseName(c.name); if(!p) return;
     var mm = val(c); if(mm==null) return;
     (g[p.strain] = g[p.strain] || {})[p.treat] = ((g[p.strain]||{})[p.treat] || []).concat([mm]);
   });
   return g;
 }
 function stats(){
   var g = groups(), rows = [];
   Object.keys(g).sort().forEach(function(strain){
     var treats = g[strain], ck = null;
     Object.keys(treats).forEach(function(t){
       if(isCK(t)){ var a=treats[t]; ck = a.reduce(function(x,y){return x+y;},0)/a.length; }
     });
     Object.keys(treats).sort(function(a,b){
       if(isCK(a))return -1; if(isCK(b))return 1; return a.localeCompare(b);
     }).forEach(function(t){
       var a = treats[t], m = a.reduce(function(x,y){return x+y;},0)/a.length;
       rows.push({strain:strain, treat:t, n:a.length, mean:m, ck:ck, isCK:isCK(t)});
     });
   });
   rows.forEach(function(r){
     if(r.isCK || r.ck==null || r.ck<=PLUG) { r.eff = null; return; }
     r.eff = (r.ck - r.mean) / (r.ck - PLUG) * 100;
   });
   return rows;
 }
 function renderSummary(){
   var rows = stats(), panel = document.getElementById('panel');
   if(!rows.length){ panel.style.display='none'; return; }
   panel.style.display='block';
   var h = '<table><tr><th>菌株</th><th>处理</th><th>重复数</th><th>平均直径(mm)</th><th>CK平均(mm)</th><th>防效(%)</th></tr>';
   rows.forEach(function(r){
     h += '<tr><td>'+esc(r.strain)+'</td><td'+(r.isCK?' class="ck"':'')+'>'+esc(r.treat)+'</td>'
        + '<td class="num">'+r.n+'</td><td class="num">'+r.mean.toFixed(2)+'</td>'
        + '<td class="num">'+(r.ck==null?'—':r.ck.toFixed(2))+'</td>'
        + '<td class="num">'+(r.eff==null?(r.isCK?'-':'—'):'<span class="eff">'+r.eff.toFixed(2)+'</span>')+'</td></tr>';
   });
   h += '</table>';
   document.getElementById('sumwrap').innerHTML = h;
 }

 document.getElementById('q').oninput = function(){ Q = this.value.trim().toLowerCase(); render(); };
 document.getElementById('flt').onchange = function(){ FLT = this.value; render(); };
 window.pickFolder = function(){
   fetch('/pick',{method:'POST'}).then(function(r){return r.json();}).then(function(j){
     if(j && j.ok) location.reload(); else if(j && j.error) alert(j.error);
   });
 };
 window.exportExcel = function(){
   var rows = DATA.map(function(c){
     var pr = pairOf(c), mm = val(c);
     var status = (mm==null) ? '失败'
                : (MAN[c.key] ? '人工十字测量'
                : (isFixed(c) ? '人工修正' : (c.note ? '需复核' : '自动')));
     return {key:c.key, stem:c.stem, file:c.name, folder:c.folder,
             mm:mm, auto_mm:c.mm, m1:pr[0], m2:pr[1],
             status:status, note:(MAN[c.key] ? '' : (c.note||''))};
   });
   fetch('/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rows)})
    .then(function(r){return r.json();})
    .then(function(j){ alert(j.ok ? ('已导出 Excel：\n'+j.path) : ('导出失败：'+j.error)); })
    .catch(function(e){ alert('导出失败：'+e); });
 };

 mstage = document.querySelector('.mstage');
 msvg = document.getElementById('msvg');
 mimg = document.getElementById('mimg');
 msvg.addEventListener('click', modalClick);
 document.getElementById('mundo').onclick = function(){ if(MODAL && MODAL.pts.length){ MODAL.pts.pop(); drawModal(); } };
 document.getElementById('mclear').onclick = function(){ if(MODAL){ MODAL.pts = []; drawModal(); } };
 document.getElementById('mcancel').onclick = closeManual;
 document.getElementById('modal').addEventListener('click', function(e){ if(e.target === this) closeManual(); });
 document.addEventListener('keydown', function(e){ if(e.key === 'Escape') closeManual(); });

 render();
})();
</script></body></html>"""


def build_page(cards: List[Dict], folder: str, plate_mm: float = 86.0, title: str = "菌落直径测量结果",
               plug_mm: float = 6.0) -> str:
    data = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
    return (PAGE.replace("__DATA__", data)
                .replace("__PLATE__", f"{plate_mm:g}")
                .replace("__PLUG__", f"{plug_mm:g}")
                .replace("__FOLDER__", html.escape(str(folder)))
                .replace("__TITLE__", html.escape(title)))