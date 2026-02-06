# YouTube DeFi Monitor

## Описание проекта

Мониторинг YouTube-каналов конкурентов в DeFi-нише: поиск вирусных видео, извлечение транскриптов, фактчекинг утверждений, генерация собственных скриптов в авторском стиле.

**Архитектура:**
```
YouTube API → Virality Check → Transcript → Fact Check → Script Generation → Telegram
                                               ↓
                                    DeFiLlama / CoinGecko / Web Search
```

## Ключевые файлы

| Файл | Описание |
|------|----------|
| `src/main.py` | Точка входа, оркестрация пайплайна |
| `src/config.py` | Конфигурация из `config.yaml` |
| `src/monitor/youtube_client.py` | YouTube Data API — поиск новых видео |
| `src/monitor/virality_checker.py` | Адаптивные пороги вирусности (small/medium/large каналы) |
| `src/transcript/extractor.py` | Извлечение транскриптов из видео |
| `src/factcheck/claim_extractor.py` | Извлечение утверждений из транскрипта через LLM |
| `src/factcheck/verifier.py` | Проверка фактов |
| `src/factcheck/sources.py` | DeFiLlama, CoinGecko API |
| `src/generator/analyzer.py` | Анализ трендов и тем |
| `src/generator/script_writer.py` | Генерация скриптов в авторском стиле |
| `src/notify/telegram_bot.py` | Уведомления в Telegram |
| `src/database/models.py` | SQLite модели — хранение обработанных видео |
| `config.yaml` | Основная конфигурация: каналы, пороги, LLM, стиль |
| `prompts/style_examples.md` | Примеры авторского стиля |

## Технологии

- **Language:** Python
- **AI:** Anthropic Claude API (claude-sonnet-4-20250514)
- **Data Sources:** YouTube Data API, DeFiLlama API, CoinGecko API
- **Database:** SQLite
- **Notifications:** Telegram Bot API
- **Deployment:** Railway / Docker

## Команды

```bash
# Локальный запуск
python -m src.main

# Docker
docker-compose up

# Деплой на Railway (автоматически при push)
git push origin main
```

## Переменные окружения

Требуются в `.env`:
- `YOUTUBE_API_KEY`
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Пайплайн обработки

1. **Мониторинг** (cron `0 8 * * *`) — проверка каналов из `config.yaml`
2. **Фильтр вирусности** — адаптивные пороги:
   - Small (<5K subs): views >= subs * 1.5
   - Medium (5K-50K): views >= subs * 1.0
   - Large (>50K): views >= subs * 0.3
3. **Транскрипт** — извлечение текста видео
4. **Фактчекинг** — проверка DeFi-утверждений через API
5. **Анализ** — тренды и темы
6. **Скрипт** — генерация в авторском стиле
7. **Telegram** — отправка результата

## Путь на разных ОС

- **Mac:** `~/Dropbox/Приложения/AI_Agents/youtube-defi-monitor/`
- **Windows:** `D:\Users\Сергей\Dropbox\Приложения\AI_Agents\youtube-defi-monitor\`

## GitHub

- **Repo:** `Sszmentor/youtube-defi-monitor`
- **Branch:** `main`

## Статус

Проект в разработке, готов к деплою на Railway.
