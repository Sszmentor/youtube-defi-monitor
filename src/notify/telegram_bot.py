"""Telegram bot for notifications and script management."""

import structlog
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from ..config import get_config
from ..database.models import Database, Script, ScriptStatus, Video


logger = structlog.get_logger()


class TelegramNotifier:
    """Telegram bot for sending notifications and managing scripts."""

    def __init__(self, db: Optional[Database] = None):
        """Initialize Telegram bot.

        Args:
            db: Database instance (optional, will be created if not provided).
        """
        config = get_config()
        self.bot_token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id
        self.db = db

        if not self.bot_token:
            raise ValueError("Telegram bot token is required")

        self.application: Optional[Application] = None

    async def initialize(self) -> None:
        """Initialize the bot application."""
        self.application = Application.builder().token(self.bot_token).build()

        # Register handlers
        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("status", self._cmd_status))
        self.application.add_handler(CommandHandler("channels", self._cmd_channels))
        self.application.add_handler(CommandHandler("pending", self._cmd_pending))
        self.application.add_handler(CommandHandler("help", self._cmd_help))

        # Callback handlers for inline buttons
        self.application.add_handler(
            CallbackQueryHandler(self._callback_approve, pattern="^approve_")
        )
        self.application.add_handler(
            CallbackQueryHandler(self._callback_reject, pattern="^reject_")
        )
        self.application.add_handler(
            CallbackQueryHandler(self._callback_regenerate, pattern="^regen_")
        )

        await self.application.initialize()
        logger.info("telegram_bot_initialized")

    async def shutdown(self) -> None:
        """Shutdown the bot."""
        if self.application:
            await self.application.shutdown()

    # === Notification Methods ===

    async def send_message(self, text: str, parse_mode: str = "HTML") -> None:
        """Send a simple message to the configured chat.

        Args:
            text: Message text.
            parse_mode: Parse mode (HTML or Markdown).
        """
        if not self.application:
            await self.initialize()

        try:
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))

    async def notify_viral_videos(self, videos: list[Video]) -> None:
        """Send notification about new viral videos found.

        Args:
            videos: List of viral videos.
        """
        if not videos:
            return

        text = f"🔥 <b>Найдено {len(videos)} вирусных видео!</b>\n\n"

        for i, video in enumerate(videos[:10], 1):  # Limit to 10
            text += (
                f"{i}. <b>{video.title[:50]}...</b>\n"
                f"   👁 {video.views:,} просмотров | "
                f"📈 {video.virality_score:.1f}x\n"
                f"   🔗 https://youtube.com/watch?v={video.id}\n\n"
            )

        if len(videos) > 10:
            text += f"... и ещё {len(videos) - 10} видео"

        await self.send_message(text)

    async def notify_new_script(self, script: Script, video: Video) -> None:
        """Send notification about a new generated script with approval buttons.

        Args:
            script: Generated script.
            video: Source video.
        """
        text = (
            f"📝 <b>Новый сценарий готов!</b>\n\n"
            f"<b>Тема:</b> {script.topic}\n"
            f"<b>Источник:</b> {video.title[:50]}...\n"
            f"🔗 https://youtube.com/watch?v={video.id}\n\n"
            f"<b>Превью сценария:</b>\n"
            f"<i>{script.script_text[:500]}...</i>\n\n"
            f"ID сценария: #{script.id}"
        )

        # Create inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{script.id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{script.id}"),
            ],
            [
                InlineKeyboardButton("🔄 Переделать", callback_data=f"regen_{script.id}"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if not self.application:
            await self.initialize()

        try:
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.error("telegram_script_notify_error", error=str(e))

    async def send_full_script(self, script: Script) -> None:
        """Send the full script text.

        Args:
            script: Script to send.
        """
        # Split into chunks if too long (Telegram limit is 4096 chars)
        text = script.script_text
        chunk_size = 4000

        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            if i == 0:
                chunk = f"📄 <b>Полный сценарий #{script.id}</b>\n\n" + chunk
            await self.send_message(chunk)

    async def notify_daily_summary(
        self,
        channels_checked: int,
        viral_found: int,
        scripts_generated: int,
    ) -> None:
        """Send daily summary notification.

        Args:
            channels_checked: Number of channels checked.
            viral_found: Number of viral videos found.
            scripts_generated: Number of scripts generated.
        """
        text = (
            f"📊 <b>Ежедневный отчёт</b>\n\n"
            f"📺 Проверено каналов: {channels_checked}\n"
            f"🔥 Найдено вирусных видео: {viral_found}\n"
            f"📝 Сгенерировано сценариев: {scripts_generated}\n"
        )

        await self.send_message(text)

    # === Command Handlers ===

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "👋 Привет! Я бот для мониторинга YouTube каналов.\n\n"
            "Команды:\n"
            "/status - Текущий статус\n"
            "/channels - Список каналов\n"
            "/pending - Неодобренные сценарии\n"
            "/help - Помощь"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not self.db:
            await update.message.reply_text("❌ База данных не подключена")
            return

        channels = await self.db.get_all_channels()
        pending = await self.db.get_pending_scripts()
        viral = await self.db.get_viral_videos(limit=5)

        text = (
            f"📊 <b>Статус системы</b>\n\n"
            f"📺 Каналов: {len(channels)}\n"
            f"📝 Ожидают одобрения: {len(pending)}\n"
            f"🔥 Топ вирусных видео: {len(viral)}\n"
        )

        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /channels command."""
        if not self.db:
            await update.message.reply_text("❌ База данных не подключена")
            return

        channels = await self.db.get_all_channels()

        if not channels:
            await update.message.reply_text("Нет отслеживаемых каналов")
            return

        text = "📺 <b>Отслеживаемые каналы:</b>\n\n"
        for ch in channels:
            last = ch.last_checked.strftime("%d.%m %H:%M") if ch.last_checked else "никогда"
            text += f"• <b>{ch.name}</b>\n  {ch.subscribers:,} подписчиков | Проверен: {last}\n\n"

        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /pending command."""
        if not self.db:
            await update.message.reply_text("❌ База данных не подключена")
            return

        pending = await self.db.get_pending_scripts()

        if not pending:
            await update.message.reply_text("✅ Нет сценариев, ожидающих одобрения")
            return

        text = f"📝 <b>Ожидают одобрения ({len(pending)}):</b>\n\n"
        for script in pending[:10]:
            created = script.created_at.strftime("%d.%m %H:%M")
            text += f"#{script.id} - {script.topic[:40]}...\n   Создан: {created}\n\n"

        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        await update.message.reply_text(
            "🤖 <b>YouTube DeFi Monitor Bot</b>\n\n"
            "Этот бот мониторит YouTube каналы конкурентов, "
            "находит вирусные видео и генерирует сценарии.\n\n"
            "<b>Команды:</b>\n"
            "/status - Текущий статус системы\n"
            "/channels - Список отслеживаемых каналов\n"
            "/pending - Сценарии на одобрении\n"
            "/help - Эта справка\n\n"
            "<b>Кнопки под сценариями:</b>\n"
            "✅ Одобрить - пометить как готовый\n"
            "❌ Отклонить - удалить сценарий\n"
            "🔄 Переделать - запросить новую версию",
            parse_mode="HTML",
        )

    # === Callback Handlers ===

    async def _callback_approve(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle approve button callback."""
        query = update.callback_query
        await query.answer()

        script_id = int(query.data.replace("approve_", ""))

        if self.db:
            await self.db.update_script_status(script_id, ScriptStatus.APPROVED)

        await query.edit_message_text(
            f"✅ Сценарий #{script_id} одобрен!",
            parse_mode="HTML",
        )

        logger.info("script_approved", script_id=script_id)

    async def _callback_reject(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle reject button callback."""
        query = update.callback_query
        await query.answer()

        script_id = int(query.data.replace("reject_", ""))

        if self.db:
            await self.db.update_script_status(script_id, ScriptStatus.REJECTED)

        await query.edit_message_text(
            f"❌ Сценарий #{script_id} отклонён",
            parse_mode="HTML",
        )

        logger.info("script_rejected", script_id=script_id)

    async def _callback_regenerate(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle regenerate button callback."""
        query = update.callback_query
        await query.answer("🔄 Запрос на переделку отправлен")

        script_id = int(query.data.replace("regen_", ""))

        await query.edit_message_text(
            f"🔄 Сценарий #{script_id} будет переделан.\n"
            "Новая версия появится в ближайшее время.",
            parse_mode="HTML",
        )

        # TODO: Trigger regeneration
        logger.info("script_regenerate_requested", script_id=script_id)

    # === Polling ===

    async def start_polling(self) -> None:
        """Start the bot in polling mode (for local development)."""
        if not self.application:
            await self.initialize()

        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("telegram_bot_polling_started")

    async def stop_polling(self) -> None:
        """Stop polling."""
        if self.application and self.application.updater:
            await self.application.updater.stop()
            await self.application.stop()
