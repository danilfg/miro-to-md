#!/usr/bin/env python3
"""Build editorial IT Roadmaps v3 course from Miro source + authored topic files.

This is a portable draft, NOT a live LMS import test. Pure standard library.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"out/agents-rag"
TOPICS=ROOT/"course_v3/topics"
OUT=ROOT/"out/agents-rag-v3"
PKG=OUT/"package"
ARCHIVE=OUT/"agents-rag-v3.zip"
STAGE_SLUGS=[
    "math","python","algorithms","git","async-fastapi","classic-ml",
    "dl-transformers","databases","rag-agents","deploy","mlsd","career","resources"
]
GUIDANCE={
    "30": "За 20 минут нового изучения освоить один шаг: краткую теорию, первую/вторую часть практики или итоговый quiz. Повторить формулу, команду или решение из предыдущего шага за 5 минут. Сохранить контрольную точку; не пропускать Python, ML, RAG или тест.",
    "60": "Использовать 35 минут нового изучения на связанные шаги: теорию и одну часть практики либо оставшуюся часть практики и quiz. На повторение выделить 10 минут: воспроизвести алгоритм или пояснить ошибку без подсказки.",
    "120": "Использовать до 70 минут на полный урок (теория, обе части практики, тест) либо части двух связанных уроков. После завершения записать проверяемый результат: вычисление, SQL, файл кода, таблицу метрик или диаграмму.",
    "180": "Собрать до 100 минут нового изучения из последовательных шагов; в длинных практических проектах закрывать части по контрольным точкам. Сохранять изменения и тестовые данные, не оставлять необъяснённых ошибок.",
    "240": "Использовать до 135 минут на последовательность уроков или проектный инкремент: теория, реализация, негативные тесты, фиксированные метрики. Последние 25 минут закрепления — пересказ архитектуры и разбор выявленных дефектов."
}
BUDGETS={30:(0,5,5,20),60:(5,10,10,35),120:(15,15,20,70),180:(30,20,30,100),240:(40,25,40,135)}
ORDERED_FILES=[
  "01-math.json","02-python.json","03-algorithms.json","04-git.json",
  "05-async-fastapi.json","06-classic-ml.json","07-dl-transformers.json",
  "08-databases.json","09-rag-agents.json","10-deploy.json","11-mlsd.json",
  "12-career.json","13-resources.json"
]

def dump(path,obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def safe_url(value):
    if not isinstance(value,str):
        return False
    try:
        p=urlsplit(value.strip())
        return bool(p.hostname and p.scheme=="https" and not (p.username or p.password)
                    and re.fullmatch(r"[A-Za-z0-9.-]+",p.hostname)
                    and "." in p.hostname and len(value)<=2048)
    except ValueError:
        return False

def rkind(url):
    p=urlsplit(url)
    h=(p.hostname or "").lower()
    suffix=p.path.lower()
    if h in ("youtube.com","www.youtube.com","youtu.be","rutube.ru","vimeo.com"):
        return "video"
    if suffix.endswith((".pdf",".zip",".ipynb",".pptx",".docx")) or h in ("drive.google.com","disk.yandex.ru"):
        return "file"
    if h in ("neetcode.io","sql-academy.org","learngitbranching.js.org","ohmygit.org","www.promptingguide.ai","colab.research.google.com"):
        return "tool"
    return "article"

def clean_title(s):
    s=str(s or "").strip()
    return (s[:180] or "Учебный ресурс").replace("\n"," ")

def effort(typical,planning):
    return {"typicalMinutes":typical,"planningMinutes":planning,
            "basis":"author","confidence":"estimated"}

def step(sid,kind,title,order,plan,typical,**kwargs):
    return {"sourceId":sid,"kind":kind,"title":title,"order":order,"status":"draft",
            "isRequired":True,"effort":effort(typical,plan),**kwargs}

def mk_lesson(stage_id,entry,index):
    if len(entry)!=11:
        raise ValueError(f"Topic {stage_id}, #{index} should have 11 tuple fields; got {len(entry)}")
    key,title,concept,worked,exercise,solution,question,correct,wrong1,wrong2,quiz_explanation=entry
    lid="agr-"+stage_id+"-"+key
    qid="q-"+key
    if len(qid)>80:
        raise ValueError("Quiz ID too long")
    theory=(
        "## Что вы научитесь делать\n\n"
        + "Объяснять «"+title+"», применять изученные понятия на небольшом примере и аргументировать результат.\n\n"
        + "## Объяснение\n\n"+concept+"\n\n"
        + "## Ключевой вывод\n\n"
        + "Сначала сформулируйте рабочую гипотезу и ограничения решения, затем проверьте её на данных или вычислениях. "
          "Если результат зависит от предположений, запишите их явно."
    )
    practical=(
        "## Разобранный пример\n\n"+worked+"\n\n"
        + "## Самостоятельная практика\n\n"+exercise+"\n\n"
        + "## Проверка результата\n\n"+solution+"\n\n"
        + "**Правило зачёта:** самостоятельно повторите решение и сохраните артефакт. "
          "Это упражнение проверяется вручную; автоматической проверки в LMS здесь нет."
    )
    practice_parts=[
      {"sourceId":"example","title":"Повторить разобранный пример","planningMinutes":20,
       "instructions":"Разберите только раздел «Разобранный пример». Воспроизведите вычисления, код или диаграмму без копирования итогового ответа.",
       "checkpoint":"Сохранён собственный расчёт, файл примера или схема с промежуточными шагами."},
      {"sourceId":"exercise","title":"Выполнить упражнение и сверить результат","planningMinutes":20,
       "instructions":"Выполните раздел «Самостоятельная практика», затем откройте «Проверка результата» и объясните расхождения.",
       "checkpoint":"Сохранён результат практики, исправленная ошибка и короткая заметка о том, что получилось."}
    ]
    if key=="capstone" and stage_id=="rag-agents":
        practice_parts=[
            {"sourceId":"ingest","title":"Загрузка и разбиение документации","planningMinutes":20,
             "instructions":"Создайте репозиторий проекта и ingestion script, сохраните входную документацию и стабильные ID чанков.",
             "checkpoint":"Есть входной набор документов, вывод ingest и список chunk IDs."},
            {"sourceId":"index","title":"Эмбеддинги и Qdrant","planningMinutes":20,
             "instructions":"Сконфигурируйте embedding-модель, коллекцию Qdrant и index upsert; получите top-k для контрольных вопросов.",
             "checkpoint":"Есть индекс, схема payload и таблица top-k найденных документов."},
            {"sourceId":"answer","title":"API и ответы с цитатами","planningMinutes":20,
             "instructions":"Соберите retrieval → context → LLM → answer с цитатами и отказом, если источник недостаточен.",
             "checkpoint":"Есть API с воспроизводимым запросом и двумя примерами цитируемых ответов."},
            {"sourceId":"eval","title":"Оценка, отрицательные тесты и отчёт","planningMinutes":20,
             "instructions":"Запустите 30 eval-вопросов, негативные сценарии ACL/injection и замерьте качество retrieval.",
             "checkpoint":"Сохранён eval report, минимум пять ошибок/ограничений и команда воспроизведения."}
        ]
    quiz={
        "title":"Контроль понимания: "+title,
        "passingScore":80,
        "questions":[{
            "id":qid,"prompt":question,"multiple":False,
            "options":[
                {"id":"correct","text":correct,"isCorrect":True},
                {"id":"distractor-1","text":wrong1,"isCorrect":False},
                {"id":"distractor-2","text":wrong2,"isCorrect":False}],
            "explanation":quiz_explanation
        }]
    }
    lesson={
        "sourceId":lid,"title":title,
        "summary":"Разобрать принцип, повторить конкретный пример, выполнить упражнение и пройти проверку: "+title+".",
        "order":index*10,"status":"draft",
        "steps":[
            step(lid+"-theory","article","Объяснение",10,15,12,bodyMarkdown=theory),
            step(lid+"-work","article","Разбор и самостоятельная работа",20,80 if key=="capstone" and stage_id=="rag-agents" else 40,
                 65 if key=="capstone" and stage_id=="rag-agents" else 32,
                 bodyMarkdown=practical,parts=practice_parts),
            step(lid+"-quiz","quiz","Проверка знаний",30,10,8)
        ],
        "quiz":quiz,
        "review":{"enabled":True,"mode":"quiz","questionIds":[qid]},
        "resources":[]
    }
    return lesson

def build():
    source=json.loads((SRC/"package/course.json").read_text(encoding="utf-8"))
    report=json.loads((SRC/"report.json").read_text(encoding="utf-8"))
    if source.get("schemaVersion")!="itroadmaps.course.v2":
        raise ValueError("Expected source v2 for traceability")
    orig=source["course"]["stages"]
    original_by_name={stage["title"]:stage for stage in orig}
    section_to_name={s["id"]:s["title"] for s in report["sections"]}
    source_links=defaultdict(list)
    with (SRC/"links.csv").open(encoding="utf-8-sig",newline="") as handle:
        for row in csv.DictReader(handle):
            source_links[section_to_name.get(row["section"],"UNASSIGNED")].append(row)
    pack_stages=[]
    mapping=[]
    issue_links=[]
    source_total=0
    total_original_resources=0
    for i,(filename,stag_slug) in enumerate(zip(ORDERED_FILES,STAGE_SLUGS)):
        topic=json.loads((TOPICS/filename).read_text(encoding="utf-8"))
        name=topic["sourceStage"]
        if name not in original_by_name:
            raise ValueError("Missing source stage: "+name)
        source_stage=original_by_name[name]
        entries=topic["entries"]
        lessons=[mk_lesson(stag_slug,e,index) for index,e in enumerate(entries,1)]
        all_links=[]
        seen=set()
        for entry in source_links.get(name,[]):
            url=(entry["url"] or "").strip()
            source_total+=1
            if not safe_url(url):
                issue_links.append({"stage":name,"item":entry.get("item_id"),"url":url,"reason":"unsafe/malformed URL"})
                continue
            if url in seen:
                continue
            seen.add(url)
            all_links.append(entry)
        # v2 resources may have been added during enrichment; keep ones absent in raw links.csv
        for orig_lesson in source_stage["lessons"]:
            total_original_resources+=len(orig_lesson.get("resources") or [])
            for item in orig_lesson.get("resources") or []:
                url=item.get("url") or ""
                if not safe_url(url):
                    issue_links.append({"stage":name,"item":orig_lesson["sourceId"],"url":url,"reason":"unsafe/malformed URL"})
                    continue
                if url in seen:
                    continue
                seen.add(url)
                all_links.append({"title":item.get("title") or url,"url":url,
                                  "item_id":orig_lesson["sourceId"]})
        # Even distribution avoids 90 Classic ML resources in one lesson.
        for j,resource in enumerate(all_links):
            dest=lessons[j%len(lessons)]
            dest["resources"].append({"title":clean_title(resource["title"]),
                "kind":rkind(resource["url"]),"order":len(dest["resources"])*10,
                "url":resource["url"]})
        for orig_lesson in source_stage["lessons"]:
            # Audit-only trace, not an invented prerequisite in course schema.
            mapping.append({"originalStage":name,
                            "originalMiroObject":orig_lesson.get("sourceId"),
                            "originalTitle":orig_lesson.get("title"),
                            "originalUrl":orig_lesson.get("originalUrl")})
        pack_stages.append({
            "sourceId":"agr-stage-"+stag_slug,
            "title":name,"order":i*10,"status":"draft","isRequired":True,
            "lessons":lessons
        })
    course={
        "schemaVersion":"itroadmaps.course.v3","packageType":"course",
        "course":{
            "slug":"agents-rag","title":"Agents + RAG: от Python и ML до production-систем",
            "planning":{
                "policyVersion":1,
                "profiles":{str(m):{"dailyMinutes":m,"guidance":GUIDANCE[str(m)]} for m in BUDGETS},
                "reviewIntervalsDays":[1,3,7,14,30]
            },
            "stages":pack_stages,"tasks":[],"connections":[]
        }
    }
    # Local assets from the original Miro export; bytes and hash are never invented.
    asset_records=[]
    image_dir=PKG/"assets/images"
    image_dir.mkdir(parents=True,exist_ok=True)
    algorithms=pack_stages[2]["lessons"]
    for n,p in enumerate(sorted((SRC/"assets/images").glob("*"))):
        if p.suffix.lower() not in (".jpg",".jpeg",".png",".webp",".gif"):
            continue
        if p.stat().st_size>50*1024*1024:
            raise ValueError("Image exceeds v3 limit: "+p.name)
        aid="agr-miro-image-"+p.stem
        target=image_dir/p.name
        shutil.copyfile(p,target)
        data=target.read_bytes()
        asset_records.append({"assetId":aid,"path":"assets/images/"+p.name,
            "mime":{".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
                    ".webp":"image/webp",".gif":"image/gif"}[p.suffix.lower()],
            "bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
        dest=algorithms[min(n,len(algorithms)-1)]["steps"][0]
        dest["bodyMarkdown"]+="\n\n## Схема из оригинальной Miro-доски\n\n" + \
            f"![Изображение №{n+1}, полученное из Miro](asset:{aid})\n\n" + \
            "Это исходное изображение сохранено как дополнительный материал. Его интерпретацию следует сопоставить с подписью на доске."
    manifest={"schemaVersion":"itroadmaps.course.v3","assets":asset_records}
    # Validate all essential requirements specified in supplied prompt.
    validate(course,manifest,PKG)
    dump(PKG/"course.json",course)
    dump(PKG/"manifest.json",manifest)
    dump(OUT/"source_trace.json",{
        "origin":"out/agents-rag/package/course.json","sourceMiroReport":report,
        "originalItems":mapping,"sourceLinksRows":source_total,
        "sourceV2Resources":total_original_resources,
        "discardedLinks":issue_links,
        "note":"Original object prose remains in source v2. These are newly authored teaching lessons."})
    blocks=[]
    for st in pack_stages:
        for ls in st["lessons"]:
            for s in ls["steps"]:
                parts=s.get("parts")
                if parts:
                    for part in parts:
                        blocks.append((f'{st["title"]} → {ls["title"]} → {s["title"]}: {part["title"]}',
                                       part["planningMinutes"]))
                else:
                    blocks.append((f'{st["title"]} → {ls["title"]} → {s["title"]}',
                                   s["effort"]["planningMinutes"]))
    days={}
    for minutes,(_,_,_,new) in BUDGETS.items():
        day_index=0
        capacity=new
        result=[]
        for title,block_minutes in blocks:
            if block_minutes>new:
                raise ValueError(f"Unpackable block {block_minutes} at {minutes} minutes: {title}")
            if block_minutes>capacity:
                day_index+=1
                capacity=new
            if day_index>=5:
                break
            while len(result)<=day_index:
                result.append([])
            result[day_index].append((title,block_minutes))
            capacity-=block_minutes
        days[minutes]=result
    readme=make_readme(course,manifest,report,issue_links,source_links,days,blocks)
    (PKG/"README.md").write_text(readme,encoding="utf-8")
    dump(OUT/"audit.json",{
        "schemaVersion":course["schemaVersion"],
        "packageType":"course",
        "sourceItems":report["items"],"sourceLinks":report["links"],
        "sourceLinksCsvRows":source_total,"sourceV2Resources":total_original_resources,
        "stages":len(pack_stages),
        "lessons":sum(len(s["lessons"]) for s in pack_stages),
        "steps":sum(len(l["steps"]) for s in pack_stages for l in s["lessons"]),
        "quizQuestions":sum(len(l["quiz"]["questions"]) for s in pack_stages for l in s["lessons"]),
        "resourceURLs":sum(len(l["resources"]) for s in pack_stages for l in s["lessons"]),
        "invalidOrUnsafeURLs":len(issue_links),
        "assets":len(asset_records),
        "mandatoryPlanningMinutes":sum(m for _,m in blocks),
        "limitations":[
            "No v3 JSON Schema or LMS v3 importer found on repository main at build time; manual contract checks only.",
            "Original large external courses/videos are supplementary URLs; their full duration is not invented or counted in effort.",
            "No code runners or task definitions provided, hence manual practice in article steps and genuine quizzes.",
            "No fresh internet URL verification; source snapshot may contain older HTTP checks.",
            "New pedagogical lessons synthesize and reorganize Miro material; not a verbatim representation of all individual boards items."
        ]})
    PKG.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ARCHIVE,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for file in PKG.rglob("*"):
            if file.is_file():
                z.write(file,"package/"+file.relative_to(PKG).as_posix())
    if ARCHIVE.stat().st_size>=2_000_000_000:
        raise ValueError("ZIP larger than v3 limit")
    with zipfile.ZipFile(ARCHIVE) as z:
        names=z.namelist()
        assert names.count("package/course.json")==1
        assert json.loads(z.read("package/course.json"))["schemaVersion"]=="itroadmaps.course.v3"
        assert json.loads(z.read("package/manifest.json"))==manifest
    print(f"OK: stages={len(pack_stages)} lessons={sum(len(s['lessons']) for s in pack_stages)} "
          f"steps={sum(len(l['steps']) for s in pack_stages for l in s['lessons'])}, "
          f"minutes={sum(m for _,m in blocks)}, assets={len(asset_records)}, "
          f"zipBytes={ARCHIVE.stat().st_size}; invalidLinks={len(issue_links)}")
    print(f"Package: {ARCHIVE}")

def validate(envelope,manifest,pkg_dir):
    assert envelope["schemaVersion"]=="itroadmaps.course.v3"
    assert envelope["packageType"]=="course"
    c=envelope["course"]
    assert set(c["planning"]["profiles"])=={"30","60","120","180","240"}
    assert c["planning"]["reviewIntervalsDays"]==[1,3,7,14,30]
    seen_stages,seen_lessons,seen_steps=set(),set(),set()
    for st in c["stages"]:
        sid=st["sourceId"]
        assert len(sid)<=120 and sid.isascii() and sid not in seen_stages
        seen_stages.add(sid)
        for lesson in st["lessons"]:
            lid=lesson["sourceId"]
            assert len(lid)<=160 and lid.isascii() and lid not in seen_lessons
            seen_lessons.add(lid)
            assert lesson["steps"] and lesson["quiz"]
            questions=lesson["quiz"]["questions"]
            assert len(questions)==1
            question_ids=[q["id"] for q in questions]
            assert lesson["review"]["questionIds"]==question_ids
            for q in questions:
                assert q["explanation"] and 1<=lesson["quiz"]["passingScore"]<=100
                option_ids=[o["id"] for o in q["options"]]
                assert len(option_ids)==len(set(option_ids))
                assert sum(bool(o["isCorrect"]) for o in q["options"])==1
                assert len(q["id"])<=80 and all(len(v)<=80 for v in option_ids)
            quiz_steps=0
            for s in lesson["steps"]:
                sid=s["sourceId"]
                assert len(sid)<=200 and sid.isascii() and sid not in seen_steps
                seen_steps.add(sid)
                assert s["kind"] in ("article","document","video","practice","quiz")
                e=s["effort"]
                assert 0<e["typicalMinutes"]<=e["planningMinutes"]
                assert e["basis"]=="author" and e["confidence"]=="estimated"
                if s["kind"]=="quiz":
                    quiz_steps+=1
                if "parts" in s:
                    parts=s["parts"]
                    assert sum(p["planningMinutes"] for p in parts)==e["planningMinutes"]
                    assert len({p["sourceId"] for p in parts})==len(parts)
                    assert all(0<p["planningMinutes"]<=20 and len(p["sourceId"])<=80
                               and p["checkpoint"] and p["instructions"] for p in parts)
                elif s["isRequired"]:
                    assert e["planningMinutes"]<=20
            assert quiz_steps==1
            assert all(safe_url(r["url"]) and r["kind"] in
                       ("video","article","book","file","tool","other")
                       for r in lesson["resources"])
    ids=set()
    for a in manifest["assets"]:
        assert a["assetId"] not in ids and len(a["assetId"])<=160
        ids.add(a["assetId"])
        p=(pkg_dir/a["path"]).resolve()
        assert str(p).startswith(str(pkg_dir.resolve())+"/")
        assert p.is_file()
        data=p.read_bytes()
        assert len(data)==a["bytes"]
        assert hashlib.sha256(data).hexdigest()==a["sha256"]
    text=json.dumps(envelope,ensure_ascii=False)
    for aid in re.findall(r"asset:([A-Za-z0-9_.-]+)",text):
        assert aid in ids
    assert len(text.encode("utf-8"))<=32*1024*1024
    assert len(json.dumps(manifest).encode("utf-8"))<=8*1024*1024

def make_readme(envelope,manifest,report,invalid_links,source_links,days,blocks):
    stages=envelope["course"]["stages"]
    lesson_count=sum(len(s["lessons"]) for s in stages)
    total=sum(m for _,m in blocks)
    lines=[
      "# Agents + RAG — авторский курс на основе Miro (v3 draft)","",
      "## Назначение и ограничения","",
      "Цель: освоить Python/ML/LLM/RAG и собрать воспроизводимую RAG-систему по документации Python-библиотеки с Qdrant, API, CI, оценкой retrieval и защитой данных.",
      "Аудитория: разработчики с базовым компьютером и готовностью писать на Python; знания Python/математики можно освоить в начальных модулях.",
      "Язык: русский. Англоязычные источники сохранены как дополнительные материалы.",
      "**Курс содержит авторские объяснения, разобранные примеры, ручную практику и тесты.** Это педагогическая переработка, а не копия внешних курсов/видеолекций.",
      "**Статус: draft.** До реализации v3 импортёр IT Roadmaps может поддерживать только v1/v2. Этот ZIP не объявлен проверенным импортом в LMS.",
      "Ссылка на исходный RoadMap: https://github.com/danilfg/miro-to-md/tree/main/out/agents-rag",
      "",
      "## Содержимое","",
      f"- {len(stages)} модулей, {lesson_count} уроков, {lesson_count*3} шагов, {lesson_count} тестовых вопросов.",
      f"- Обязательная плановая трудоёмкость: {total} минут (~{total/60:.1f} часов), только для авторских шагов.",
      f"- Из Miro: {report['items']} объектов, {report['links']} ссылок. Внешние длинные курсы и видео по ссылкам не включены в эти минуты, поскольку их реальная длительность и доступ не подтверждены.",
      f"- Локальных изображений с реальными hash: {len(manifest['assets'])}.",
      f"- Некорректных/небезопасных URL исключено из LMS: {len(invalid_links)} (проверять в out/agents-rag-v3/source_trace.json).",
      "- Автоматически проверяемого кода нет: Task key и существующие runners не предоставлены. Coding practice оформлена как ручная работа в article steps; quiz проверяется ключом ответов.",
      "",
      "## Карта модулей / sourceId","",
      "| № | Stage.sourceId | Модуль | Уроков |","|---:|---|---|---:|"
    ]
    for i,s in enumerate(stages,1):
        lines.append(f"| {i} | \`{s['sourceId']}\` | {s['title']} | {len(s['lessons'])} |")
    lines+=["","Каждый урок имеет стабильные ASCII ID для lesson, theory/work/quiz, parts и quiz/options. Обновление обязано сохранять эти ID.",
            "Исходные Miro IDs и их заголовки сохранены в source_trace.json рядом с ZIP. Названия из доски сгруппированы в новые учебные уроки; переноса один-к-одному нет.",
            "Содержание пакета не задаёт требуемые межмодульные connections, поскольку в исходнике стрелки не доказывают обязательность предпосылок. Порядок указан в поле order.",
            "",
            "## Пять бюджетов обучения","",
            "| Минут в день | Перерыв | Повторение | Запас | Новое изучение и практика |",
            "|---:|---:|---:|---:|---:|"]
    for minutes,(pause,review,reserve,new) in BUDGETS.items():
        lines.append(f"| {minutes} | {pause} | {review} | {reserve} | {new} |")
    lines+=["","Все пять режимов содержат **одинаковые обязательные уроки и тесты**. Меняется только упаковка шагов/частей в день; план не содержит реальных дат или пользовательских карточек.",
            "Повторения: 1, 3, 7, 14, 30 дней. Это авторская политика; корректируется по результатам ученика.",
            "",
            "### Первые пять учебных дней (последовательная упаковка блоков)",""]
    for minutes in BUDGETS:
        lines.append(f"#### {minutes} минут в день")
        lines.append("")
        for idx,units in enumerate(days[minutes],1):
            used=sum(n for _,n in units)
            lines.append(f"- День {idx}: "+ "; ".join(f"{name} ({duration} мин)" for name,duration in units)+
                         f" — {used}/{BUDGETS[minutes][3]} минут нового изучения")
        lines.append("")
    lines+=["## Контрольные точки","",
            "- Теория — самостоятельное объяснение ключевого принципа; обычно 15 минут на шаг.",
            "- Практика — две смысловые части по 20 минут: воспроизведение примера, самостоятельная задача и сверка результата. Каждая часть имеет instructions и checkpoint; до обеих частей урок не завершён.",
            "- Сквозной RAG-проект — четыре части по 20 минут: ingest → Qdrant → API/citations → evaluation/negative tests.",
            "- Итоговый quiz — 10 минут, не дробится и имеет авторский ключ ответов.",
            "",
            "## Порядок освоения и зависимости","",
            "Начальные Python/математика и алгоритмы поддерживают классический ML; ML и основы DL предшествуют embeddings и RAG; базы данных и API нужны для проекта; deployment и ML System Design идут после базовой системы. Технические required connections в v3 не создавались без подтверждённой схемы зависимостей.",
            "",
            "## Исходные ресурсы и доступность","",
            "Вся собранная подборка исходных HTTPS URL из Miro распределена по полю resources уроков; дубли внутри этапа удалены. Для повреждённых ссылок создан список в source_trace.json.",
            "В исходной выгрузке часть ссылок помечена HTTP 200, а часть требует входа. **В текущей сборке новые HTTP-запросы к ним не выполнялись**; публичный URL не гарантирует открытый контент, возможность воспроизведения, соблюдение лицензии или актуальность.",
            "Stepik, курсы Deep Learning, HuggingFace Agents и другие внешние большие материалы остаются ссылками для углубления. Полная самостоятельная работа по внешним курсам может значительно увеличить реальную продолжительность программы.",
            "Скачаны и включены только изображения, которые **фактически присутствовали** в публичной выгрузке Miro. Неизвестные PDF/MP4 и платные лекции не копировались.",
            "",
            "## Самопроверки","",
            "- Проверены синтаксис JSON, структура envelope v3 и packageType.",
            "- Проверены уникальные ID модулей/уроков/шагов/частей и quiz IDs, один корректный ответ на single-choice, соответствие review.questionIds.",
            "- Проверены усилия, сумма минут частей, существование и SHA-256 ассетов, только HTTPS ресурсы, ровно один course.json в ZIP.",
            "- Проверено, что все обязательные планируемые блоки помещаются в дневной лимит 20 минут для 30-минутного режима.",
            "- **НЕ проверено:** фактический общий JSON Schema/validate_course_package для v3 (его нет в проверенной main-ветке в момент подготовки), импорт в LMS, живой доступ/длительность каждого внешнего источника, запуск кода через runners.",
            "",
            "## Состав архива","",
            "\`\`\`text","package/","  course.json","  manifest.json","  README.md",
            "  assets/","    images/","      [файлы из выгрузки Miro]","\`\`\`",
            "",
            "Текстовый v3 reference на реальное изображение: \`![описание](asset:asset-id)\`. Реестр manifest хранит assetId, относительный path, MIME, bytes и SHA-256.",
            "",
            "## Следующие действия","",
            "1. Реализовать/проверить общий валидатор и импортер itroadmaps.course.v3 в LMS; расхождения с текущей schema исправлять по актуальной schema.",
            "2. Выполнить dry-run импорта в тестовой среде с тем же контекстом курса, но не применять автоматически в production.",
            "3. После проверки источников и педагогической редакции опубликовать только утверждённый пакет.",
            ""]
    return "\n".join(lines)

if __name__=="__main__":
    build()
