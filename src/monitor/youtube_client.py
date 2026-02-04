"""YouTube Data API client for monitoring channels."""

import structlog
from datetime import datetime, timedelta
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..config import get_config, ChannelConfig
from ..database.models import Channel, Video


logger = structlog.get_logger()


class YouTubeClient:
    """Client for YouTube Data API v3."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize YouTube client.

        Args:
            api_key: YouTube Data API key. If not provided, uses config.
        """
        self.api_key = api_key or get_config().youtube_api_key
        if not self.api_key:
            raise ValueError("YouTube API key is required")

        self._youtube = build("youtube", "v3", developerKey=self.api_key)

    async def get_channel_info(self, channel_id: str) -> Optional[Channel]:
        """Get channel information including subscriber count.

        Args:
            channel_id: YouTube channel ID (starts with UC).

        Returns:
            Channel object or None if not found.
        """
        try:
            response = self._youtube.channels().list(
                part="snippet,statistics",
                id=channel_id,
            ).execute()

            if not response.get("items"):
                logger.warning("channel_not_found", channel_id=channel_id)
                return None

            item = response["items"][0]
            return Channel(
                id=channel_id,
                name=item["snippet"]["title"],
                subscribers=int(item["statistics"].get("subscriberCount", 0)),
                last_checked=datetime.utcnow(),
            )

        except HttpError as e:
            logger.error("youtube_api_error", error=str(e), channel_id=channel_id)
            return None

    async def get_recent_videos(
        self,
        channel_id: str,
        max_age_days: int = 7,
        max_results: int = 50,
    ) -> list[dict]:
        """Get recent videos from a channel.

        Args:
            channel_id: YouTube channel ID.
            max_age_days: Maximum age of videos in days.
            max_results: Maximum number of results.

        Returns:
            List of video data dictionaries.
        """
        try:
            # Calculate date threshold
            published_after = (
                datetime.utcnow() - timedelta(days=max_age_days)
            ).isoformat() + "Z"

            # Search for videos
            search_response = self._youtube.search().list(
                part="id",
                channelId=channel_id,
                type="video",
                order="date",
                publishedAfter=published_after,
                maxResults=max_results,
            ).execute()

            video_ids = [
                item["id"]["videoId"]
                for item in search_response.get("items", [])
            ]

            if not video_ids:
                return []

            # Get video details
            videos_response = self._youtube.videos().list(
                part="snippet,statistics",
                id=",".join(video_ids),
            ).execute()

            videos = []
            for item in videos_response.get("items", []):
                videos.append({
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "description": item["snippet"]["description"],
                    "published_at": item["snippet"]["publishedAt"],
                    "channel_id": item["snippet"]["channelId"],
                    "views": int(item["statistics"].get("viewCount", 0)),
                    "likes": int(item["statistics"].get("likeCount", 0)),
                    "comments": int(item["statistics"].get("commentCount", 0)),
                })

            logger.info(
                "fetched_videos",
                channel_id=channel_id,
                count=len(videos),
            )
            return videos

        except HttpError as e:
            logger.error("youtube_api_error", error=str(e), channel_id=channel_id)
            return []

    async def get_video_details(self, video_id: str) -> Optional[dict]:
        """Get detailed information about a specific video.

        Args:
            video_id: YouTube video ID.

        Returns:
            Video data dictionary or None.
        """
        try:
            response = self._youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=video_id,
            ).execute()

            if not response.get("items"):
                return None

            item = response["items"][0]
            return {
                "id": item["id"],
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "published_at": item["snippet"]["publishedAt"],
                "channel_id": item["snippet"]["channelId"],
                "channel_title": item["snippet"]["channelTitle"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "likes": int(item["statistics"].get("likeCount", 0)),
                "comments": int(item["statistics"].get("commentCount", 0)),
                "duration": item["contentDetails"]["duration"],
                "tags": item["snippet"].get("tags", []),
            }

        except HttpError as e:
            logger.error("youtube_api_error", error=str(e), video_id=video_id)
            return None

    async def monitor_channels(
        self,
        channels: list[ChannelConfig],
        max_video_age_days: int = 7,
    ) -> tuple[list[Channel], list[dict]]:
        """Monitor multiple channels and collect their videos.

        Args:
            channels: List of channel configurations.
            max_video_age_days: Maximum age of videos to fetch.

        Returns:
            Tuple of (updated channels, all videos).
        """
        updated_channels = []
        all_videos = []

        for channel_config in channels:
            # Get channel info
            channel = await self.get_channel_info(channel_config.id)
            if channel:
                channel.name = channel_config.name  # Use configured name
                updated_channels.append(channel)

                # Get recent videos
                videos = await self.get_recent_videos(
                    channel_config.id,
                    max_age_days=max_video_age_days,
                )
                all_videos.extend(videos)

                logger.info(
                    "monitored_channel",
                    channel_id=channel_config.id,
                    name=channel_config.name,
                    subscribers=channel.subscribers,
                    videos_found=len(videos),
                )

        return updated_channels, all_videos
