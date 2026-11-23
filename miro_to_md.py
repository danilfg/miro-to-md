#!/usr/bin/env python3
"""Read-only Miro API -> inspectable JSON/Markdown + IT Roadmaps v2 ZIP."""
import argparse
import csv
import getpass
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from markdownify import markdownify
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.miro.com/v2"
HINTS = ["Математика", "Python", "Алгосекции", "Git + Terminal",
         "Асинхронность + FastApi", "Classic ML", "Deep Learning",
         "RAG + Agents", "Agents + RAG", "ML System Design", "Deploy"]


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def board_id(value):
    match = re.search(r"/app/(?:board|embed|live-embed)/([^/?#]+)", value)
    value = unquote(match.group(1) if match else value.strip())
    if not re.fullmatch(r"[\w=+.-]+", value):
        raise ValueError("Укажи ссылку на доску Miro или её ID")
    return value


def is_https(value):
    try:
        u = urlsplit(value)
        return u.scheme == "https" and bool(u.hostname) and not (u.username or u.password)
    except (ValueError, TypeError):
        return False


def plain(value):
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def content_fields(item):
    data = item.get("data") or {}
    seen = set()
    for k in ("title", "content", "description", "text", "caption"):
        v = data.get(k, item.get(k))
        if isinstance(v, str) and v.strip() and v not in seen:
            seen.add(v)
            yield k, v


def extract(item):
    chunks, links, seen = [], [], set()

    def add(url, title, origin):
        if not isinstance(url, str) or not url.strip():
            return
        url = url.strip()
        if (url, title) in seen:
            return
        seen.add((url, title))
        links.append({"url": url, "title": title or url, "origin": origin,
                      "validHttps": is_https(url)})

    for key, raw in content_fields(item):
        soup = BeautifulSoup(raw, "html.parser")
        for a in soup.find_all("a", href=True):
            add(a["href"], a.get_text(" ", strip=True), key)
            if not is_https(a["href"]):
                a.replace_with(a.get_text(" ", strip=True) + " (" + a["href"] + ")")
        for url in re.findall(r'https?://[^\s<>"\x27]+', soup.get_text(" ", strip=True)):
            add(url, url, key)
        for bad in soup(["script", "style", "iframe", "object"]):
            bad.decompose()
        chunks.append(markdownify(str(soup), heading_style="ATX", bullets="-").strip())
    data = item.get("data") or {}
    for key in ("linkedTo", "link", "url"):
        if key == "url" and item.get("type") == "image":
            continue
        for target in (item.get(key), data.get(key)):
            add(target, plain(data.get("title") or key), key)
    return "\n\n".join(x for x in chunks if x), links


class Miro:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + token
        retries = Retry(total=5, backoff_factor=1,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods={"GET"}, respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def get(self, path, **params):
        r = self.session.get(API + path, params=params, timeout=(15, 90))
        if not r.ok:
            raise RuntimeError(f"Miro API HTTP {r.status_code} ({path}). "
                               "Проверь токен, boards:read, команду установки и ID доски")
        return r.json()

    def pages(self, path, **params):
        result, seen, cursor = [], set(), None
        while True:
            query = dict(params, limit=50)
            if cursor:
                query["cursor"] = cursor
            data = self.get(path, **query)
            if not isinstance(data.get("data"), list):
                raise RuntimeError("Miro API вернул неожиданный список")
            result.extend(data["data"])
            cursor = data.get("cursor")
            if not cursor:
                return result
            if cursor in seen:
                raise RuntimeError("Miro API повторил cursor")
            seen.add(cursor)

    def snapshot(self, bid, frame_titles=None):
        base = "/boards/" + quote(bid, safe="")
        board = self.get(base)
        selected = []
        if frame_titles:
            normalize = lambda s: " ".join(plain(s).casefold().split())
            available = self.pages(base + "/items", type="frame")
            for title in frame_titles:
                matches = [f for f in available if normalize(f.get("data", {}).get("title")) == normalize(title)]
                if len(matches) != 1:
                    raise ValueError(f"Фрейм {title!r}: найдено {len(matches)}, нужен ровно один")
                selected.append(matches[0])
            found = {str(x["id"]): x for x in selected}
        else:
            found = {str(x["id"]): x for x in self.pages(base + "/items")}
        frames = [x for x in found.values() if x.get("type") == "frame"]
        visited = set()
        while frames:
            frame = frames.pop()
            fid = str(frame["id"])
            if fid in visited:
                continue
            visited.add(fid)
            for x in self.pages(base + "/items", parent_item_id=fid):
                iid = str(x["id"])
                found[iid] = x
                if x.get("type") == "frame":
                    frames.append(x)
        warnings = []
        try:
            connectors = self.pages(base + "/connectors")
            if selected:
                connectors = [c for c in connectors
                              if str((c.get("startItem") or {}).get("id")) in found
                              and str((c.get("endItem") or {}).get("id")) in found]
        except RuntimeError as exc:
            connectors = []
            warnings.append(str(exc))
        return {"board": board, "items": list(found.values()),
                "connectors": connectors, "warnings": warnings,
                "selectedFrameIds": [str(f["id"]) for f in selected]}


def geometry(items):
    index = {str(i["id"]): i for i in items}
    cache = {}

    def xy(iid, path=()):
        if iid in cache:
            return cache[iid]
        if iid not in index or iid in path:
            return 0, 0, 0, 0
        item = index[iid]
        p, g = item.get("position") or {}, item.get("geometry") or {}
        x, y = float(p.get("x") or 0), float(p.get("y") or 0)
        w, h = float(g.get("width") or 0), float(g.get("height") or 0)
        if p.get("origin") == "top_left":
            x += w / 2
            y += h / 2
        parent = str((item.get("parent") or {}).get("id") or item.get("parentId") or "")
        if parent in index and p.get("relativeTo") == "parent_top_left":
            px, py, pw, ph = xy(parent, path + (iid,))
            x += px - pw / 2
            y += py - ph / 2
        cache[iid] = [x, y, w, h]
        return cache[iid]

    return {iid: xy(iid) for iid in index}


def select_frames(snapshot, titles):
    """Scope an existing snapshot without including neighbouring frames."""
    normalize = lambda s: " ".join(plain(s).casefold().split())
    roots = []
    for title in titles:
        matches = [i for i in snapshot["items"] if i["type"] == "frame"
                   and normalize(i.get("data", {}).get("title")) == normalize(title)]
        if len(matches) != 1:
            raise ValueError(f"Фрейм {title!r}: найдено {len(matches)}, нужен ровно один")
        roots.append(str(matches[0]["id"]))
    keep = set(roots)
    while True:
        children = {str(i["id"]) for i in snapshot["items"]
                    if str((i.get("parent") or {}).get("id") or i.get("parentId")) in keep}
        if children <= keep:
            break
        keep.update(children)
    return {**snapshot, "items": [i for i in snapshot["items"] if str(i["id"]) in keep],
            "selectedFrameIds": roots,
            "connectors": [c for c in snapshot.get("connectors", [])
                           if str((c.get("startItem") or {}).get("id")) in keep
                           and str((c.get("endItem") or {}).get("id")) in keep]}


def group(items, coords):
    from study_tools import numbered_sections
    numbered = numbered_sections(items, coords)
    if numbered:
        return numbered
    sections = []
    names = [name.casefold() for name in HINTS]
    for item in items:
        iid = str(item["id"])
        text = " ".join(plain(raw) for _, raw in content_fields(item)).strip()
        if not text:
            continue
        if item.get("type") != "frame" and text.casefold() not in names:
            continue
        x, y, w, h = coords[iid]
        bounds = [x, y, w, h]
        if item.get("type") != "frame":
            rects = []
            for candidate in items:
                cid = str(candidate["id"])
                if cid == iid or candidate.get("type") not in ("frame", "shape"):
                    continue
                a, b, cw, ch = coords[cid]
                if cw > w * 2 and ch > h * 2 and abs(x-a) <= cw/2 and abs(y-(b-ch/2)) <= max(h*3, ch*.2):
                    rects.append((cw*ch, [a,b,cw,ch]))
            if not rects:
                continue
            bounds = min(rects)[1]
        sections.append({"id": iid, "title": text[:200], "bounds": bounds,
                         "order": names.index(text.casefold()) if text.casefold() in names else 999})
    sections.sort(key=lambda s: (s["order"], s["bounds"][1], s["bounds"][0]))
    sections.append({"id": "unassigned", "title": "Нераспределённые материалы", "bounds": [0,0,0,0]})
    ids = {str(x["id"]): x for x in items}
    assignments = {}
    for iid, item in ids.items():
        parent = str((item.get("parent") or {}).get("id") or item.get("parentId") or "")
        if iid in {s["id"] for s in sections}:
            assignments[iid] = iid
        elif parent in {s["id"] for s in sections}:
            assignments[iid] = parent
        else:
            x,y,_,_ = coords[iid]
            candidates = []
            for s in sections[:-1]:
                a,b,w,h = s["bounds"]
                if w>0 and h>0 and abs(x-a)<=w/2 and abs(y-b)<=h/2:
                    candidates.append((w*h, s["id"]))
            assignments[iid] = min(candidates)[1] if candidates else "unassigned"
    return sections, assignments


def overview(out, nodes, sections):
    """Approximate geometry preview. This is NOT an actual screenshot of Miro."""
    rects = [x["bounds"] for x in nodes if x["bounds"][2] > 0]
    if not rects:
        rects = [[0,0,1000,700]]
    left = min(x-w/2 for x,y,w,h in rects)-50
    top = min(y-h/2 for x,y,w,h in rects)-50
    width = max(x+w/2 for x,y,w,h in rects)-left+50
    height = max(y+h/2 for x,y,w,h in rects)-top+50
    pieces = []
    for s in sections[:-1]:
        x,y,w,h = s["bounds"]
        if w>0 and h>0:
            pieces.append(f'<rect x="{x-w/2-left}" y="{y-h/2-top}" width="{w}" height="{h}" fill="#dbeafe" fill-opacity=".2" stroke="#2563eb" stroke-width="3"/>')
    for item in sorted(nodes, key=lambda i:i["bounds"][2]*i["bounds"][3], reverse=True):
        x,y,w,h = item["bounds"]
        w,h = max(110,w),max(35,h)
        label = escape(plain(item["bodyMarkdown"])[:75] or item["type"])
        url = escape(item["sourceUrl"],quote=True)
        pieces.append(f'<a href="{url}" target="_blank"><rect x="{x-w/2-left}" y="{y-h/2-top}" width="{w}" height="{h}" rx="5" fill="white" stroke="#888"/><text x="{x-w/2-left+7}" y="{y-h/2-top+23}" font-size="14">{label}</text></a>')
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Miro API overview</title>'
            '<body style="margin:0;font-family:sans-serif"><header style="padding:15px">'
            '<b>Схема по API координатам — не скриншот Miro</b> (кликните на блок для перехода)'
            '</header><svg xmlns="http://www.w3.org/2000/svg" width="100%" height="90vh" '
            f'viewBox="0 0 {width} {height}">' + "".join(pieces) + '</svg></body></html>')
    (out/"preview.html").write_text(html, encoding="utf-8")


def inspect(snapshot, out):
    save(out/"board.json", snapshot)
    items = snapshot["items"]
    coords = geometry(items)
    sections, assigned = group(items, coords)
    bid = str(snapshot["board"]["id"])
    nodes = []
    for item in items:
        iid = str(item["id"])
        body, links = extract(item)
        source = "https://miro.com/app/board/" + quote(bid,safe="=") + "/?moveToWidget=" + quote(iid,safe="")
        nodes.append({"id":iid, "type":item.get("type","unknown"), "section":assigned[iid],
                      "bodyMarkdown":body, "htmlFields":dict(content_fields(item)),
                      "links":links,"bounds":coords[iid],"sourceUrl":source,
                      "isSupported": item.get("isSupported", True)})
    save(out/"items.json",nodes)
    save(out/"sections.json",{"sections":sections,"assign":assigned})
    with (out/"links.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=["item_id","section","title","url","origin","validHttps"])
        writer.writeheader()
        for item in nodes:
            for link in item["links"]:
                writer.writerow({"item_id":item["id"],"section":item["section"],**link})
    conn=[]
    for c in snapshot.get("connectors",[]):
        a=str((c.get("startItem") or {}).get("id") or "")
        b=str((c.get("endItem") or {}).get("id") or "")
        conn.append({"id":c.get("id"),"from":a,"to":b,"fromSection":assigned.get(a),"toSection":assigned.get(b)})
    save(out/"connectors.json",conn)
    report={"board":snapshot["board"].get("name"),"items":len(nodes),
            "types":dict(Counter(x["type"] for x in nodes)),
            "links":sum(len(x["links"]) for x in nodes),
            "unassigned":sum(x["section"]=="unassigned" for x in nodes),
            "noText":[x["id"] for x in nodes if not x["bodyMarkdown"]],
            "sections":[{"id":s["id"],"title":s["title"],
                         "items":sum(x["section"]==s["id"] for x in nodes)} for s in sections],
            "warnings":snapshot.get("warnings",[]),
            "unsupported": [x["id"] for x in nodes if not x["isSupported"]],
            "moduleMetadata": [s for s in sections if "originalNumber" in s],
            "limitations":["Текст внутри изображений не распознаётся",
                           "Геометрическая структура приблизительная",
                           "Стрелки не преобразуются в обязательные связи автоматически"]}
    save(out/"report.json",report)
    overview(out,nodes,sections)
    return nodes,sections,report


def make_course(snapshot,nodes,sections,out,slug,title):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",slug):
        raise ValueError("Неправильный slug")
    by_section=defaultdict(list)
    assets = []
    structural = {s.get(k) for s in sections if "originalNumber" in s
                  for k in ("titleId", "numberId", "durationId")}
    for i in sorted(nodes,key=lambda v:(v["bounds"][1],v["bounds"][0],v["id"])):
        if (i["id"] in structural or i["type"] == "frame") and not i["links"]:
            continue
        body=i["bodyMarkdown"].strip()
        if not body and i["type"] not in ("image","document","embed","preview","table","paint"):
            continue
        if not body:
            body="Материал доступен на исходной доске Miro."
        if i.get("imageAsset"):
            asset = Path(i["imageAsset"])
            rel = "assets/images/" + asset.name
            assets.append((asset, rel))
            body = "![Изображение из Miro]("+rel+")\n\n" + body
        if i["type"] in ("image","document","embed","preview","table","paint"):
            body += "\n\n[Открыть объект в Miro]("+i["sourceUrl"]+")"
        resources=[]
        for link in i["links"]:
            if is_https(link["url"]) and len(link["url"])<=200 and link["url"] not in [r["url"] for r in resources]:
                host=(urlsplit(link["url"]).hostname or "").lower()
                kind="video" if host in ("youtu.be","youtube.com","www.youtube.com","rutube.ru","vimeo.com") else "article"
                resources.append({"title":link["title"][:200],"url":link["url"],"kind":kind,"order":len(resources)})
        # Keep links stored on the object itself and URLs too long for resources.
        missing = [l for l in i["links"] if l["url"] not in body]
        if missing:
            body += "\n\n" + "\n".join("- <" + l["url"] + ">" for l in missing)
        if i.get("linkChecks"):
            body += "\n\nПроверка ссылок:\n\n" + "\n".join(
                "- <" + c["url"] + "> — " + c["label"] for c in i["linkChecks"])
        first=plain(next(iter(i["htmlFields"].values()),""))
        by_section[i["section"]].append({"sourceId":"miro-"+i["id"],"type":"article",
            "title":(first[:180] or i["type"]),"order":0,"status":"draft",
            "bodyMarkdown":body,"originalUrl":i["sourceUrl"],"resources":resources,
            "metadata":{"miroObjectId":i["id"],"miroType":i["type"]}})
    stages=[]
    for s in sections:
        lessons=by_section[s["id"]]
        if not lessons:
            continue
        for n,l in enumerate(lessons):
            l["order"]=n
        stages.append({"sourceId":"miro-stage-"+s["id"],"number":s.get("originalNumber",len(stages)+1),
            "title":s["title"],"order":len(stages),"status":"draft","isRequired":True,"lessons":lessons})
        if s.get("duration"):
            stages[-1]["duration"] = s["duration"]
        if "originalNumber" in s:
            stages[-1]["detailedDescription"] = (
                f'Номер на доске: {s["originalNumber"]}. Время на доске: {s["duration"]}. '
                f'Miro ID контейнера: {s["id"]}; заголовка: {s["titleId"]}; '
                f'номера: {s["numberId"]}; времени: {s["durationId"]}.')
    if not stages:
        raise ValueError("Нет доступных для экспорта уроков")
    bid=str(snapshot["board"]["id"])
    course={"schemaVersion":"itroadmaps.course.v2","course":{
        "slug":slug,"title":title,"isFree":True,"isIndexable":False,
        "lockStagesUntilCompletion":False,"connections":[],"stages":stages,
        "sources":[{"title":title,"url":"https://miro.com/app/board/"+quote(bid,safe="=")+"/",
                    "kind":"roadmap","isPrimary":True,"order":0}]}}
    pkg=out/"package"
    pkg.mkdir(exist_ok=True)
    for asset, rel in assets:
        dest = pkg/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(asset,dest)
        md_dest = out/rel
        md_dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(asset,md_dest)
    save(pkg/"course.json",course)
    lines=["# "+title,"","Черновая выгрузка из Miro. Номера и время сохранены как на доске; повторные номера не исправлены.",
           "Порядок модулей: номер, затем положение сверху вниз и слева направо. Внутри модуля: сверху вниз, слева направо; развилки смотрите по связям.",
           "Таблицы, рисунки и изображения без текста требуют просмотра в Miro. Полнота визуально не подтверждена: нужны крупные PNG.", ""]
    lines += ["[Все объекты и Miro ID](objects.csv) · [Связи между блоками](structure.md)", "", "Содержание:", ""]
    for section in sections:
        if "originalNumber" in section:
            lines += [f'- {section["originalNumber"]}. {section["title"]} — {section["duration"]}'
                      + (" (номер повторяется на доске)" if section.get("duplicateNumber") else "")]
    lines += [""]
    for stage in stages:
        section = next(s for s in sections if "miro-stage-"+s["id"] == stage["sourceId"])
        label = (str(section["originalNumber"])+". " if "originalNumber" in section else "")+stage["title"]
        lines += ["## "+label,"", "Время: "+stage["duration"] if stage.get("duration") else "", ""]
        if "numberId" in section:
            lines += ["Miro ID: модуль `"+section["id"]+"`, номер `"+section["numberId"]+"`, время `"+section.get("durationId","")+"`.", ""]
        for lesson in stage["lessons"]:
            lines += ["### "+lesson["title"],"", "Miro ID: `"+lesson["metadata"]["miroObjectId"]+"`.", "",lesson["bodyMarkdown"],"",
                      "[Оригинал в Miro]("+lesson["originalUrl"]+")",""]
    markdown="\n".join(lines)
    (out/"course.md").write_text(markdown,encoding="utf-8")
    (pkg/"README.md").write_text(markdown,encoding="utf-8")
    with (out/"objects.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=["id","type","section","sourceUrl"])
        writer.writeheader()
        writer.writerows({k:node[k] for k in writer.fieldnames} for node in nodes)
    structure=["# Связи исходной доски", "", "Сохранены как указания между объектами; обязательность обучения по стрелкам не назначалась.", "",
               "| Miro ID связи | От объекта | К объекту |", "| --- | --- | --- |"]
    for c in snapshot.get("connectors",[]):
        structure.append(f'| {c["id"]} | {(c.get("startItem") or {}).get("id", "")} | {(c.get("endItem") or {}).get("id", "")} |')
    (out/"structure.md").write_text("\n".join(structure)+"\n",encoding="utf-8")
    destination=out/(slug+".zip")
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as z:
        z.write(pkg/"course.json","course.json")
        z.write(pkg/"README.md","README.md")
        z.write(out/"objects.csv","objects.csv")
        z.write(out/"structure.md","structure.md")
        for _, rel in assets:
            z.write(pkg/rel,rel)
    if destination.stat().st_size>64*1024*1024:
        destination.unlink()
        raise ValueError("ZIP превысил лимит IT Roadmaps (64 МБ)")
    return destination,len(stages),sum(len(s["lessons"]) for s in stages)


def main(argv=None):
    load_dotenv()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=["inspect","export"])
    p.add_argument("--board",default=os.getenv("MIRO_BOARD_URL") or os.getenv("MIRO_BOARD_ID"))
    p.add_argument("--from-json",type=Path)
    p.add_argument("--out",type=Path,default=Path("out"))
    p.add_argument("--slug",default="agents-rag")
    p.add_argument("--title")
    p.add_argument("--frame", action="append", help="Точное название фрейма; можно повторять")
    p.add_argument("--link-audit", type=Path, help="JSON с результатами check_links.py")
    p.add_argument("--image-assets", type=Path, help="JSON: Miro image ID -> локальный файл")
    args=p.parse_args(argv)
    if not args.board and not args.from_json:
        p.error("Укажи MIRO_BOARD_URL в .env или --board")
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=True)
    if args.from_json:
        snapshot=json.loads(args.from_json.read_text(encoding="utf-8"))
        if args.frame:
            snapshot=select_frames(snapshot,args.frame)
    else:
        token=os.getenv("MIRO_ACCESS_TOKEN") or getpass.getpass("Miro access token: ")
        if not token:
            p.error("Нет MIRO_ACCESS_TOKEN")
        snapshot=Miro(token).snapshot(board_id(args.board), args.frame)
    nodes,sections,report=inspect(snapshot,out)
    print(f'Аудит: {report["items"]} объектов, {report["links"]} ссылок, {report["unassigned"]} вне разделов')
    print(f"Отчёты: {out}/report.json, links.csv и preview.html")
    if args.mode=="export":
        if len(snapshot.get("selectedFrameIds",[]))>1:
            raise ValueError("Экспортируй каждый курс отдельно: укажи один --frame")
        if args.link_audit:
            checks=json.loads(args.link_audit.read_text(encoding="utf-8"))
            for node in nodes:
                node["linkChecks"]=[checks[l["url"]] for l in node["links"] if l["url"] in checks]
        if args.image_assets:
            images=json.loads(args.image_assets.read_text(encoding="utf-8"))
            for node in nodes:
                if node["id"] in images:
                    node["imageAsset"]=images[node["id"]]
        zip_path,stages,lessons=make_course(snapshot,nodes,sections,out,args.slug,
                          args.title or snapshot["board"].get("name") or "RoadMap")
        print(f'Экспорт: {stages} этапов, {lessons} уроков: {zip_path}')
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, requests.RequestException) as exc:
        msg="Сетевой запрос не выполнен" if isinstance(exc,requests.RequestException) else str(exc)
        print(f"Ошибка: {msg}",file=sys.stderr)
        sys.exit(1)
