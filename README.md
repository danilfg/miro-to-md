# miro-to-md

Локальный read-only экспорт Miro RoadMap в Markdown и ZIP-курс для [IT Roadmaps](https://github.com/danilfg/itroadmaps) формата itroadmaps.course.v2.

## Важно о безопасности

**Публичный репозиторий:** никогда не коммитьте .env, OAuth Access Token, Client Secret, cookie-файлы, out/, .local/ или изображения закрытых досок.

Секрет приложения ранее был виден на скриншоте — в Miro откройте App Credentials → Refresh и замените Client Secret. Он для этого проекта не нужен.

## Настройка Miro

1. В приложении Miro to IT Roadmaps включите только boards:read.
2. Нажмите Install app and get OAuth token.
3. Выберите **организацию, где реально находится ваша доска RoadMap** (не обязательно Dev team), затем Add.
4. Сохраните OAuth Access Token только локально.
5. Откройте доску → Share → Copy board link.
6. Draft-статус приложения допустим: публикация в Marketplace, App URL и Redirect URI для этого проекта не нужны.

## Установка на Mac

~~~bash
git clone https://github.com/danilfg/miro-to-md.git
cd miro-to-md
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
~~~

Заполните в локальном .env:

~~~dotenv
MIRO_BOARD_URL=https://miro.com/app/board/YOUR_BOARD_ID=/
MIRO_ACCESS_TOKEN=YOUR_PRIVATE_OAUTH_ACCESS_TOKEN
~~~

Client ID и Client Secret вводить не нужно.

## 1. Проверить структуру доски

~~~bash
python miro_to_md.py inspect \
  --frame 'Agents + RAG' \
  --frame 'Classic ML и DL' \
  --frame 'NLP finetuning'
~~~

Повторяемый `--frame` выбирает точные названия (HTML-сущности, регистр и лишние
пробелы нормализуются). Сначала читается список фреймов, затем содержимое только
выбранных. При отсутствии или неоднозначности названия команда останавливается.
Без `--frame` читается вся доска. Соединители API выдаёт общим списком; в снимок
попадают только связи с обоими концами среди выбранных объектов.

Красный числовой стикер сопоставляется с левым верхним углом контейнера,
синий стикер времени — с правым верхним. Заголовок берётся с доски.
Исходные номера, включая повторы, не перенумеровываются. Модули сортируются
по номеру и координатам, блоки внутри — сверху вниз, затем слева направо.
Неуверенное сопоставление контейнера/заголовка останавливает экспорт.
Ненумерованные материалы сохраняются отдельно под редакционной подписью.

В приватной папке out/ будут: board.json (сырой снимок API), items.json (все извлечённые текстовые объекты), links.csv (встроенные URL), sections.json (группировка), connectors.json (связи), report.json (сводка) и preview.html (приближённый HTML-обзор по координатам).

preview.html **не является настоящим скриншотом Miro**. Текст внутри изображений API не распознаёт. Разделы, позиции и порядок нужно сверить вручную.

## 2. Снять настоящие изображения доски для Codex

~~~bash
pip install -r requirements-visual.txt
python -m playwright install chromium
python visual_capture.py
~~~

Откроется отдельный Chromium. Если Miro запросит логин, авторизуйтесь вручную. Откройте общий вид, настройте масштаб Miro, затем в терминале нажмите Enter, чтобы сохранить out/visual/miro-view-01.png. Передвиньтесь к Python, Classic ML, RAG + Agents и повторите. q — выход.

Профиль браузера хранится в .local/ и не попадает в GitHub. Снимки охватывают только видимую область, а не бесконечный Canvas. Codex должен работать в этой же локальной папке или иметь доступ к безопасно переданным отчётам/PNG.

## 3. Запрос к Codex после добавления .env и снятия PNG

~~~text
Прочитай AGENTS.md и README.md. В локальной рабочей папке запусти python miro_to_md.py inspect. Не раскрывай .env и секреты.
Проанализируй out/report.json, out/items.json, out/links.csv, out/sections.json, out/connectors.json.
Сравни структуру с out/preview.html и реальными PNG из out/visual/. Не выдавай preview.html за реальный вид Miro.
Проверь, все ли ссылки восстановлены, корректно ли распределены разделы, нет ли неполных или дублированных материалов. Если нужно, исправь код и добавь тесты.
Затем запусти python miro_to_md.py export --slug agents-rag --title "Agents + RAG".
Проверь полученный out/agents-rag.zip по контракту COURSE_PACKAGE_FORMAT.md репозитория danilfg/itroadmaps. Не загружай и не публикуй курс без моего согласия.
Напиши отчёт с числами объектов, ссылок, разделов, потерянных данных и расхождений с PNG.
~~~

## 4. Выгрузить курс

~~~bash
python check_links.py
python miro_to_md.py export --from-json out/board.json \
  --frame 'Agents + RAG' --slug agents-rag --title 'Agents + RAG' \
  --link-audit out/link-checks.json --out out/agents-rag
python miro_to_md.py export --from-json out/board.json \
  --frame 'Classic ML и DL' --slug classic-ml-dl --title 'Classic ML и DL' \
  --link-audit out/link-checks.json --out out/classic-ml-dl
python miro_to_md.py export --from-json out/board.json \
  --frame 'NLP finetuning' --slug nlp-finetuning --title 'NLP finetuning' \
  --link-audit out/link-checks.json --out out/nlp-finetuning
~~~

Каждый подкаталог содержит `course.md` и ZIP с `course.json` формата
`itroadmaps.course.v2`, `README.md`, индексом всех объектов `objects.csv`
и связями `structure.md`. Все этапы и уроки создаются в статусе draft.
Курс является **черновиком**, сохраняет ссылки на оригиналы в Miro,
не публикуется и не импортируется автоматически. Экспорт нескольких выбранных
фреймов в один курс запрещён: запускайте экспорт отдельно для каждого.

`check_links.py` проверяет уникальные исходные URL анонимными GET-запросами,
без Miro-токена, браузерных cookie и Referer. Результаты — `link-checks.json`
и CSV; при передаче `--link-audit` отметки добавляются в Markdown и ZIP.
HTTP 200 подтверждает ответ URL, но не доступ ко всему платному материалу;
YouTube отмечается отдельно, воспроизведение видео не проверяется.
Неверные адреса, авторизация, HTTP-ошибки, блокировки и ошибки сети
различаются. Исходные URL не исправляются автоматически.

Для включения уже скачанных изображений можно передать `--image-assets`:
локальный JSON-словарь `Miro image ID -> абсолютный путь файла`. Изображения
копируются рядом с Markdown и внутрь ZIP. Таблицы и рисованные объекты,
которые API возвращает без содержимого, остаются с ID и ссылкой на Miro;
текст в изображениях не распознаётся.

Существующий импортёр IT Roadmaps: [COURSE_PACKAGE_FORMAT.md](https://github.com/danilfg/itroadmaps/blob/main/COURSE_PACKAGE_FORMAT.md).

## Офлайн-режим и тесты

~~~bash
python miro_to_md.py inspect --from-json out/board.json --out out
python -m pytest -q
~~~

Если получаете HTTP 401/403/404, проверьте токен, boards:read, URL доски и то, что приложение установлено именно в организацию с доской. Скрипт не делает OCR, не обходит авторизацию Miro и не выполняет записи через API.
