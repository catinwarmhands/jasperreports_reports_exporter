# Reports Exporter

Программа для экспорта шаблонов отчётов с сервера
и проведения манипуляциями над ними в процессе


## Подготовка шаблонов к поставке

Для подготовки шаблонов отчётов к поставке, нужно:

1. Прописать конфигурацию в файле config.json
2. Запустить exporter.exe

Архивы с шаблонами будут сложены в папку output, логи будут записаны в папку log,
с датой и временем запуска в качестве имени файла


## Выбор конфигурационного файла

Если просто запустить exporter.exe, то будет использован стандартный  конфигурационный  файл,
который используется для сборки поставок - config.json

Если при запуске exporter.exe указать один (или несколько)  конфигурационных файлов, то он (они)
будут использованы вместо стандартного

При запуске из консоли это выглядит так:

```bash
exporter.exe config1.json config2.json
```

При запуске через проводник, достаточно перетащить мышкой нужные файлы на exporter.exe


## Конфигурация

Вся конфигурация хранится в файле config.json, в нём указаны:

- host_url - Адрес JRS
- username - Логин админа
- password - Пароль админа
- output_dir - Путь к папке для готовых архивов
- batch_export - Если false, то каждый шаблон будет сохранён в отдельном архиве.
  Если true, то все шаблоны будут лежать в одном архиве,
  при чем все шабоны должны быть экспортированы без ошибок.
  Имя выходных архивов при этом будет "remote_batch_output" и "local_batch_output".
- watermark - Если true, то в конец всех обработанных файлов будет добавляться комментарий
  с информаций об экспорте
- commit - Если true, то выходная папка будет закомитчена в SVN
- file_rename - Переименования имен файлов в архиве (через regexp, переименовывается даже часть пути).
  Если после переименования имя файла стало пустым, то он удаляется
- text_replace - Замена текста в текстовых файлах (через regexp)
- text_replace_file_masks - Список масок текстовых файлов.
  Если имя файла соответствует хотя бы одной из этих масок,
  в нём будет произведена замена текста, по правилам, описанным выше
- output_rename - Переименование выходных архивов
- reports - Список путей к шаблонам на сервере, которые нужно экспортировать.
  Путь к шаблону можно посмотреть через веб-интерфейс JRS,
  если навести мышкой на отчёт, он будет в самом низу.
  Кроме того, можно указать путь к локальному файлу zip или jrxml.
  Поддерживаются абсолютные и относительные пути.
- jobs - Список с параметрами, аналогичными всем перечисленным выше.
  Позволяет переопределять параметры, заданные выше.


Обратите внимание, что в config.json можно комментировать  строки с помощью `//` и `/**/`,
а так же игнорируется лишняя последняя запятая в списках,
что позволяет удобнее выбирать нужные отчёты для экспорта.


## Для разработчиков

Исходник программы - файл src/exporter.py

Для создания venv нужно запустить create_venv.bat, а затем активировать его командой venv/Scripts/activate.bat

Для удаления времнных файлов нужно запустить cleanup.bat

Для сборки достаточно запустить src/build.bat , шаги сборки следующие:

- Активируется venv
- Проводится проверка mypy, если есть ошибки, сборка прекращается
- pyinstaller собирает exe файл и кладет его в нужное место
- Вызывается cleanup.bat для удаления временных файлов
- Деактивируется venv
