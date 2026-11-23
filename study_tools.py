"""Recover numbered modules from badge positions, without OCR or invented titles."""
import re
from collections import Counter

from bs4 import BeautifulSoup


def text(item):
    return BeautifulSoup((item.get("data") or {}).get("content", ""), "html.parser").get_text(" ", strip=True)


def numbered_sections(items, coords):
    badges = [i for i in items if i.get("type") == "sticky_note"
              and (i.get("style") or {}).get("fillColor") == "red"
              and re.fullmatch(r"\d{1,2}", text(i))]
    if not badges:
        return None
    frames = [i for i in items if i.get("type") == "frame"]
    sections, assignments = [], {}
    for frame in frames:
        fid = str(frame["id"])
        children = [i for i in items if str((i.get("parent") or {}).get("id") or i.get("parentId")) == fid]
        modules = []
        for badge in badges:
            if badge not in children:
                continue
            x, y, w, h = coords[str(badge["id"])]
            candidates = []
            for shape in children:
                if shape["type"] != "shape" or text(shape):
                    continue
                a, b, sw, sh = coords[str(shape["id"])]
                if sw > w * 2 and sh > h * 1.5:
                    score = ((a-sw/2-x)/w)**2 + ((b-sh/2-y)/h)**2
                    if score < 1:
                        candidates.append((score, str(shape["id"])))
            if not candidates:
                raise ValueError("Не найден контейнер для номерного стикера; нужна визуальная сверка")
            _, iid = min(candidates)
            a, b, sw, sh = coords[iid]
            top = b-sh/2
            titles = []
            durations = []
            for item in children:
                tx, ty, tw, th = coords[str(item["id"])]
                if item["type"] == "text" and 0 < len(text(item)) < 200 and abs(tx-a) < sw/2 and top-h*2 < ty < top+h*.3:
                    titles.append((abs(tx-a)/sw + abs(ty-top)/(h*4), str(item["id"]), text(item)))
                if item["type"] == "sticky_note" and (item.get("style") or {}).get("fillColor") == "dark_blue":
                    score = ((tx-(a+sw/2))/w)**2 + ((ty-top)/h)**2
                    if score < 1:
                        durations.append((score, str(item["id"]), text(item)))
            if not titles:
                raise ValueError("Не найден заголовок номерного модуля; нужна визуальная сверка")
            _, title_id, title = min(titles)
            section = {"id": iid, "frameId": fid, "title": title, "titleId": title_id,
                       "bounds": coords[iid], "originalNumber": int(text(badge)),
                       "numberId": str(badge["id"]), "duration": "", "durationId": ""}
            if durations:
                _, section["durationId"], section["duration"] = min(durations)
            modules.append(section)
        if len({m["id"] for m in modules}) != len(modules):
            raise ValueError("Два номера попали в один контейнер; нужна визуальная сверка")
        modules.sort(key=lambda m: (m["originalNumber"], m["bounds"][1]-m["bounds"][3]/2, m["bounds"][0]))
        duplicates = {n for n, count in Counter(m["originalNumber"] for m in modules).items() if count > 1}
        for m in modules:
            m["duplicateNumber"] = m["originalNumber"] in duplicates
        remainder = {"id": fid, "title": "Материалы вне нумерованных модулей",
                     "editorialLabel": True, "frameId": fid, "bounds": coords[fid]}
        sections.extend(modules + [remainder])
        pinned = {iid: m["id"] for m in modules for iid in (m["id"], m["titleId"], m["numberId"], m["durationId"]) if iid}
        assignments[fid] = fid
        for item in children:
            iid = str(item["id"])
            if iid in pinned:
                assignments[iid] = pinned[iid]
                continue
            x, y, _, _ = coords[iid]
            containing = []
            for m in modules:
                a, b, w, h = m["bounds"]
                if abs(x-a) <= w/2 and abs(y-b) <= h/2:
                    containing.append((w*h, m["id"]))
            assignments[iid] = min(containing)[1] if containing else fid
    sections.append({"id": "unassigned", "title": "Нераспределённые материалы", "bounds": [0, 0, 0, 0]})
    for item in items:
        assignments.setdefault(str(item["id"]), "unassigned")
    return sections, assignments
