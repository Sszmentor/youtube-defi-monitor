"""Fact verification service combining multiple data sources."""

import re
import structlog
from dataclasses import dataclass
from typing import Optional

from .claim_extractor import ExtractedClaim, ClaimExtractor
from .sources import DefiLlamaSource, CoinGeckoSource, FactData
from ..database.models import VerifiedFact, FactStatus


logger = structlog.get_logger()


@dataclass
class VerificationResult:
    """Result of fact verification."""
    claim: ExtractedClaim
    status: FactStatus
    source: Optional[str] = None
    verified_value: Optional[str] = None
    original_value: Optional[str] = None
    notes: Optional[str] = None


class FactVerifier:
    """Verify claims using multiple data sources."""

    def __init__(self):
        self.claim_extractor = ClaimExtractor()
        self.defillama = DefiLlamaSource()
        self.coingecko = CoinGeckoSource()

    async def verify_claims(
        self,
        transcript_text: str,
        video_id: str,
    ) -> list[VerifiedFact]:
        """Extract and verify all claims from transcript.

        Args:
            transcript_text: Video transcript text.
            video_id: Video ID for tracking.

        Returns:
            List of VerifiedFact objects.
        """
        # Extract claims from transcript
        claims = await self.claim_extractor.extract_claims(transcript_text)

        if not claims:
            logger.info("no_claims_to_verify", video_id=video_id)
            return []

        # Verify each claim
        verified_facts = []
        for claim in claims:
            result = await self.verify_single_claim(claim)

            fact = VerifiedFact(
                video_id=video_id,
                claim=claim.claim,
                status=result.status,
                source=result.source,
                verified_value=result.verified_value,
            )
            verified_facts.append(fact)

            logger.info(
                "claim_verified",
                claim=claim.claim[:50],
                status=result.status.value,
                source=result.source,
            )

        # Summary
        status_counts = {}
        for fact in verified_facts:
            status_counts[fact.status.value] = status_counts.get(fact.status.value, 0) + 1

        logger.info(
            "verification_complete",
            video_id=video_id,
            total_claims=len(verified_facts),
            status_counts=status_counts,
        )

        return verified_facts

    async def verify_single_claim(self, claim: ExtractedClaim) -> VerificationResult:
        """Verify a single claim against data sources.

        Args:
            claim: Extracted claim to verify.

        Returns:
            VerificationResult with status and details.
        """
        # Try to verify based on category and entities
        if claim.category == "tvl":
            return await self._verify_tvl_claim(claim)
        elif claim.category == "price":
            return await self._verify_price_claim(claim)
        elif claim.category == "percentage":
            return await self._verify_yield_claim(claim)
        else:
            # For other categories, try general verification
            return await self._verify_general_claim(claim)

    async def _verify_tvl_claim(self, claim: ExtractedClaim) -> VerificationResult:
        """Verify TVL-related claims using DefiLlama.

        Args:
            claim: TVL claim to verify.

        Returns:
            VerificationResult.
        """
        for entity in claim.entities:
            data = await self.defillama.get_protocol_tvl(entity)

            if data:
                # Extract claimed value from text
                claimed_value = self._extract_number(claim.claim)
                actual_value = data.value

                if claimed_value:
                    # Check if values are close (within 20%)
                    ratio = actual_value / claimed_value if claimed_value else 0
                    if 0.8 <= ratio <= 1.2:
                        return VerificationResult(
                            claim=claim,
                            status=FactStatus.VERIFIED,
                            source=self.defillama.name,
                            verified_value=f"${actual_value:,.0f}",
                            original_value=f"${claimed_value:,.0f}",
                        )
                    else:
                        return VerificationResult(
                            claim=claim,
                            status=FactStatus.OUTDATED,
                            source=self.defillama.name,
                            verified_value=f"${actual_value:,.0f}",
                            original_value=f"${claimed_value:,.0f}",
                            notes=f"Actual TVL differs: {ratio:.1%}",
                        )

                # If no number to compare, just confirm entity exists
                return VerificationResult(
                    claim=claim,
                    status=FactStatus.VERIFIED,
                    source=self.defillama.name,
                    verified_value=f"${actual_value:,.0f}",
                    notes="Protocol exists, TVL confirmed",
                )

        return VerificationResult(
            claim=claim,
            status=FactStatus.UNVERIFIED,
            notes="Could not find protocol data",
        )

    async def _verify_price_claim(self, claim: ExtractedClaim) -> VerificationResult:
        """Verify price-related claims using CoinGecko.

        Args:
            claim: Price claim to verify.

        Returns:
            VerificationResult.
        """
        for entity in claim.entities:
            data = await self.coingecko.get_token_price(entity)

            if data:
                claimed_price = self._extract_number(claim.claim)
                actual_price = data.value

                if claimed_price:
                    # Check if prices are close (within 10%)
                    ratio = actual_price / claimed_price if claimed_price else 0
                    if 0.9 <= ratio <= 1.1:
                        return VerificationResult(
                            claim=claim,
                            status=FactStatus.VERIFIED,
                            source=self.coingecko.name,
                            verified_value=f"${actual_price:,.2f}",
                            original_value=f"${claimed_price:,.2f}",
                        )
                    else:
                        return VerificationResult(
                            claim=claim,
                            status=FactStatus.OUTDATED,
                            source=self.coingecko.name,
                            verified_value=f"${actual_price:,.2f}",
                            original_value=f"${claimed_price:,.2f}",
                            notes=f"Price changed: {ratio:.1%}",
                        )

                return VerificationResult(
                    claim=claim,
                    status=FactStatus.VERIFIED,
                    source=self.coingecko.name,
                    verified_value=f"${actual_price:,.2f}",
                )

        return VerificationResult(
            claim=claim,
            status=FactStatus.UNVERIFIED,
            notes="Could not find token price",
        )

    async def _verify_yield_claim(self, claim: ExtractedClaim) -> VerificationResult:
        """Verify yield/APY claims using DefiLlama.

        Args:
            claim: Yield claim to verify.

        Returns:
            VerificationResult.
        """
        for entity in claim.entities:
            pools = await self.defillama.get_yields(entity)

            if pools:
                # Find the best matching pool
                claimed_apy = self._extract_percentage(claim.claim)

                for pool in pools[:10]:
                    pool_apy = pool.get("apy", 0)
                    if pool_apy and claimed_apy:
                        ratio = pool_apy / claimed_apy if claimed_apy else 0
                        if 0.7 <= ratio <= 1.3:
                            return VerificationResult(
                                claim=claim,
                                status=FactStatus.VERIFIED,
                                source=self.defillama.name,
                                verified_value=f"{pool_apy:.1f}%",
                                original_value=f"{claimed_apy:.1f}%",
                                notes=f"Pool: {pool.get('pool', 'unknown')}",
                            )

                # APY exists but doesn't match well
                if pools and claimed_apy:
                    avg_apy = sum(p.get("apy", 0) for p in pools[:5]) / min(5, len(pools))
                    return VerificationResult(
                        claim=claim,
                        status=FactStatus.OUTDATED,
                        source=self.defillama.name,
                        verified_value=f"~{avg_apy:.1f}%",
                        original_value=f"{claimed_apy:.1f}%",
                        notes="APY may have changed",
                    )

        return VerificationResult(
            claim=claim,
            status=FactStatus.UNVERIFIED,
            notes="Could not verify yield data",
        )

    async def _verify_general_claim(self, claim: ExtractedClaim) -> VerificationResult:
        """Try to verify claims using available sources.

        Args:
            claim: Claim to verify.

        Returns:
            VerificationResult.
        """
        # Try each entity against both sources
        for entity in claim.entities:
            # Try DefiLlama
            data = await self.defillama.query(entity)
            if data:
                return VerificationResult(
                    claim=claim,
                    status=FactStatus.VERIFIED,
                    source=self.defillama.name,
                    verified_value=str(data.value),
                    notes=f"Entity '{entity}' found",
                )

            # Try CoinGecko
            data = await self.coingecko.query(entity)
            if data:
                return VerificationResult(
                    claim=claim,
                    status=FactStatus.VERIFIED,
                    source=self.coingecko.name,
                    verified_value=f"${data.value:,.2f}",
                    notes=f"Token '{entity}' found",
                )

        return VerificationResult(
            claim=claim,
            status=FactStatus.UNVERIFIED,
            notes="No matching data found",
        )

    def _extract_number(self, text: str) -> Optional[float]:
        """Extract a number from text.

        Handles formats like:
        - $1.5 billion
        - 1,500,000
        - 1.5M
        - $100

        Args:
            text: Text containing a number.

        Returns:
            Extracted number or None.
        """
        text = text.lower().replace(",", "").replace(" ", "")

        # Find numbers with multipliers
        patterns = [
            (r"\$?([\d.]+)\s*(?:billion|млрд)", 1e9),
            (r"\$?([\d.]+)\s*(?:million|млн|m)", 1e6),
            (r"\$?([\d.]+)\s*(?:thousand|тыс|k)", 1e3),
            (r"\$?([\d.]+)", 1),
        ]

        for pattern, multiplier in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1)) * multiplier
                except ValueError:
                    continue

        return None

    def _extract_percentage(self, text: str) -> Optional[float]:
        """Extract a percentage from text.

        Args:
            text: Text containing a percentage.

        Returns:
            Extracted percentage or None.
        """
        # Match patterns like "15%", "15 процентов", "15.5%"
        patterns = [
            r"([\d.]+)\s*%",
            r"([\d.]+)\s*процент",
            r"([\d.]+)\s*percent",
        ]

        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue

        return None

    def get_only_verified(
        self,
        facts: list[VerifiedFact],
    ) -> list[VerifiedFact]:
        """Filter to only verified facts.

        Args:
            facts: List of all facts.

        Returns:
            List of verified facts only.
        """
        return [f for f in facts if f.status == FactStatus.VERIFIED]

    def format_fact_report(self, fact: VerifiedFact) -> str:
        """Format a fact for display.

        Args:
            fact: VerifiedFact to format.

        Returns:
            Formatted string.
        """
        status_emoji = {
            FactStatus.VERIFIED: "✅",
            FactStatus.OUTDATED: "⚠️",
            FactStatus.FALSE: "❌",
            FactStatus.UNVERIFIED: "❓",
        }

        emoji = status_emoji.get(fact.status, "❓")
        source = f" [{fact.source}]" if fact.source else ""
        value = f" → {fact.verified_value}" if fact.verified_value else ""

        return f"{emoji} {fact.claim}{value}{source}"
