"""
Animica Marketplace RPC Methods
================================

RPC methods for ANM token purchasing and treasury management:
  - explorer_getTreasurySnapshot: Get treasury state (sold, balance, revenue)
  - explorer_getMarketData: Get current ANM market price & metrics
  - explorer_getPriceHistory: Get historical price data
  - wallet_getPurchaseHistory: Get user's purchase transactions
  - marketplace_getPricingCurve: Get pricing curve formula & parameters

All methods are deterministic and safe for consensus-critical operations.
"""

from __future__ import annotations

import dataclasses as dc
import typing as t
from datetime import datetime
from decimal import Decimal

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

# Optional imports for treasury state access
try:
    from core.db.state_db import TreasuryAccount  # type: ignore
    from execution.adapters.state_db import StateDB  # type: ignore
except Exception:
    StateDB = None  # type: ignore
    TreasuryAccount = None  # type: ignore


# ============================================================================
# Type Definitions
# ============================================================================


@dc.dataclass(frozen=True)
class TreasurySnapshot:
    """Current treasury state snapshot."""

    totalSupply: float  # Total ANM tokens ever created
    soldToDate: float  # ANM tokens sold from treasury
    treasuryBalance: float  # ANM remaining in treasury
    percentSold: float  # Percentage of supply sold (0-100)
    revenueToDate: float  # USD revenue generated from sales
    lastUpdateBlock: int  # Block height of last update
    timestamp: str  # ISO8601 timestamp
    targetRevenue: float = 1_000_000_000.0  # $1B target
    yearsToTarget: float | None = None  # Estimated years to reach target


@dc.dataclass(frozen=True)
class MarketPriceData:
    """Market price information."""

    price: float  # Current ANM price in USD
    marketCap: float  # Total market cap in USD
    volume24h: float  # 24-hour trading volume in USD
    change24h: float  # 24-hour price change percentage
    change7d: float  # 7-day price change percentage
    high24h: float  # 24-hour high price
    low24h: float  # 24-hour low price
    lastUpdate: str  # ISO8601 timestamp
    source: str  # "coingecko" | "coinmarketcap" | "animica_exchange"


@dc.dataclass(frozen=True)
class PriceHistoryPoint:
    """Single historical price point."""

    timestamp: str  # ISO8601 timestamp
    price: float  # Price in USD at this timestamp
    volume: float | None = None  # Optional volume


@dc.dataclass(frozen=True)
class HistoricalPurchase:
    """User purchase transaction record."""

    id: str  # Transaction ID or order ID
    timestamp: str  # ISO8601 timestamp
    anmQuantity: float  # ANM tokens purchased
    usdAmount: float  # USD amount paid
    pricePerAnm: float  # Price per token at purchase time
    paymentMethod: str  # "credit_card" | "paypal" | "bank_transfer" | "crypto"
    status: str  # "completed" | "pending" | "failed"
    receiptUrl: str | None  # Link to receipt
    transactionHash: str | None  # On-chain transaction hash
    fee: float  # Fee paid
    feePercentage: float  # Fee percentage


@dc.dataclass(frozen=True)
class PurchaseHistoryResult:
    """User's full purchase history."""

    purchases: list[HistoricalPurchase]
    totalPurchases: int
    totalAnmPurchased: float
    totalSpent: float
    averagePrice: float


@dc.dataclass(frozen=True)
class PricingCurveFormula:
    """Pricing formula and parameters."""

    basePrice: float = 1.0  # Base minimum price in USD
    markupPercentage: float = 0.15  # 15% exchange markup
    treasuryMultiplierFormula: str = "1.0 + 2.0 * sqrt(percentSold)"
    treasuryTargetRevenue: float = 1_000_000_000.0  # $1B
    deterministic: bool = True  # Reproducible across clients
    formula: str = "max($1.00, exchangePrice * 1.15) * treasuryMultiplier"


# ============================================================================
# Hardcoded Fallback Data (for development/testing)
# ============================================================================

FALLBACK_TREASURY_SNAPSHOT: TreasurySnapshot = TreasurySnapshot(
    totalSupply=1_000_000_000.0,
    soldToDate=345_000_000.0,
    treasuryBalance=655_000_000.0,
    percentSold=34.5,
    revenueToDate=450_000_000.0,
    lastUpdateBlock=12345678,
    timestamp=datetime.utcnow().isoformat() + "Z",
    yearsToTarget=9.2,
)

FALLBACK_MARKET_PRICE_DATA: MarketPriceData = MarketPriceData(
    price=1.50,
    marketCap=1_500_000_000.0,
    volume24h=45_000_000.0,
    change24h=12.5,
    change7d=35.2,
    high24h=1.55,
    low24h=1.40,
    lastUpdate=datetime.utcnow().isoformat() + "Z",
    source="coingecko",
)

FALLBACK_PRICING_CURVE = PricingCurveFormula()


# ============================================================================
# RPC Methods
# ============================================================================


@method("explorer_getTreasurySnapshot")
async def explorer_get_treasury_snapshot(
    ctx: deps.RpcContext,
) -> dict[str, t.Any]:
    """
    Get current treasury state snapshot.

    Returns:
        {
            "totalSupply": 1000000000.0,
            "soldToDate": 345000000.0,
            "treasuryBalance": 655000000.0,
            "percentSold": 34.5,
            "revenueToDate": 450000000.0,
            "lastUpdateBlock": 12345678,
            "timestamp": "2025-01-08T10:30:45Z",
            "targetRevenue": 1000000000.0,
            "yearsToTarget": 9.2
        }
    """
    try:
        # TODO: Implement actual treasury state fetch from state DB
        # For now, return fallback data
        snapshot = FALLBACK_TREASURY_SNAPSHOT

        return {
            "totalSupply": snapshot.totalSupply,
            "soldToDate": snapshot.soldToDate,
            "treasuryBalance": snapshot.treasuryBalance,
            "percentSold": snapshot.percentSold,
            "revenueToDate": snapshot.revenueToDate,
            "lastUpdateBlock": snapshot.lastUpdateBlock,
            "timestamp": snapshot.timestamp,
            "targetRevenue": snapshot.targetRevenue,
            "yearsToTarget": snapshot.yearsToTarget,
        }
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Failed to fetch treasury snapshot: {e}",
            code=-32000,
        )


@method("explorer_getMarketData")
async def explorer_get_market_data(
    ctx: deps.RpcContext,
    token: str = "ANM",
) -> dict[str, t.Any]:
    """
    Get current market price and metrics for ANM token.

    Params:
        token (str): Token symbol, default "ANM"

    Returns:
        {
            "price": 1.50,
            "marketCap": 1500000000.0,
            "volume24h": 45000000.0,
            "change24h": 12.5,
            "change7d": 35.2,
            "high24h": 1.55,
            "low24h": 1.40,
            "lastUpdate": "2025-01-08T10:30:45Z",
            "source": "coingecko"
        }
    """
    try:
        # TODO: Implement actual market data fetch from external APIs
        # with fallback chain: CoinGecko → CoinMarketCap → internal exchange
        data = FALLBACK_MARKET_PRICE_DATA

        return {
            "price": data.price,
            "marketCap": data.marketCap,
            "volume24h": data.volume24h,
            "change24h": data.change24h,
            "change7d": data.change7d,
            "high24h": data.high24h,
            "low24h": data.low24h,
            "lastUpdate": data.lastUpdate,
            "source": data.source,
        }
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Failed to fetch market data: {e}",
            code=-32000,
        )


@method("explorer_getPriceHistory")
async def explorer_get_price_history(
    ctx: deps.RpcContext,
    token: str = "ANM",
    days: int = 7,
) -> dict[str, t.Any]:
    """
    Get historical price data for ANM token.

    Params:
        token (str): Token symbol, default "ANM"
        days (int): Number of days of history (1, 7, 30, 90), default 7

    Returns:
        {
            "prices": [1.0, 1.01, 1.02, ..., 1.50],
            "timestamps": ["2025-01-01T00:00:00Z", ..., "2025-01-08T00:00:00Z"],
            "period": "7d",
            "currency": "USD"
        }
    """
    if days not in (1, 7, 30, 90):
        days = 7  # Default to 7 days

    try:
        # TODO: Implement actual price history fetch from database
        # Generate mock history for now
        prices = [1.0 + (i * 0.07) for i in range(days)]
        timestamps = [datetime.utcnow().isoformat() + "Z" for _ in range(day)]

        return {
            "prices": prices,
            "timestamps": timestamps,
            "period": f"{days}d",
            "currency": "USD",
        }
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Failed to fetch price history: {e}",
            code=-32000,
        )


@method("wallet_getPurchaseHistory")
async def wallet_get_purchase_history(
    ctx: deps.RpcContext,
    address: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, t.Any]:
    """
    Get user's purchase history from marketplace.

    Params:
        address (str): User's wallet address
        limit (int): Maximum results to return (default 100, max 1000)
        offset (int): Pagination offset (default 0)

    Returns:
        {
            "purchases": [
                {
                    "id": "tx_123",
                    "timestamp": "2025-01-08T10:30:45Z",
                    "anmQuantity": 1000.0,
                    "usdAmount": 1500.0,
                    "pricePerAnm": 1.50,
                    "paymentMethod": "credit_card",
                    "status": "completed",
                    "receiptUrl": "https://...",
                    "transactionHash": "0x...",
                    "fee": 45.0,
                    "feePercentage": 3.0
                }
            ],
            "totalPurchases": 5,
            "totalAnmPurchased": 5000.0,
            "totalSpent": 7500.0,
            "averagePrice": 1.50
        }
    """
    # Validate address format
    if not address or len(address) < 20:
        raise rpc_errors.RpcError(
            "Invalid address format",
            code=-32602,  # Invalid params
        )

    # Validate pagination
    limit = min(max(1, limit), 1000)  # 1-1000
    offset = max(0, offset)

    try:
        # TODO: Implement actual purchase history fetch from database
        # For now, return empty result (user has not purchased)

        return {
            "purchases": [],
            "totalPurchases": 0,
            "totalAnmPurchased": 0.0,
            "totalSpent": 0.0,
            "averagePrice": 0.0,
        }
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Failed to fetch purchase history: {e}",
            code=-32000,
        )


@method("marketplace_getPricingCurve")
async def marketplace_get_pricing_curve(
    ctx: deps.RpcContext,
) -> dict[str, t.Any]:
    """
    Get current pricing curve formula and parameters.

    Used by wallet and explorer to ensure consistent price calculations.

    Returns:
        {
            "basePrice": 1.0,
            "markupPercentage": 0.15,
            "treasuryMultiplierFormula": "1.0 + 2.0 * sqrt(percentSold)",
            "treasuryTargetRevenue": 1000000000.0,
            "deterministic": true,
            "formula": "max($1.00, exchangePrice * 1.15) * treasuryMultiplier"
        }
    """
    try:
        formula = FALLBACK_PRICING_CURVE

        return {
            "basePrice": formula.basePrice,
            "markupPercentage": formula.markupPercentage,
            "treasuryMultiplierFormula": formula.treasuryMultiplierFormula,
            "treasuryTargetRevenue": formula.treasuryTargetRevenue,
            "deterministic": formula.deterministic,
            "formula": formula.formula,
        }
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Failed to fetch pricing curve: {e}",
            code=-32000,
        )


@method("marketplace_calculatePrice")
async def marketplace_calculate_price(
    ctx: deps.RpcContext,
    marketPrice: float,
    percentSold: float,
    basePrice: float = 1.0,
    markupPercentage: float = 0.15,
) -> dict[str, float]:
    """
    Calculate ANM token price given market price and treasury state.

    Deterministic price calculation used by all clients for verification.
    Formula: max(basePrice, marketPrice * (1 + markupPercentage)) * treasuryMultiplier
    where treasuryMultiplier = 1.0 + 2.0 * sqrt(percentSold)

    Params:
        marketPrice (float): Current market price in USD
        percentSold (float): Percentage of treasury sold (0-100)
        basePrice (float): Minimum base price (default $1.00)
        markupPercentage (float): Markup on market price (default 15%)

    Returns:
        {
            "exchangePrice": 1.50,  # market + markup
            "effectivePrice": 2.414,  # with treasury multiplier
            "treasuryMultiplier": 1.609,  # sqrt-based curve
            "breakdownLabel": "max($1.00, $1.50*1.15) * 1.609"
        }
    """
    try:
        # Validate inputs
        if marketPrice < 0 or percentSold < 0 or percentSold > 100:
            raise rpc_errors.RpcError(
                "Invalid market price or percent sold",
                code=-32602,
            )

        # Step 1: Apply markup to market price
        exchange_price = marketPrice * (1.0 + markupPercentage)

        # Step 2: Use minimum base price
        effective_exchange_price = max(basePrice, exchange_price)

        # Step 3: Calculate treasury multiplier
        # multiplier = 1.0 + 2.0 * sqrt(percentSold / 100)
        import math

        treasury_multiplier = 1.0 + 2.0 * math.sqrt(percentSold / 100.0)

        # Step 4: Calculate final price
        final_price = effective_exchange_price * treasury_multiplier

        return {
            "exchangePrice": round(exchange_price, 8),
            "effectivePrice": round(final_price, 8),
            "treasuryMultiplier": round(treasury_multiplier, 8),
            "basePrice": basePrice,
            "markupPercentage": markupPercentage,
            "percentSold": percentSold,
        }
    except TypeError as e:
        raise rpc_errors.RpcError(
            "Invalid parameter type",
            code=-32602,
        )
    except Exception as e:
        raise rpc_errors.RpcError(
            f"Price calculation failed: {e}",
            code=-32000,
        )


# ============================================================================
# Helper Methods (not exposed via RPC, used internally)
# ============================================================================


def get_treasury_snapshot_sync() -> TreasurySnapshot:
    """Synchronous version for non-async code paths."""
    # TODO: Implement actual state fetching
    return FALLBACK_TREASURY_SNAPSHOT


def get_market_price_sync() -> MarketPriceData:
    """Synchronous version for non-async code paths."""
    # TODO: Implement actual market data fetching with retries
    return FALLBACK_MARKET_PRICE_DATA
