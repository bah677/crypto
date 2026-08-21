#!/usr/bin/env bash
# Подготовка Ubuntu: Wine + скачивание официального установщика MT5 для Linux.
# Полный цикл: https://www.mql5.com/en/articles/625
#
# В Wine после установки MT5: Python for Windows → pip install MetaTrader5 mt5linux
# Запуск моста (в среде Wine, где стоит пакет mt5linux): python -m mt5linux
#
# В .env бота (Linux): MT5_TRANSPORT=linux_bridge MT5LINUX_HOST=127.0.0.1 MT5LINUX_PORT=18812
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALLER="$SCRIPT_DIR/mt5linux-installer.sh"

echo "==> apt: wine64, winetricks, xvfb, wget (нужен sudo)..."
sudo apt-get update -qq
sudo apt-get install -y wget wine64 winetricks xvfb

echo "==> Скачивание mt5linux.sh от MetaQuotes → $INSTALLER"
wget -q "https://download.terminal.free/cdn/web/metaquotes.software.corp/mt5/mt5linux.sh" -O "$INSTALLER" || {
  echo "Ошибка wget. URL из статьи: https://www.mql5.com/en/articles/625"
  exit 1
}
chmod +x "$INSTALLER"

echo "==> Готово. Пример установки MT5 в Wine без монитора:"
echo "    xvfb-run -a \"$INSTALLER\""
if [[ "${RUN_MT5_INSTALLER:-}" == "1" ]]; then
  xvfb-run -a "$INSTALLER" || true
fi
