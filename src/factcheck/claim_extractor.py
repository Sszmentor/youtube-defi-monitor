"""Extract verifiable claims from video transcripts using LLM."""

import json
import structlog
from dataclasses import dataclass
from typing import Optional

import anthropic
import openai

from ..config import get_config


logger = structlog.get_logger()


@dataclass
class ExtractedClaim:
    """A claim extracted from text that can be verified."""
    claim: str
    category: str  # "price", "tvl", "percentage", "date", "protocol_info", "other"
    entities: list[str]  # Mentioned protocols, tokens, etc.
    original_text: str
    confidence: float  # 0-1 confidence that this is a verifiable claim


CLAIM_EXTRACTION_PROMPT = """Ты эксперт по DeFi и криптовалютам. Проанализируй следующий текст (транскрипт YouTube видео) и извлеки все фактические утверждения, которые можно проверить.

Для каждого утверждения определи:
1. claim - само утверждение на русском языке
2. category - категория: "price" (цены), "tvl" (TVL/ликвидность), "percentage" (проценты, доходность), "date" (даты, сроки), "protocol_info" (информация о протоколах), "other" (другое)
3. entities - список упомянутых протоколов, токенов, блокчейнов
4. original_text - оригинальный фрагмент текста
5. confidence - уверенность 0-1, что это проверяемое утверждение

Фокусируйся на:
- Числовые данные (TVL, цены, проценты APY/APR)
- Названия протоколов и их характеристики
- Даты запуска, обновлений
- Статистика (количество пользователей, объемы)

Игнорируй:
- Субъективные мнения
- Прогнозы и предположения
- Рекламные фразы без конкретики

Верни JSON массив с объектами. Если проверяемых утверждений нет, верни пустой массив [].

Текст для анализа:
---
{text}
---

Ответ в формате JSON:"""


class ClaimExtractor:
    """Extract verifiable claims from text using LLM."""

    def __init__(self):
        config = get_config()
        self.provider = config.llm.provider
        self.model = config.llm.model
        self.api_key = config.llm.api_key

        if self.provider == "anthropic":
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = openai.OpenAI(api_key=self.api_key)

    async def extract_claims(
        self,
        text: str,
        max_claims: int = 20,
    ) -> list[ExtractedClaim]:
        """Extract verifiable claims from text.

        Args:
            text: Text to analyze (transcript).
            max_claims: Maximum number of claims to extract.

        Returns:
            List of extracted claims.
        """
        if not text or len(text) < 50:
            return []

        # Truncate very long texts
        if len(text) > 15000:
            text = text[:15000] + "..."

        try:
            prompt = CLAIM_EXTRACTION_PROMPT.format(text=text)

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

            # Parse JSON response
            claims_data = self._parse_json_response(response_text)

            claims = []
            for item in claims_data[:max_claims]:
                try:
                    claim = ExtractedClaim(
                        claim=item.get("claim", ""),
                        category=item.get("category", "other"),
                        entities=item.get("entities", []),
                        original_text=item.get("original_text", ""),
                        confidence=float(item.get("confidence", 0.5)),
                    )
                    if claim.claim and claim.confidence >= 0.5:
                        claims.append(claim)
                except (KeyError, ValueError) as e:
                    logger.warning("claim_parse_error", error=str(e), item=item)

            logger.info(
                "claims_extracted",
                total=len(claims),
                categories={c.category for c in claims},
            )
            return claims

        except Exception as e:
            logger.error("claim_extraction_error", error=str(e))
            return []

    def _parse_json_response(self, text: str) -> list[dict]:
        """Parse JSON from LLM response.

        Args:
            text: Response text potentially containing JSON.

        Returns:
            Parsed list of dictionaries.
        """
        # Try to find JSON array in response
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

        # Find array boundaries
        if "[" in text:
            start = text.find("[")
            end = text.rfind("]") + 1
            text = text[start:end]

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError as e:
            logger.warning("json_parse_error", error=str(e), text=text[:200])
            return []

    def categorize_claim(self, claim: str) -> str:
        """Categorize a claim based on its content.

        Args:
            claim: Claim text.

        Returns:
            Category string.
        """
        claim_lower = claim.lower()

        if any(w in claim_lower for w in ["цена", "price", "$", "usd", "стоит"]):
            return "price"
        if any(w in claim_lower for w in ["tvl", "ликвидност", "liquidity", "locked"]):
            return "tvl"
        if any(w in claim_lower for w in ["%", "процент", "apy", "apr", "доходност"]):
            return "percentage"
        if any(w in claim_lower for w in ["запуск", "launch", "дата", "год", "месяц"]):
            return "date"
        if any(w in claim_lower for w in ["протокол", "protocol", "сеть", "chain"]):
            return "protocol_info"

        return "other"
