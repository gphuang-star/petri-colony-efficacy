#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract plate photos from a PPTX and name them from the slide labels.

Each slide is treated as one experiment batch. Pictures are matched to the
nearest text label (treatment) by x-centre, and to a topic label taken from the
slide title or from the row/group label placed to the left of the images.
Layouts handled:
  * centred topic title (label above the image row)
  * left-side row labels (one label per crop/disease group, slides 2/16/18 style)
  * optional per-column overrides when the slide has no column captions
    (e.g. a plate with CK / product A / product B shown only in a data table)

Outputs (in --out-dir):
  slideNN/<NN>_<idx>_<topic>_<treat>.<ext>   extracted plate photos
  mapping.csv                                slide/topic/treat/filename
  plate_manifest.csv                         manifest ready for run_sam_survey_v2.py
"""
from __future__ import annotations
import argparse, csv, re, shutil, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": A, "r": R}
TOL = 250000  # EMU tolerance when matching a caption to an image column


def _off(el):
    o = el.find(".//a:xfrm/a:off", NS)
    e = el.find(".//a:xfrm/a:ext", NS)
    if o is None:
        return (None, None, None, None)
    return (int(o.get("x", 0)), int(o.get("y", 0)),
            int(e.get("cx", 0)) if e is not None else 0,
            int(e.get("cy", 0)) if e is not None else 0)


def _collect(root, rels):
    imgs, texts = [], []
    for pic in root.findall(".//p:pic", NS):
        blip = pic.find(".//a:blip", NS)
        rid = blip.get("{%s}embed" % R) if blip is not None else None
        x, y, cx, cy = _off(pic)
        if x is None:
            continue
        tgt = rels.get(rid, "")
        imgs.append(dict(media=tgt.split("/")[-1] if tgt else "", srckey=rid,
                         x=x, y=y, cx=cx, cy=cy, icx=x + cx / 2, icy=y + cy / 2, bottom=y + cy))
    for sp in root.findall(".//p:sp", NS):
        t = " ".join((z.text or "").strip() for z in sp.iter("{%s}t" % A) if (z.text or "").strip())
        if not t:
            continue
        x, y, cx, cy = _off(sp)
        if x is None:
            continue
        texts.append(dict(text=t, x=x, y=y, cx=cx, cy=cy, tcx=x + cx / 2, tcy=y + cy / 2))
    return imgs, texts


def _assign(num, imgs, texts, overrides):
    if not imgs:
        return imgs
    centers = [im["icx"] for im in imgs]
    offcol = [t for t in texts if min(abs(t["tcx"] - c) for c in centers) > TOL]
    min_top = min(im["y"] for im in imgs)
    headers = sorted([t for t in offcol if t["y"] < min_top], key=lambda t: t["y"])
    rowlab = [t for t in offcol if t["y"] >= min_top]
    left_layout = any(t["tcx"] < min(centers) for t in rowlab)
    cols = sorted(set(round(c / 100000) for c in centers))
    for im in imgs:
        cands = [t for t in texts if abs(t["tcx"] - im["icx"]) <= TOL]
        below = [t for t in cands if t["y"] >= im["bottom"] - 250000]
        if below:
            t = min(below, key=lambda t: t["y"] - im["bottom"])
        elif cands:
            t = min(cands, key=lambda t: abs(t["tcy"] - im["icy"]))
        else:
            t = None
        im["treat"] = t["text"] if t else ""
        cc = ([t for t in rowlab if t["tcx"] <= im["icx"] + 250000] if left_layout
              else [t for t in rowlab if abs(t["tcy"] - im["icy"]) < 1600000])
        if cc:
            im["topic"] = min(cc, key=lambda t: abs(t["tcy"] - im["icy"]) + 0.15 * abs(t["tcx"] - im["icx"]))["text"]
        elif headers:
            im["topic"] = headers[0]["text"]
        else:
            im["topic"] = ""
        if num in overrides:
            names = overrides[num]
            im["treat"] = names[cols.index(round(im["icx"] / 100000)) % len(names)]
    return imgs


def _san(s):
    s = (s or "").strip()
    s = re.sub(r"[\\/:*?\"<>|\n\r]+", "_", s)
    s = re.sub(r"\s+", "", s).replace("·", "")
    return s.strip("_")


def parse_override(text):
    """'18=CK,嗜硫,戊唑醇' -> {18: ['CK','嗜硫','戊唑醇']}"""
    out = {}
    for part in filter(None, (text or "").split(";")):
        k, _, v = part.partition("=")
        out[int(k)] = [x for x in v.split(",") if x]
    return out


def run(pptx, out_dir, plug_mm=6.0, dish_mm=86.0, overrides=None, html_recon=None):
    src = Path(pptx)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    overrides = overrides or {}
    rows, manifest = [], []
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        slides = sorted([n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                        key=lambda x: int(re.search(r"slide(\d+)", x).group(1)))
        for s in slides:
            num = int(re.search(r"slide(\d+)", s).group(1))
            root = ET.fromstring(z.read(s))
            rel_path = f"ppt/slides/_rels/slide{num}.xml.rels"
            rels = {}
            if rel_path in names:
                for r in ET.fromstring(z.read(rel_path)):
                    if r.get("Type", "").endswith("/image"):
                        t = r.get("Target", "")
                        full = ("ppt/" + t.replace("../", "")) if t.startswith("../") else t
                        rels[r.get("Id")] = full.replace("ppt/slides/", "ppt/")
            imgs, texts = _collect(root, rels)
            _assign(num, imgs, texts, overrides)
            d = out / f"slide{num:02d}"
            d.mkdir(parents=True, exist_ok=True)
            ordered = sorted(imgs, key=lambda m: (round(m["icy"] / 200000), m["icx"]))
            for i, im in enumerate(ordered, 1):
                ext = Path(im["media"]).suffix or ".png"
                topic, treat = _san(im["topic"]), _san(im["treat"])
                base = f"{topic}_{treat}" if topic else (treat or "unlabeled")
                fn = f"{num:02d}_{i:02d}_{base}{ext}"
                mpath = rels.get(im["srckey"], "")
                if mpath in names:
                    (d / fn).write_bytes(z.read(mpath))
                rel = f"slide{num:02d}/{fn}"
                rows.append(dict(slide=num, idx=i, media=im["media"], topic=im["topic"],
                                 treat=im["treat"], filename=rel))
                manifest.append(dict(experiment_id=f"S{num:02d}", sample_id=f"S{num:02d}_{i}",
                                     treatment=f"{topic}_{treat}" if topic else treat, replicate=1,
                                     image_path=str((out / rel).resolve()), plug_diameter_mm=plug_mm,
                                     tip_color="", dish_diameter_mm=dish_mm, reference_diameter_mm=dish_mm,
                                     measurement_plane="bottom_inner", camera_angle="top_down",
                                     scale_axis="major", rays=4, method="auto", main_colony_mode="auto",
                                     edge_type="irregular", notes=f"slide={num}"))
    with (out / "mapping.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with (out / "plate_manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys())); w.writeheader(); w.writerows(manifest)
    return rows, manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plug-mm", type=float, default=6.0)
    ap.add_argument("--dish-mm", type=float, default=86.0)
    ap.add_argument("--col-override", default="",
                    help="per-slide treatment names when the slide has no column captions, "
                         "e.g. \"18=CK,嗜硫小红卵菌,竞品戊唑醇\" (separate slides with ';')")
    a = ap.parse_args()
    rows, manifest = run(a.pptx, a.out_dir, a.plug_mm, a.dish_mm, parse_override(a.col_override))
    print(f"extracted {len(rows)} images into {a.out_dir}")
    print(f"manifest: {Path(a.out_dir) / 'plate_manifest.csv'}")


if __name__ == "__main__":
    main()
