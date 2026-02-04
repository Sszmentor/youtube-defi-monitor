"""Virality checker with adaptive thresholds based on channel size."""

import structlog
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..config import get_config, ViralityThresholds
from ..database.models import Video, Channel


logger = structlog.get_logger()


@dataclass
class ViralityResult:
    """Result of virality check."""
    is_viral: bool
    score: float
    threshold_used: float
    channel_category: str  # "small", "medium", "large"
    views: int
    subscribers: int


class ViralityChecker:
    """Check if videos are viral based on adaptive thresholds.

    Thresholds are based on channel size:
    - Small channels (< 5K subs): views >= subs * 1.5
    - Medium channels (5K-50K subs): views >= subs * 1.0
    - Large channels (> 50K subs): views >= subs * 0.3 (30%)
    """

    def __init__(self, thresholds: Optional[ViralityThresholds] = None):
        """Initialize with thresholds from config or custom.

        Args:
            thresholds: Custom thresholds or None to use config.
        """
        self.thresholds = thresholds or get_config().monitoring.virality_thresholds

    def get_channel_category(self, subscribers: int) -> str:
        """Determine channel category based on subscriber count.

        Args:
            subscribers: Number of subscribers.

        Returns:
            Category name: "small", "medium", or "large".
        """
        if subscribers <= self.thresholds.small.max_subs:
            return "small"
        elif subscribers <= self.thresholds.medium.max_subs:
            return "medium"
        else:
            return "large"

    def get_threshold_ratio(self, category: str) -> float:
        """Get the virality threshold ratio for a category.

        Args:
            category: Channel category name.

        Returns:
            Threshold ratio (views/subs required for virality).
        """
        if category == "small":
            return self.thresholds.small.ratio
        elif category == "medium":
            return self.thresholds.medium.ratio
        else:
            return self.thresholds.large.ratio

    def check_virality(
        self,
        views: int,
        subscribers: int,
    ) -> ViralityResult:
        """Check if a video is viral based on views and subscriber count.

        Args:
            views: Video view count.
            subscribers: Channel subscriber count.

        Returns:
            ViralityResult with all details.
        """
        # Avoid division by zero
        if subscribers <= 0:
            return ViralityResult(
                is_viral=False,
                score=0.0,
                threshold_used=0.0,
                channel_category="unknown",
                views=views,
                subscribers=subscribers,
            )

        category = self.get_channel_category(subscribers)
        threshold_ratio = self.get_threshold_ratio(category)

        # Calculate virality score (views / subscribers)
        score = views / subscribers

        # Check if viral
        is_viral = score >= threshold_ratio

        return ViralityResult(
            is_viral=is_viral,
            score=round(score, 3),
            threshold_used=threshold_ratio,
            channel_category=category,
            views=views,
            subscribers=subscribers,
        )

    def filter_viral_videos(
        self,
        videos: list[dict],
        channel: Channel,
    ) -> list[Video]:
        """Filter videos that meet virality threshold.

        Args:
            videos: List of video data dictionaries.
            channel: Channel object with subscriber info.

        Returns:
            List of Video objects that are viral.
        """
        viral_videos = []

        for video_data in videos:
            result = self.check_virality(
                views=video_data["views"],
                subscribers=channel.subscribers,
            )

            if result.is_viral:
                video = Video(
                    id=video_data["id"],
                    channel_id=channel.id,
                    title=video_data["title"],
                    views=video_data["views"],
                    published_at=datetime.fromisoformat(
                        video_data["published_at"].replace("Z", "+00:00")
                    ),
                    virality_score=result.score,
                )

                viral_videos.append(video)

                logger.info(
                    "viral_video_found",
                    video_id=video.id,
                    title=video.title[:50],
                    views=result.views,
                    subscribers=result.subscribers,
                    score=result.score,
                    threshold=result.threshold_used,
                    category=result.channel_category,
                )

        return viral_videos

    def format_virality_report(self, result: ViralityResult) -> str:
        """Format a human-readable virality report.

        Args:
            result: ViralityResult to format.

        Returns:
            Formatted string report.
        """
        status = "VIRAL" if result.is_viral else "Normal"
        category_emoji = {
            "small": "🏠",
            "medium": "🏢",
            "large": "🏰",
        }.get(result.channel_category, "❓")

        return (
            f"{status} | Score: {result.score:.2f}x "
            f"(threshold: {result.threshold_used}x)\n"
            f"{category_emoji} Channel: {result.channel_category} "
            f"({result.subscribers:,} subs)\n"
            f"👁 Views: {result.views:,}"
        )
