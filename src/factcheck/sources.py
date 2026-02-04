"""Data sources for fact verification (DefiLlama, CoinGecko)."""

import httpx
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any
from datetime import datetime


logger = structlog.get_logger()


@dataclass
class FactData:
    """Data retrieved from a source for fact verification."""
    source: str
    query: str
    value: Any
    unit: Optional[str] = None
    timestamp: datetime = None
    raw_data: Optional[dict] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class DataSource(ABC):
    """Abstract base class for data sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Source name."""
        pass

    @abstractmethod
    async def query(self, query: str) -> Optional[FactData]:
        """Query the source for data.

        Args:
            query: Search query (protocol name, token, etc.)

        Returns:
            FactData if found, None otherwise.
        """
        pass


class DefiLlamaSource(DataSource):
    """DefiLlama API for DeFi protocol data."""

    BASE_URL = "https://api.llama.fi"

    @property
    def name(self) -> str:
        return "defillama"

    async def query(self, query: str) -> Optional[FactData]:
        """Query DefiLlama for protocol/TVL data."""
        # Try different query types
        result = await self.get_protocol_tvl(query)
        if result:
            return result

        result = await self.get_chain_tvl(query)
        if result:
            return result

        return None

    async def get_protocol_tvl(self, protocol: str) -> Optional[FactData]:
        """Get TVL for a specific protocol.

        Args:
            protocol: Protocol name (e.g., "aave", "uniswap").

        Returns:
            FactData with TVL info.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Get protocol data
                response = await client.get(
                    f"{self.BASE_URL}/protocol/{protocol.lower()}",
                    timeout=10.0,
                )

                if response.status_code != 200:
                    return None

                data = response.json()

                tvl = data.get("tvl")
                if tvl is None:
                    return None

                # Get current TVL (last entry)
                if isinstance(tvl, list) and tvl:
                    current_tvl = tvl[-1].get("totalLiquidityUSD", 0)
                else:
                    current_tvl = tvl

                logger.info(
                    "defillama_tvl_fetched",
                    protocol=protocol,
                    tvl=current_tvl,
                )

                return FactData(
                    source=self.name,
                    query=protocol,
                    value=current_tvl,
                    unit="USD",
                    raw_data={
                        "name": data.get("name"),
                        "symbol": data.get("symbol"),
                        "chain": data.get("chain"),
                        "tvl": current_tvl,
                    },
                )

        except Exception as e:
            logger.error("defillama_error", protocol=protocol, error=str(e))
            return None

    async def get_chain_tvl(self, chain: str) -> Optional[FactData]:
        """Get TVL for a specific chain.

        Args:
            chain: Chain name (e.g., "ethereum", "bsc").

        Returns:
            FactData with chain TVL.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/v2/chains",
                    timeout=10.0,
                )

                if response.status_code != 200:
                    return None

                chains = response.json()

                # Find matching chain
                chain_lower = chain.lower()
                for chain_data in chains:
                    if chain_data.get("name", "").lower() == chain_lower:
                        return FactData(
                            source=self.name,
                            query=chain,
                            value=chain_data.get("tvl", 0),
                            unit="USD",
                            raw_data=chain_data,
                        )

                return None

        except Exception as e:
            logger.error("defillama_chain_error", chain=chain, error=str(e))
            return None

    async def get_yields(self, protocol: Optional[str] = None) -> list[dict]:
        """Get yield data from DefiLlama.

        Args:
            protocol: Optional protocol filter.

        Returns:
            List of yield pool data.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/pools",
                    timeout=15.0,
                )

                if response.status_code != 200:
                    return []

                data = response.json()
                pools = data.get("data", [])

                if protocol:
                    protocol_lower = protocol.lower()
                    pools = [
                        p for p in pools
                        if p.get("project", "").lower() == protocol_lower
                    ]

                return pools[:100]  # Limit results

        except Exception as e:
            logger.error("defillama_yields_error", error=str(e))
            return []


class CoinGeckoSource(DataSource):
    """CoinGecko API for token price and market data."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    @property
    def name(self) -> str:
        return "coingecko"

    async def query(self, query: str) -> Optional[FactData]:
        """Query CoinGecko for token data."""
        return await self.get_token_price(query)

    async def get_token_price(self, token_id: str) -> Optional[FactData]:
        """Get current price for a token.

        Args:
            token_id: CoinGecko token ID (e.g., "bitcoin", "ethereum").

        Returns:
            FactData with price info.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/simple/price",
                    params={
                        "ids": token_id.lower(),
                        "vs_currencies": "usd",
                        "include_market_cap": "true",
                        "include_24hr_change": "true",
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    return None

                data = response.json()

                if token_id.lower() not in data:
                    # Try searching by name
                    return await self._search_token(token_id)

                token_data = data[token_id.lower()]

                return FactData(
                    source=self.name,
                    query=token_id,
                    value=token_data.get("usd", 0),
                    unit="USD",
                    raw_data={
                        "price_usd": token_data.get("usd"),
                        "market_cap": token_data.get("usd_market_cap"),
                        "change_24h": token_data.get("usd_24h_change"),
                    },
                )

        except Exception as e:
            logger.error("coingecko_price_error", token=token_id, error=str(e))
            return None

    async def _search_token(self, query: str) -> Optional[FactData]:
        """Search for a token by name/symbol.

        Args:
            query: Token name or symbol.

        Returns:
            FactData if found.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/search",
                    params={"query": query},
                    timeout=10.0,
                )

                if response.status_code != 200:
                    return None

                data = response.json()
                coins = data.get("coins", [])

                if not coins:
                    return None

                # Get the top result
                top_coin = coins[0]
                return await self.get_token_price(top_coin["id"])

        except Exception as e:
            logger.error("coingecko_search_error", query=query, error=str(e))
            return None

    async def get_token_info(self, token_id: str) -> Optional[dict]:
        """Get detailed token information.

        Args:
            token_id: CoinGecko token ID.

        Returns:
            Token info dictionary.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/coins/{token_id.lower()}",
                    params={
                        "localization": "false",
                        "tickers": "false",
                        "community_data": "false",
                        "developer_data": "false",
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    return None

                return response.json()

        except Exception as e:
            logger.error("coingecko_info_error", token=token_id, error=str(e))
            return None


class WebSearchSource(DataSource):
    """Web search for additional fact verification.

    Note: This is a placeholder. In production, you would integrate
    with a search API like Google, Bing, or SerpAPI.
    """

    @property
    def name(self) -> str:
        return "web_search"

    async def query(self, query: str) -> Optional[FactData]:
        """Search the web for information.

        Args:
            query: Search query.

        Returns:
            FactData with search results.
        """
        # Placeholder - implement with actual search API
        logger.info("web_search_query", query=query)
        return None
