"""Video structure analyzer using LLM."""

import json
import structlog
from dataclasses import dataclass
from typing import Optional

import anthropic
import openai

from ..config import get_config
from ..transcript.extractor import VideoTranscript


logger = structlog.get_logger()


@dataclass
class VideoStructure:
    """Analyzed video structure."""
    hook: str  # Attention-grabbing opening
    intro: str  # Introduction/context
    main_points: list[str]  # Key points/sections
    examples: list[str]  # Examples/case studies used
    cta: str  # Call to action
    outro: str  # Closing
    tone: str  # Overall tone
    target_audience: str  # Identified audience
    estimated_duration_minutes: int


STRUCTURE_ANALYSIS_PROMPT = """Ты эксперт по анализу YouTube видео. Проанализируй структуру следующего транскрипта видео о DeFi/криптовалютах.

Определи:
1. hook - захватывающий крючок в начале (цитата или пересказ)
2. intro - введение, контекст темы
3. main_points - список основных пунктов/разделов видео (3-7 пунктов)
4. examples - примеры, кейсы, упомянутые протоколы
5. cta - призыв к действию (подписка, комментарий, и т.д.)
6. outro - закрытие видео
7. tone - тон подачи (экспертный, дружеский, новостной, и т.д.)
8. target_audience - целевая аудитория
9. estimated_duration_minutes - примерная длительность в минутах

Транскрипт:
---
{transcript}
---

Верни JSON объект с этими полями. Для main_points и examples верни массивы строк."""


class StructureAnalyzer:
    """Analyze video structure using LLM."""

    def __init__(self):
        config = get_config()
        self.provider = config.llm.provider
        self.model = config.llm.model
        self.api_key = config.llm.api_key

        if self.provider == "anthropic":
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = openai.OpenAI(api_key=self.api_key)

    async def analyze(self, transcript: VideoTranscript) -> Optional[VideoStructure]:
        """Analyze video structure from transcript.

        Args:
            transcript: Video transcript.

        Returns:
            VideoStructure or None on error.
        """
        text = transcript.full_text

        if not text or len(text) < 100:
            logger.warning("transcript_too_short", video_id=transcript.video_id)
            return None

        # Truncate very long transcripts
        if len(text) > 20000:
            text = text[:20000] + "..."

        try:
            prompt = STRUCTURE_ANALYSIS_PROMPT.format(transcript=text)

            if self.provider == "anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                response_text = response.content[0].text
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2048,
                )
                response_text = response.choices[0].message.content

            # Parse JSON response
            data = self._parse_json_response(response_text)

            if not data:
                logger.warning("could_not_parse_structure", video_id=transcript.video_id)
                return None

            structure = VideoStructure(
                hook=data.get("hook", ""),
                intro=data.get("intro", ""),
                main_points=data.get("main_points", []),
                examples=data.get("examples", []),
                cta=data.get("cta", ""),
                outro=data.get("outro", ""),
                tone=data.get("tone", ""),
                target_audience=data.get("target_audience", ""),
                estimated_duration_minutes=data.get("estimated_duration_minutes", 10),
            )

            logger.info(
                "structure_analyzed",
                video_id=transcript.video_id,
                main_points=len(structure.main_points),
                examples=len(structure.examples),
            )

            return structure

        except Exception as e:
            logger.error("structure_analysis_error", error=str(e))
            return None

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """Parse JSON from LLM response.

        Args:
            text: Response text.

        Returns:
            Parsed dictionary or None.
        """
        text = text.strip()

        # Handle markdown code blocks
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            text = text[start:end].strip()

        # Find object boundaries
        if "{" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            text = text[start:end]

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("json_parse_error", error=str(e))
            return None

    def structure_to_dict(self, structure: VideoStructure) -> dict:
        """Convert structure to dictionary for storage.

        Args:
            structure: VideoStructure object.

        Returns:
            Dictionary representation.
        """
        return {
            "hook": structure.hook,
            "intro": structure.intro,
            "main_points": structure.main_points,
            "examples": structure.examples,
            "cta": structure.cta,
            "outro": structure.outro,
            "tone": structure.tone,
            "target_audience": structure.target_audience,
            "estimated_duration_minutes": structure.estimated_duration_minutes,
        }

    def format_structure_summary(self, structure: VideoStructure) -> str:
        """Format structure as readable summary.

        Args:
            structure: VideoStructure to format.

        Returns:
            Formatted string.
        """
        lines = [
            f"🎯 Целевая аудитория: {structure.target_audience}",
            f"🎭 Тон: {structure.tone}",
            f"⏱ Длительность: ~{structure.estimated_duration_minutes} мин",
            "",
            "📌 Основные пункты:",
        ]

        for i, point in enumerate(structure.main_points, 1):
            lines.append(f"  {i}. {point}")

        if structure.examples:
            lines.append("")
            lines.append("💡 Примеры:")
            for example in structure.examples[:5]:
                lines.append(f"  • {example}")

        return "\n".join(lines)
