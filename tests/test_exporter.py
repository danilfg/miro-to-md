import csv
import json
from zipfile import ZipFile

from miro_to_md import board_id, extract, geometry, group, inspect, is_https, make_course


def fixture():
    return {
      "board":{"id":"uXjSample=","name":"RoadMap"},
      "items":[
        {"id":"frame1","type":"frame","data":{"title":"Python"},
         "position":{"x":500,"y":400},"geometry":{"width":600,"height":450}},
        {"id":"text1","type":"text",
         "data":{"content":"<p>Basics</p><a href='https://docs.python.org/3/'>Documentation</a>"},
         "position":{"x":500,"y":350},"geometry":{"width":300,"height":100}},
        {"id":"image1","type":"image","data":{"title":"Схема"},
         "position":{"x":600,"y":450},"geometry":{"width":100,"height":80}},
        {"id":"lonely","type":"sticky_note","data":{"content":"Повторить Python"},
         "position":{"x":1700,"y":1800},"geometry":{"width":150,"height":80}}
      ],"connectors":[],"warnings":[]
    }


def test_url():
    assert board_id("https://miro.com/app/board/uXjSample%3D/?moveToWidget=12")=="uXjSample="
    assert is_https("https://example.org/a")
    assert not is_https("javascript:alert(1)")
    assert not is_https("https://someone:password@example.org/")


def test_extract_preserves_href():
    md,links=extract(fixture()["items"][1])
    assert "[Documentation](https://docs.python.org/3/)" in md
    assert len(links)==1
    assert links[0]["url"]=="https://docs.python.org/3/"


def test_section_auto_assign():
    items=fixture()["items"]
    sections,assign=group(items,geometry(items))
    assert any(s["title"]=="Python" for s in sections)
    assert assign["text1"]=="frame1"
    assert assign["lonely"]=="unassigned"


def test_course_zip(tmp_path):
    data=fixture()
    nodes,sections,report=inspect(data,tmp_path)
    assert report["items"]==4
    assert report["links"]==1
    assert (tmp_path/"preview.html").exists()
    with (tmp_path/"links.csv").open(encoding="utf-8-sig") as f:
        rows=list(csv.DictReader(f))
    assert rows[0]["url"]=="https://docs.python.org/3/"
    path,stages,lessons=make_course(data,nodes,sections,tmp_path,"agents-rag","Agents + RAG")
    assert stages>=1 and lessons>=2
    with ZipFile(path) as z:
        obj=json.loads(z.read("course.json"))
    assert obj["schemaVersion"]=="itroadmaps.course.v2"
    assert all(stage["status"]=="draft" for stage in obj["course"]["stages"])
    assert any("https://docs.python.org/3/" in lesson["bodyMarkdown"]
               for stage in obj["course"]["stages"] for lesson in stage["lessons"])
