— НАЧАЛО README.md —

UEFN Telegram Bot (fortnite.gg parser)

Телеграм‑бот, который вытягивает данные с fortnite.gg и помогает работать с картами Fortnite Creative: топ, карточки карт/креаторов, подписки и уведомления по онлайну. Бот работает через long polling (без вебхуков), так что его можно запускать на любом сервере.

Возможности

Топ самых популярных карт (пагинация, переключатель Hide Epic).
Карточка карты: превью, онлайн, пики, теги, быстрые кнопки подписки.
Карточка креатора: аватар, суммарный онлайн, список карт.
Подписки на онлайн карт/креаторов: быстрые пороги 50/100/500/1000 + кастомный порог.
Раздел «Подписки»: переход к карточкам подписанных карт/креаторов одной кнопкой.
Главное меню с баннером и текущим количеством игроков в Fortnite.
«Один пост»: бот удаляет свой предыдущий пост при показе нового, чтобы не засорять чат.
Требования

Python 3.10+
Зависимости из requirements.txt
Быстрый старт (локально)

В корне проекта:
python -m venv .venv
..venv\Scripts\Activate.ps1
pip install -r requirements.txt
.env (в корне):
TELEGRAM_TOKEN=xxxxxxxx:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BOT_BANNER_URL=https://example.com/banner.jpg
(опц.) BOT_BANNER_FILE=banner.jpg
Запуск:
python bot.py
Переменные окружения

TELEGRAM_TOKEN — токен Telegram‑бота (обязательно).
BOT_BANNER_URL — URL изображения для главной.
BOT_BANNER_FILE — путь к локальному файлу (если задан и существует, будет использован вместо URL).
Деплой на Render (Background Worker)

Подключите репозиторий к Render.
Создайте Background Worker:
Build Command: pip install -r requirements.txt
Start Command: python bot.py
В Settings → Environment задайте переменные TELEGRAM_TOKEN, BOT_BANNER_URL (и/или BOT_BANNER_FILE).
Deploy. В логах увидите Bot is running...
Примечание: На Render .env из репозитория не читается — используйте переменные окружения сервиса.

Деплой на VPS (Ubuntu + systemd)

Установите зависимости, создайте venv, положите .env с TELEGRAM_TOKEN и настройками баннера.

Создайте сервис unit uefn-bot.service:
[Unit]
Description=UEFN Telegram Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/uefn_bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/uefn_bot/.venv/bin/python /opt/uefn_bot/bot.py
Restart=always

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload && sudo systemctl enable --now uefn-bot

journalctl -u uefn-bot -f

Docker (опционально)
Dockerfile:

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
Запуск:

docker build -t uefn-bot .
docker run -d --restart=always -e TELEGRAM_TOKEN=xxxx -e BOT_BANNER_URL=https://... uefn-bot
Git: добавить/обновить проект

Игнорируйте локальные файлы и секреты:
.env
.venv/
bot_state.json
bot_subs.json
Первый коммит и публикация:
git init
git add -A
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/<user>/UEFN_BOT.git
git pull --rebase origin main (если remote уже не пустой)
git push -u origin main
Частые вопросы

“Нет баннера”: проверьте BOT_BANNER_URL (или BOT_BANNER_FILE). Бот берёт баннер в рантайме.
“TELEGRAM_TOKEN is not set”: задайте переменную окружения на сервере (Render/VPS) и перезапустите процесс.
“Кракозябры”: в боте все строки UI заданы через Unicode‑эскейпы, проблемы с кодировкой оболочки исключены.
Лицензия

Код без явной лицензии. Не публикуйте секреты (.env) в публичных репозиториях.