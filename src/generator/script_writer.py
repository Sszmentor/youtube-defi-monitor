"""Script generator that creates original content based on analyzed structure."""

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anthropic
import openai

from ..config import get_config
from ..database.models import VerifiedFact, FactStatus
from .analyzer import VideoStructure


logger = structlog.get_logger()


@dataclass
class GeneratedScript:
    """Generated video script."""
    topic: str
    hook: str
    intro: str
    sections: list[dict]  # [{title: str, content: str}]
    cta: str
    outro: str
    full_text: str
    word_count: int
    estimated_duration_minutes: int


SCRIPT_GENERATION_PROMPT = """Ты опытный YouTube-сценарист, специализирующийся на DeFi и криптовалютах.

ЗАДАЧА: Напиши ОРИГИНАЛЬНЫЙ сценарий на тему "{topic}" для YouTube видео.

СТРУКТУРА УСПЕШНОГО ВИДЕО (референс):
{structure_summary}

ПРОВЕРЕННЫЕ ФАКТЫ (используй только их):
{verified_facts}

СТИЛЬ АВТОРА:
{style_guide}

ПРИМЕРЫ СТИЛЯ АВТОРА:
{style_examples}

ТРЕБОВАНИЯ:
1. Создай ПОЛНОСТЬЮ ОРИГИНАЛЬНЫЙ контент - не копируй исходное видео
2. Используй ТОЛЬКО проверенные факты из списка выше
3. Сохрани структуру успешного видео (крючок → intro → основные пункты → CTA → outro)
4. Пиши в стиле автора - {author_name}
5. Язык: русский
6. Длительность: ~{duration} минут

ФОРМАТ ОТВЕТА - JSON:
{{
    "topic": "Тема видео",
    "hook": "Захватывающее начало (1-2 предложения)",
    "intro": "Введение в тему (2-3 предложения)",
    "sections": [
        {{"title": "Название раздела 1", "content": "Контент раздела..."}},
        {{"title": "Название раздела 2", "content": "Контент раздела..."}}
    ],
    "cta": "Призыв к действию",
    "outro": "Завершение видео"
}}

Напиши сценарий:"""


class ScriptWriter:
    """Generate original video scripts based on analyzed structure."""

    def __init__(self):
        config = get_config()
        self.provider = config.llm.provider
        self.model = config.llm.model
        self.api_key = config.llm.api_key
        self.style_config = config.style

        if self.provider == "anthropic":
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = openai.OpenAI(api_key=self.api_key)

        # Load style examples
        self.style_examples = self._load_style_examples()

    def _load_style_examples(self) -> str:
        """Load style examples from file.

        Returns:
            Style examples text or default message.
        """
        examples_path = Path(self.style_config.examples_file)

        if examples_path.exists():
            try:
                return examples_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("could_not_load_style_examples", error=str(e))

        return "Примеры стиля не загружены. Используй естественный разговорный стиль."

    async def generate_script(
        self,
        topic: str,
        structure: VideoStructure,
        verified_facts: list[VerifiedFact],
        target_duration: int = 10,
    ) -> Optional[GeneratedScript]:
        """Generate a new script based on structure and facts.

        Args:
            topic: Video topic.
            structure: Analyzed structure of reference video.
            verified_facts: List of verified facts to use.
            target_duration: Target duration in minutes.

        Returns:
            GeneratedScript or None on error.
        """
        # Format structure summary
        structure_summary = self._format_structure_for_prompt(structure)

        # Format verified facts
        facts_text = self._format_facts_for_prompt(verified_facts)

        # Style guide
        style_guide = f"""
Тон: {self.style_config.tone}
Язык: {self.style_config.language}
"""

        try:
            prompt = SCRIPT_GENERATION_PROMPT.format(
                topic=topic,
                structure_summary=structure_summary,
                verified_facts=facts_text,
                style_guide=style_guide,
                style_examples=self.style_examples[:3000],  # Limit examples
                author_name=self.style_config.author_name,
                duration=target_duration,
            )

            if self.provider == "anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                response_text = response.content[0].text
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
                )
                response_text = response.choices[0].message.content

            # Parse response
            script = self._parse_script_response(response_text, topic)

            if script:
                logger.info(
                    "script_generated",
                    topic=topic,
                    word_count=script.word_count,
                    sections=len(script.sections),
                )

            return script

        except Exception as e:
            logger.error("script_generation_error", error=str(e), topic=topic)
            return None

    def _format_structure_for_prompt(self, structure: VideoStructure) -> str:
        """Format video structure for the prompt.

        Args:
            structure: VideoStructure to format.

        Returns:
            Formatted string.
        """
        lines = [
            f"Крючок: {structure.hook}",
            f"Введение: {structure.intro}",
            "Основные пункты:",
        ]

        for i, point in enumerate(structure.main_points, 1):
            lines.append(f"  {i}. {point}")

        lines.extend([
            f"CTA: {structure.cta}",
            f"Закрытие: {structure.outro}",
            f"Тон: {structure.tone}",
            f"Аудитория: {structure.target_audience}",
        ])

        return "\n".join(lines)

    def _format_facts_for_prompt(self, facts: list[VerifiedFact]) -> str:
        """Format verified facts for the prompt.

        Args:
            facts: List of VerifiedFact objects.

        Returns:
            Formatted string.
        """
        if not facts:
            return "Нет проверенных фактов. Используй общеизвестную информацию."

        # Only use verified facts
        verified = [f for f in facts if f.status == FactStatus.VERIFIED]

        if not verified:
            return "Нет проверенных фактов. Используй общеизвестную информацию."

        lines = []
        for fact in verified[:20]:  # Limit to 20 facts
            line = f"• {fact.claim}"
            if fact.verified_value:
                line += f" [{fact.verified_value}]"
            if fact.source:
                line += f" (источник: {fact.source})"
            lines.append(line)

        return "\n".join(lines)

    def _parse_script_response(
        self,
        text: str,
        topic: str,
    ) -> Optional[GeneratedScript]:
        """Parse script from LLM response.

        Args:
            text: Response text.
            topic: Original topic.

        Returns:
            GeneratedScript or None.
        """
        import json

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

        # Find JSON object
        if "{" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            text = text[start:end]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("script_json_parse_error", error=str(e))
            # Try to extract content manually
            return self._fallback_parse(text, topic)

        # Build full text
        sections = data.get("sections", [])
        full_text_parts = [
            data.get("hook", ""),
            data.get("intro", ""),
        ]

        for section in sections:
            if isinstance(section, dict):
                full_text_parts.append(f"\n{section.get('title', '')}\n")
                full_text_parts.append(section.get("content", ""))

        full_text_parts.extend([
            data.get("cta", ""),
            data.get("outro", ""),
        ])

        full_text = "\n\n".join(p for p in full_text_parts if p)
        word_count = len(full_text.split())
        estimated_duration = max(1, word_count // 150)  # ~150 words per minute

        return GeneratedScript(
            topic=data.get("topic", topic),
            hook=data.get("hook", ""),
            intro=data.get("intro", ""),
            sections=sections,
            cta=data.get("cta", ""),
            outro=data.get("outro", ""),
            full_text=full_text,
            word_count=word_count,
            estimated_duration_minutes=estimated_duration,
        )

    def _fallback_parse(self, text: str, topic: str) -> Optional[GeneratedScript]:
        """Fallback parsing when JSON fails.

        Args:
            text: Raw text.
            topic: Topic.

        Returns:
            GeneratedScript or None.
        """
        if len(text) < 100:
            return None

        word_count = len(text.split())

        return GeneratedScript(
            topic=topic,
            hook="",
            intro="",
            sections=[{"title": "Основной контент", "content": text}],
            cta="",
            outro="",
            full_text=text,
            word_count=word_count,
            estimated_duration_minutes=max(1, word_count // 150),
        )

    def format_script_for_display(self, script: GeneratedScript) -> str:
        """Format script for Telegram/display.

        Args:
            script: GeneratedScript to format.

        Returns:
            Formatted string.
        """
        lines = [
            f"📝 СЦЕНАРИЙ: {script.topic}",
            f"📊 {script.word_count} слов | ~{script.estimated_duration_minutes} мин",
            "",
            "═" * 40,
            "",
            f"🎣 КРЮЧОК:\n{script.hook}",
            "",
            f"📖 ВВЕДЕНИЕ:\n{script.intro}",
            "",
        ]

        for i, section in enumerate(script.sections, 1):
            if isinstance(section, dict):
                lines.append(f"📌 {i}. {section.get('title', 'Раздел')}")
                lines.append(section.get("content", ""))
                lines.append("")

        lines.extend([
            f"🎯 CTA:\n{script.cta}",
            "",
            f"👋 ЗАВЕРШЕНИЕ:\n{script.outro}",
        ])

        return "\n".join(lines)

    def format_script_for_teleprompter(self, script: GeneratedScript) -> str:
        """Format script for teleprompter/reading.

        Args:
            script: GeneratedScript.

        Returns:
            Clean text for reading.
        """
        return script.full_text
