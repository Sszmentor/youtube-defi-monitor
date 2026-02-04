"""Main entry point for YouTube DeFi Monitor."""

import asyncio
import structlog
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_config
from .database.models import Database, Video, Script
from .monitor.youtube_client import YouTubeClient
from .monitor.virality_checker import ViralityChecker
from .transcript.extractor import TranscriptExtractor
from .factcheck.verifier import FactVerifier
from .generator.analyzer import StructureAnalyzer
from .generator.script_writer import ScriptWriter
from .notify.telegram_bot import TelegramNotifier


# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


class YouTubeMonitor:
    """Main application class for YouTube monitoring."""

    def __init__(self):
        self.config = get_config()
        self.db = Database(self.config.database.path)

        # Services
        self.youtube = YouTubeClient()
        self.virality_checker = ViralityChecker()
        self.transcript_extractor = TranscriptExtractor()
        self.fact_verifier = FactVerifier()
        self.structure_analyzer = StructureAnalyzer()
        self.script_writer = ScriptWriter()
        self.telegram = TelegramNotifier(self.db)

        # Scheduler
        self.scheduler = AsyncIOScheduler()

    async def initialize(self) -> None:
        """Initialize all services."""
        await self.db.connect()
        await self.telegram.initialize()

        # Initialize channels in database
        for channel_config in self.config.channels:
            channel = await self.youtube.get_channel_info(channel_config.id)
            if channel:
                channel.name = channel_config.name
                await self.db.upsert_channel(channel)

        logger.info("monitor_initialized", channels=len(self.config.channels))

    async def shutdown(self) -> None:
        """Shutdown all services."""
        self.scheduler.shutdown()
        await self.telegram.shutdown()
        await self.db.close()
        logger.info("monitor_shutdown")

    async def run_monitoring_cycle(self) -> None:
        """Run a full monitoring cycle."""
        logger.info("monitoring_cycle_started")

        try:
            # 1. Monitor channels and find viral videos
            viral_videos = await self._find_viral_videos()

            if not viral_videos:
                logger.info("no_viral_videos_found")
                return

            # Notify about viral videos
            await self.telegram.notify_viral_videos(viral_videos)

            # 2. Process each viral video
            scripts_generated = 0
            for video in viral_videos:
                # Skip if we already have a script for this video
                if await self.db.script_exists_for_video(video.id):
                    logger.info("script_already_exists", video_id=video.id)
                    continue

                script = await self._process_video(video)
                if script:
                    scripts_generated += 1

            # 3. Send daily summary
            await self.telegram.notify_daily_summary(
                channels_checked=len(self.config.channels),
                viral_found=len(viral_videos),
                scripts_generated=scripts_generated,
            )

            logger.info(
                "monitoring_cycle_completed",
                viral_videos=len(viral_videos),
                scripts_generated=scripts_generated,
            )

        except Exception as e:
            logger.error("monitoring_cycle_error", error=str(e))
            await self.telegram.send_message(f"❌ Ошибка мониторинга: {str(e)}")

    async def _find_viral_videos(self) -> list[Video]:
        """Find viral videos across all monitored channels.

        Returns:
            List of viral videos.
        """
        all_viral = []

        for channel_config in self.config.channels:
            # Get channel from database
            channel = await self.db.get_channel(channel_config.id)
            if not channel:
                continue

            # Update channel info
            updated_channel = await self.youtube.get_channel_info(channel_config.id)
            if updated_channel:
                updated_channel.name = channel_config.name
                await self.db.upsert_channel(updated_channel)
                channel = updated_channel

            # Get recent videos
            videos = await self.youtube.get_recent_videos(
                channel_config.id,
                max_age_days=self.config.monitoring.max_video_age_days,
            )

            # Filter viral videos
            viral = self.virality_checker.filter_viral_videos(videos, channel)

            # Save to database and collect new ones
            for video in viral:
                if not await self.db.video_exists(video.id):
                    await self.db.insert_video(video)
                    all_viral.append(video)

        return all_viral

    async def _process_video(self, video: Video) -> Script | None:
        """Process a viral video: extract transcript, verify facts, generate script.

        Args:
            video: Video to process.

        Returns:
            Generated Script or None.
        """
        logger.info("processing_video", video_id=video.id, title=video.title[:50])

        # 1. Extract transcript
        transcript = await self.transcript_extractor.get_transcript(video.id)
        if not transcript:
            logger.warning("no_transcript", video_id=video.id)
            return None

        # Update video with transcript
        structure = self.transcript_extractor.extract_structure(transcript)
        await self.db.update_video_transcript(
            video.id,
            transcript.full_text,
            structure,
        )

        # 2. Analyze video structure
        video_structure = await self.structure_analyzer.analyze(transcript)
        if not video_structure:
            logger.warning("structure_analysis_failed", video_id=video.id)
            return None

        # 3. Verify facts
        verified_facts = await self.fact_verifier.verify_claims(
            transcript.full_text,
            video.id,
        )

        # Save facts to database
        for fact in verified_facts:
            await self.db.insert_fact(fact)

        # Get only verified facts for script generation
        verified_only = self.fact_verifier.get_only_verified(verified_facts)

        logger.info(
            "facts_verified",
            video_id=video.id,
            total=len(verified_facts),
            verified=len(verified_only),
        )

        # 4. Generate script
        generated = await self.script_writer.generate_script(
            topic=video.title,
            structure=video_structure,
            verified_facts=verified_only,
            target_duration=video_structure.estimated_duration_minutes,
        )

        if not generated:
            logger.warning("script_generation_failed", video_id=video.id)
            return None

        # Save script
        script = Script(
            source_video_id=video.id,
            topic=generated.topic,
            script_text=generated.full_text,
        )
        script_id = await self.db.insert_script(script)
        script.id = script_id

        # Notify via Telegram
        await self.telegram.notify_new_script(script, video)

        logger.info("script_generated", script_id=script_id, video_id=video.id)

        return script

    def start_scheduler(self) -> None:
        """Start the scheduler for periodic monitoring."""
        # Parse cron expression from config
        cron_expr = self.config.monitoring.check_interval
        parts = cron_expr.split()

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        self.scheduler.add_job(
            self.run_monitoring_cycle,
            trigger=trigger,
            id="monitoring",
            name="YouTube Monitoring",
        )

        self.scheduler.start()
        logger.info("scheduler_started", cron=cron_expr)


async def main():
    """Main entry point."""
    monitor = YouTubeMonitor()

    try:
        await monitor.initialize()

        # Run initial cycle
        await monitor.run_monitoring_cycle()

        # Start scheduler
        monitor.start_scheduler()

        # Start Telegram bot polling
        await monitor.telegram.start_polling()

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("shutting_down")
    finally:
        await monitor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
