import csv
import json
from zipfile import ZipFile

from miro_to_md import Miro, board_id, extract, geometry, group, inspect, is_https, make_course, select_frames


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


def test_exact_href_punctuation_and_object_link():
    url = "https://example.org/search?q=hello!"
    body, links = extract({"type":"shape", "data":{"content":f'<a href="{url}">Read</a>'},
                           "linkedTo":"https://example.org/other?"})
    assert {l["url"] for l in links} == {url, "https://example.org/other?"}
    assert url in body


def test_scope_is_transitive_and_excludes_neighbours():
    data = fixture()
    data["items"] += [
        {"id":"child", "type":"shape", "parent":{"id":"frame1"}},
        {"id":"grandchild", "type":"text", "parent":{"id":"child"}},
        {"id":"other", "type":"frame", "data":{"title":"Other"}}]
    data["connectors"] = [
        {"id":"keep", "startItem":{"id":"child"}, "endItem":{"id":"grandchild"}},
        {"id":"exclude", "startItem":{"id":"child"}, "endItem":{"id":"other"}}]
    selected = select_frames(data, [" python "])
    assert {i["id"] for i in selected["items"]} == {"frame1", "child", "grandchild"}
    assert [c["id"] for c in selected["connectors"]] == ["keep"]


def test_scoped_api_never_requests_unfiltered_items():
    class FakeMiro(Miro):
        def __init__(self):
            self.calls = []
        def get(self, path, **params):
            return {"id":"board"}
        def pages(self, path, **params):
            self.calls.append((path, params))
            if path.endswith("connectors"):
                return []
            if params == {"type":"frame"}:
                return [{"id":"f", "type":"frame", "data":{"title":"Agents &#43;  RAG"}},
                        {"id":"other", "type":"frame", "data":{"title":"Other"}}]
            assert params == {"parent_item_id":"f"}
            return [{"id":"child", "type":"text", "parent":{"id":"f"}}]
    api = FakeMiro()
    snap = api.snapshot("board", ["Agents + RAG"])
    assert {i["id"] for i in snap["items"]} == {"f", "child"}
    assert all(params for path, params in api.calls if path.endswith("/items"))


def test_missing_or_ambiguous_frame_fails_closed():
    import pytest
    data = fixture()
    with pytest.raises(ValueError):
        select_frames(data, ["missing"])
    data["items"].append({**data["items"][0], "id":"duplicate"})
    with pytest.raises(ValueError):
        select_frames(data, ["Python"])


def numbered_fixture():
    def item(iid, kind, x, y, w, h, content="", color=None):
        return {"id":iid, "type":kind, "parent":{"id":"f"},
                "position":{"x":x,"y":y,"relativeTo":"parent_top_left"},
                "geometry":{"width":w,"height":h}, "data":{"content":content},
                "style":{"fillColor":color}}
    data = {"board":{"id":"sample"}, "items":[
        {"id":"f", "type":"frame", "data":{"title":"Course"},
         "position":{"x":2000,"y":3000}, "geometry":{"width":2000,"height":2000}}],
        "connectors":[]}
    for suffix, x in [("a",100),("b",700)]:
        data["items"] += [item("rect"+suffix,"shape",x+200,500,400,600),
                          item("number"+suffix,"sticky_note",x,200,40,45,"2","red"),
                          item("time"+suffix,"sticky_note",x+400,200,40,45,"~20 часов","dark_blue"),
                          item("title"+suffix,"text",x+200,160,300,0,"Module "+suffix),
                          item("body"+suffix,"text",x+200,400,250,50,"Original content "+suffix)]
    data["items"].append(item("unsupported","table",1500,1500,200,100))
    data["items"][-1]["isSupported"] = False
    return data


def test_number_time_and_parent_coordinates_are_preserved(tmp_path):
    data = numbered_fixture()
    nodes, sections, report = inspect(data,tmp_path)
    modules = [s for s in sections if "originalNumber" in s]
    assert len(modules) == 2
    assert [s["originalNumber"] for s in modules] == [2,2]
    assert all(s["duplicateNumber"] and s["duration"] == "~20 часов" for s in modules)
    assert {n["id"]:n["section"] for n in nodes}["bodya"] == "recta"
    assert modules[0]["bounds"] == [1300,2500,400,600]
    assert report["unsupported"] == ["unsupported"]
    path, _, _ = make_course(data,nodes,sections,tmp_path,"sample","Course")
    with ZipFile(path) as archive:
        package = json.loads(archive.read("course.json"))
        assert "objects.csv" in archive.namelist()
        assert "structure.md" in archive.namelist()
    stages = package["course"]["stages"]
    assert [s["number"] for s in stages[:2]] == [2,2]
    assert all(s["duration"] == "~20 часов" for s in stages[:2])
    lessons = [l for s in stages for l in s["lessons"]]
    assert any(l["sourceId"] == "miro-unsupported" for l in lessons)
    assert not any(l["sourceId"] == "miro-numbera" for l in lessons)


def test_link_checks_and_long_object_urls_survive_zip(tmp_path):
    data = fixture()
    url = "https://example.org/" + "a"*220
    data["items"][1]["linkedTo"] = url
    nodes, sections, _ = inspect(data,tmp_path)
    node = next(n for n in nodes if n["id"] == "text1")
    node["linkChecks"] = [{"url":url,"label":"Нужна авторизация"}]
    path, _, _ = make_course(data,nodes,sections,tmp_path,"test","Test")
    with ZipFile(path) as archive:
        course = json.loads(archive.read("course.json"))["course"]
    lesson = next(l for s in course["stages"] for l in s["lessons"] if l["sourceId"] == "miro-text1")
    assert url in lesson["bodyMarkdown"]
    assert "Нужна авторизация" in lesson["bodyMarkdown"]
    assert lesson["metadata"]["miroObjectId"] == "text1"


def test_link_audit_distinguishes_access_from_http_response():
    from check_links import classify
    assert classify(404,"https://example.org/","")[0] == "unavailable"
    assert classify(403,"https://example.org/","")[0] == "restricted"
    assert classify(200,"https://example.org/login","")[0] == "auth_required"
    assert classify(200,"https://example.org/","<title>Sign in</title>")[0] == "restricted"
    assert classify(200,"https://www.youtube.com/watch?v=test","")[0] == "page_only"


def test_malformed_source_urls_are_marked_without_network():
    from check_links import check
    for url in ["https://user_name/", "https://bad%20host.example/"]:
        result = check(url)
        assert result["url"] == url
        assert result["status"] == "invalid_url"
        assert result["httpStatus"] is None


def test_image_asset_is_present_at_markdown_path_in_zip(tmp_path):
    data = fixture()
    nodes, sections, _ = inspect(data,tmp_path)
    asset = tmp_path/"tiny.png"
    # PNG signature is sufficient here: verify byte preservation and packaging.
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")
    image = next(n for n in nodes if n["id"] == "image1")
    image["imageAsset"] = str(asset)
    path, _, _ = make_course(data,nodes,sections,tmp_path,"image-test","Test")
    with ZipFile(path) as archive:
        assert archive.read("assets/images/tiny.png") == asset.read_bytes()
        assert "assets/images/tiny.png" in archive.read("README.md").decode()
