#!/usr/bin/env bash
# Установка и обновление бота на сервере. Запуск от root из папки с кодом:
#   sudo bash deploy/install.sh
set -euo pipefail

DIR=/opt/aa-bot
SRC="$(cd "$(dirname "$0")/.." && pwd)"

id aabot >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin aabot
mkdir -p "$DIR"
if [ "$SRC" != "$DIR" ]; then
    # .env и state.db на сервере не трогаем.
    tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=.env --exclude='*.db' -cf - . | tar -C "$DIR" -xf -
fi

python3 -m venv "$DIR/.venv" 2>/dev/null || { apt-get install -y python3-venv && python3 -m venv "$DIR/.venv"; }
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    chown aabot:aabot "$DIR/.env"
    chmod 600 "$DIR/.env"
    echo "Создан $DIR/.env. Заполните его и запустите скрипт ещё раз."
    exit 0
fi

chown -R aabot:aabot "$DIR"
chmod 600 "$DIR/.env"
cp "$DIR/deploy/aa-bot.service" /etc/systemd/system/aa-bot.service
systemctl daemon-reload
systemctl enable aa-bot >/dev/null
systemctl restart aa-bot
sleep 3
systemctl --no-pager --lines=20 status aa-bot
