#!/usr/bin/env bash
# One-shot Fly.io deploy helper for the Tilek Auto bot.
# Usage:
#   cd telegram-ai-uploader
#   ./deploy-fly.sh
#
# What it does:
#   1. Installs flyctl if missing (brew)
#   2. Prompts you to log in / sign up on Fly
#   3. Creates the app (idempotent)
#   4. Pushes all .env values as Fly secrets (so they never end up in git)
#   5. Builds + deploys remotely (no local Docker needed)
#   6. Shows logs

set -e

cd "$(dirname "$0")"

# 1. flyctl
if ! command -v flyctl >/dev/null 2>&1; then
  echo "▶ flyctl не найден — ставлю через brew..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "❌ brew не установлен. Поставь brew: https://brew.sh — или поставь flyctl вручную:"
    echo "   curl -L https://fly.io/install.sh | sh"
    exit 1
  fi
  brew install flyctl
fi

echo "▶ flyctl версия: $(flyctl version | head -1)"

# 2. Auth
if ! flyctl auth whoami >/dev/null 2>&1; then
  echo "▶ Нужно залогиниться. Откроется браузер..."
  flyctl auth login
fi
echo "▶ Залогинен как: $(flyctl auth whoami)"

# 3. Read app name from fly.toml
APP_NAME=$(grep -E '^app\s*=' fly.toml | head -1 | sed -E 's/.*=\s*"([^"]+)".*/\1/')
echo "▶ App: $APP_NAME"

# 4. Create app if it doesn't exist
if ! flyctl apps list --json 2>/dev/null | grep -q "\"Name\":\"$APP_NAME\""; then
  echo "▶ Создаю приложение $APP_NAME (без деплоя)..."
  flyctl apps create "$APP_NAME" || true
fi

# 5. Push .env as secrets
if [ ! -f .env ]; then
  echo "❌ .env не найден в $(pwd). Создай его сначала (cp .env.example .env)."
  exit 1
fi

echo "▶ Загружаю секреты из .env..."
SECRET_ARGS=()
while IFS= read -r line; do
  # skip comments and empty lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "$line" ]] && continue
  # must contain = and non-empty value
  key="${line%%=*}"
  val="${line#*=}"
  [[ -z "$key" || -z "$val" ]] && continue
  SECRET_ARGS+=("$key=$val")
done < .env

if [ ${#SECRET_ARGS[@]} -eq 0 ]; then
  echo "❌ В .env нет ни одной переменной."
  exit 1
fi

flyctl secrets set "${SECRET_ARGS[@]}" --app "$APP_NAME" --stage
echo "▶ Секреты загружены ($((${#SECRET_ARGS[@]})) шт.)"

# 6. Deploy (remote build, no Docker required locally)
echo "▶ Деплой (билд идёт на серверах Fly — может занять 2-3 минуты)..."
flyctl deploy --remote-only --app "$APP_NAME"

echo ""
echo "✅ ГОТОВО! Бот теперь работает в облаке."
echo ""
echo "Полезные команды:"
echo "   flyctl logs --app $APP_NAME            # смотреть логи"
echo "   flyctl status --app $APP_NAME          # статус"
echo "   flyctl restart --app $APP_NAME         # рестарт"
echo "   flyctl scale count 0 --app $APP_NAME   # остановить (счёт идёт)"
echo "   flyctl scale count 1 --app $APP_NAME   # снова запустить"
echo ""
echo "⚠️  Остановите ЛОКАЛЬНОГО бота на ноуте, иначе будет Telegram conflict:"
echo "   pkill -f 'telegram-ai-uploader.*bot.py'"
