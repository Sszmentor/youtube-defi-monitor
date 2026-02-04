"""YouTube transcript extraction and parsing."""

import re
import structlog
from dataclasses import dataclass
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)


logger = structlog.get_logger()


@dataclass
class TranscriptSegment:
    """A segment of transcript with timing."""
    text: str
    start: float
    duration: float


@dataclass
class VideoTranscript:
    """Full video transcript with metadata."""
    video_id: str
    language: str
    segments: list[TranscriptSegment]
    full_text: str
    is_auto_generated: bool


class TranscriptExtractor:
    """Extract and process YouTube video transcripts."""

    # Preferred languages in order of priority
    PREFERRED_LANGUAGES = ["ru", "en", "en-US", "en-GB"]

    async def get_transcript(
        self,
        video_id: str,
        languages: Optional[list[str]] = None,
    ) -> Optional[VideoTranscript]:
        """Get transcript for a video.

        Args:
            video_id: YouTube video ID.
            languages: Preferred languages (defaults to ru, en).

        Returns:
            VideoTranscript or None if not available.
        """
        languages = languages or self.PREFERRED_LANGUAGES

        try:
            # Get list of available transcripts
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            transcript = None
            is_auto_generated = False
            used_language = None

            # Try to find manually created transcript first
            for lang in languages:
                try:
                    transcript = transcript_list.find_transcript([lang])
                    is_auto_generated = transcript.is_generated
                    used_language = lang
                    break
                except NoTranscriptFound:
                    continue

            # If no manual transcript, try auto-generated
            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript(languages)
                    is_auto_generated = True
                    used_language = transcript.language_code
                except NoTranscriptFound:
                    # Last resort: get any available transcript
                    try:
                        available = list(transcript_list)
                        if available:
                            transcript = available[0]
                            is_auto_generated = transcript.is_generated
                            used_language = transcript.language_code
                    except Exception:
                        pass

            if transcript is None:
                logger.warning("no_transcript_found", video_id=video_id)
                return None

            # Fetch the actual transcript data
            transcript_data = transcript.fetch()

            segments = [
                TranscriptSegment(
                    text=item["text"],
                    start=item["start"],
                    duration=item["duration"],
                )
                for item in transcript_data
            ]

            # Combine into full text
            full_text = self._combine_segments(segments)

            logger.info(
                "transcript_extracted",
                video_id=video_id,
                language=used_language,
                segments=len(segments),
                auto_generated=is_auto_generated,
            )

            return VideoTranscript(
                video_id=video_id,
                language=used_language,
                segments=segments,
                full_text=full_text,
                is_auto_generated=is_auto_generated,
            )

        except TranscriptsDisabled:
            logger.warning("transcripts_disabled", video_id=video_id)
            return None
        except VideoUnavailable:
            logger.warning("video_unavailable", video_id=video_id)
            return None
        except Exception as e:
            logger.error("transcript_error", video_id=video_id, error=str(e))
            return None

    def _combine_segments(self, segments: list[TranscriptSegment]) -> str:
        """Combine transcript segments into coherent text.

        Args:
            segments: List of transcript segments.

        Returns:
            Combined text with proper spacing.
        """
        if not segments:
            return ""

        texts = []
        for segment in segments:
            text = segment.text.strip()
            # Clean up auto-generated artifacts
            text = re.sub(r"\[.*?\]", "", text)  # Remove [Music], [Applause], etc.
            text = re.sub(r"\s+", " ", text)  # Normalize whitespace
            if text:
                texts.append(text)

        # Join with spaces, then fix punctuation
        combined = " ".join(texts)
        # Fix spacing around punctuation
        combined = re.sub(r"\s+([.,!?])", r"\1", combined)
        combined = re.sub(r"([.,!?])([A-ZА-Я])", r"\1 \2", combined)

        return combined.strip()

    def extract_structure(self, transcript: VideoTranscript) -> dict:
        """Analyze transcript structure to identify sections.

        Args:
            transcript: Video transcript.

        Returns:
            Dictionary with identified sections.
        """
        full_text = transcript.full_text
        segments = transcript.segments

        if not segments:
            return {"intro": "", "main": "", "outro": ""}

        # Calculate total duration
        total_duration = segments[-1].start + segments[-1].duration

        # Find section boundaries based on timing
        intro_end = min(60, total_duration * 0.1)  # First 60s or 10%
        outro_start = max(total_duration - 60, total_duration * 0.9)  # Last 60s or 10%

        intro_segments = []
        main_segments = []
        outro_segments = []

        for segment in segments:
            if segment.start < intro_end:
                intro_segments.append(segment.text)
            elif segment.start >= outro_start:
                outro_segments.append(segment.text)
            else:
                main_segments.append(segment.text)

        structure = {
            "intro": " ".join(intro_segments).strip(),
            "main": " ".join(main_segments).strip(),
            "outro": " ".join(outro_segments).strip(),
            "total_duration": total_duration,
            "word_count": len(full_text.split()),
        }

        # Try to identify key topics from main content
        structure["estimated_topics"] = self._extract_topics(structure["main"])

        return structure

    def _extract_topics(self, text: str, max_topics: int = 5) -> list[str]:
        """Extract potential topics from text.

        Simple extraction based on capitalized phrases and numbers.

        Args:
            text: Text to analyze.
            max_topics: Maximum number of topics.

        Returns:
            List of potential topic phrases.
        """
        if not text:
            return []

        topics = []

        # Find phrases with numbers (likely stats/facts)
        number_patterns = re.findall(
            r"[\w\s]{5,30}(?:\d+[%$]|\$\d+|\d+\s*(?:миллион|тысяч|процент|billion|million|thousand|percent))",
            text,
            re.IGNORECASE,
        )
        topics.extend(number_patterns[:max_topics])

        # Find capitalized terms (likely names/protocols)
        cap_words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        # Filter common words
        common = {"The", "This", "That", "What", "When", "Where", "How", "Why"}
        cap_words = [w for w in cap_words if w not in common]
        topics.extend(cap_words[:max_topics - len(topics)])

        return list(set(topics))[:max_topics]

    def get_timestamps_for_topics(
        self,
        transcript: VideoTranscript,
        topics: list[str],
    ) -> dict[str, float]:
        """Find timestamps where specific topics are mentioned.

        Args:
            transcript: Video transcript.
            topics: List of topics to search for.

        Returns:
            Dictionary mapping topics to first mention timestamp.
        """
        timestamps = {}

        for topic in topics:
            topic_lower = topic.lower()
            for segment in transcript.segments:
                if topic_lower in segment.text.lower():
                    timestamps[topic] = segment.start
                    break

        return timestamps
