#!/usr/bin/env bash
# Installs and starts the COINBABA bot on an Ubuntu server (Oracle Cloud Always Free).
# Run ON THE SERVER from the bot folder:   bash deploy/setup_server.sh
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"

echo "== Installing Python =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip tzdata

echo "== Creating the Python environment =="
cd "$BOT_DIR"
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
chmod 600 .env
mkdir -p data

echo "== Running the offline tests =="
.venv/bin/python -m unittest discover tests

echo "== Installing the always-on service =="
sudo tee /etc/systemd/system/coinbaba-bot.service >/dev/null <<EOF
[Unit]
Description=COINBABA news posting bot
After=network-online.target
Wants=network-online.target

[Service]
User=${USER_NAME}
WorkingDirectory=${BOT_DIR}
ExecStart=${BOT_DIR}/.venv/bin/python run.py
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable coinbaba-bot
sudo systemctl restart coinbaba-bot
sleep 5
sudo systemctl --no-pager status coinbaba-bot | head -n 12
echo "== Done. The bot restarts by itself after crashes and server reboots. =="
