"""Dynamic Pricing & Cost Estimation Engine for HermesKarma.

Reconciles historical and live session costs across frontier cloud models
and local APU hardware (Chunkito Strix Halo 128GB, Beehive SER8).
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import re

_ONE_MILLION = 1_000_000.0


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float = 0.0
    cache_write_per_million: float = 0.0
    tier_threshold_tokens: Optional[int] = None
    input_per_million_above: Optional[float] = None
    output_per_million_above: Optional[float] = None
    cache_read_per_million_above: Optional[float] = None
    is_local: bool = False
    source: str = "official_pricing_snapshot"


@dataclass(frozen=True)
class UsageCostEstimate:
    cost_usd: float
    raw_stored_cost_usd: float
    is_reconciled: bool
    is_local: bool
    pricing_source: str
    rate_card: Optional[ModelPricing] = None


# Known local APU patterns (running on Chunkito / Beehive local hardware at $0.00)
_LOCAL_MODEL_PATTERNS = (
    r"^qwen3-coder-next",
    r"^qwen3\.8-flash-next",
    r"^qwen3\.8-27b",
    r"^qwen3\.5",
    r"^qwen2\.5",
    r"^deepseek-v4:96k",
    r"^hf\.co/",
    r"^gemma4:",
    r"^gpt-oss:",
    r"^flash-next$",
    r"^jagular$",
    r"^tigger$",
)

# Frontier Commercial Rate Cards (USD per 1M tokens)
# Official published rates including prompt caching
_RATE_CARDS: Dict[str, ModelPricing] = {
    # Google Gemini 3.8 & 3.7 Flash
    # Input: $0.75, Output: $3.75, Prompt Cache Read: $0.075 (10% of input)
    "gemini-3.8-flash": ModelPricing(
        input_per_million=0.75,
        output_per_million=3.75,
        cache_read_per_million=0.075,
        cache_write_per_million=0.0,
    ),
    "gemini-3.7-flash": ModelPricing(
        input_per_million=0.75,
        output_per_million=3.75,
        cache_read_per_million=0.075,
        cache_write_per_million=0.0,
    ),
    "gemini-3.6-flash": ModelPricing(
        input_per_million=1.50,
        output_per_million=7.50,
        cache_read_per_million=0.15,
    ),
    "gemini-3.5-flash": ModelPricing(
        input_per_million=1.50,
        output_per_million=9.00,
        cache_read_per_million=0.15,
    ),
    "gemini-3.5-flash-lite": ModelPricing(
        input_per_million=0.30,
        output_per_million=2.50,
        cache_read_per_million=0.03,
    ),
    "gemini-3.1-pro": ModelPricing(
        input_per_million=2.00,
        output_per_million=12.00,
        cache_read_per_million=0.20,
        tier_threshold_tokens=200_000,
        input_per_million_above=4.00,
        output_per_million_above=18.00,
        cache_read_per_million_above=0.40,
    ),
    "gemini-3.1-pro-preview": ModelPricing(
        input_per_million=2.00,
        output_per_million=12.00,
        cache_read_per_million=0.20,
        tier_threshold_tokens=200_000,
        input_per_million_above=4.00,
        output_per_million_above=18.00,
        cache_read_per_million_above=0.40,
    ),
    "gemini-3-pro-preview": ModelPricing(
        input_per_million=2.00,
        output_per_million=12.00,
        cache_read_per_million=0.20,
    ),
    "gemini-3-flash-preview": ModelPricing(
        input_per_million=0.50,
        output_per_million=3.00,
        cache_read_per_million=0.05,
    ),
    "gemini-2.5-flash": ModelPricing(
        input_per_million=0.15,
        output_per_million=0.60,
        cache_read_per_million=0.015,
    ),
    "gemini-2.0-flash": ModelPricing(
        input_per_million=0.10,
        output_per_million=0.40,
        cache_read_per_million=0.01,
    ),

    # Anthropic Claude
    "claude-opus-4-8": ModelPricing(
        input_per_million=5.00,
        output_per_million=25.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "claude-opus-4-7": ModelPricing(
        input_per_million=5.00,
        output_per_million=25.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "claude-opus-4-6": ModelPricing(
        input_per_million=5.00,
        output_per_million=25.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "claude-opus-4-5": ModelPricing(
        input_per_million=5.00,
        output_per_million=25.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "claude-opus-5": ModelPricing(
        input_per_million=5.00,
        output_per_million=25.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input_per_million=3.00,
        output_per_million=15.00,
        cache_read_per_million=0.30,
        cache_write_per_million=3.75,
    ),
    "claude-sonnet-4-5": ModelPricing(
        input_per_million=3.00,
        output_per_million=15.00,
        cache_read_per_million=0.30,
        cache_write_per_million=3.75,
    ),
    "claude-sonnet-5": ModelPricing(
        input_per_million=3.00,
        output_per_million=15.00,
        cache_read_per_million=0.30,
        cache_write_per_million=3.75,
    ),
    "claude-haiku-4-5": ModelPricing(
        input_per_million=1.00,
        output_per_million=5.00,
        cache_read_per_million=0.10,
        cache_write_per_million=1.25,
    ),

    # OpenAI GPT
    "gpt-5.6-sol": ModelPricing(
        input_per_million=5.00,
        output_per_million=30.00,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
    "gpt-5.6-terra": ModelPricing(
        input_per_million=2.50,
        output_per_million=15.00,
        cache_read_per_million=0.25,
        cache_write_per_million=3.125,
    ),
    "gpt-5.6-luna": ModelPricing(
        input_per_million=1.00,
        output_per_million=6.00,
        cache_read_per_million=0.10,
        cache_write_per_million=1.25,
    ),
    "gpt-4o": ModelPricing(
        input_per_million=2.50,
        output_per_million=10.00,
        cache_read_per_million=1.25,
    ),
    "gpt-4o-mini": ModelPricing(
        input_per_million=0.15,
        output_per_million=0.60,
        cache_read_per_million=0.075,
    ),

    # DeepSeek Cloud API
    "deepseek-flash": ModelPricing(
        input_per_million=0.15,
        output_per_million=0.60,
        cache_read_per_million=0.003,
    ),
    "deepseek-v4-flash": ModelPricing(
        input_per_million=0.15,
        output_per_million=0.60,
        cache_read_per_million=0.003,
    ),
    "deepseek-v4-pro": ModelPricing(
        input_per_million=0.66,
        output_per_million=1.98,
        cache_read_per_million=0.022,
    ),
}


class PricingEngine:
    """Pricing calculation and reconciliation engine."""

    def __init__(self, custom_rate_cards: Optional[Dict[str, ModelPricing]] = None):
        self.rate_cards: Dict[str, ModelPricing] = dict(_RATE_CARDS)
        if custom_rate_cards:
            self.rate_cards.update(custom_rate_cards)

    def is_local_model(self, model_name: str, billing_provider: str = "") -> bool:
        """Return True if model runs on local APU/hardware at $0.00."""
        provider = (billing_provider or "").lower().strip()
        if provider in ("local", "ollama", "chunkito", "beehive", "vllm_local"):
            return True
        if provider in ("gemini", "google", "anthropic", "openai", "openrouter", "deepseek"):
            return False

        m = (model_name or "").lower().strip()
        for pat in _LOCAL_MODEL_PATTERNS:
            if re.search(pat, m, re.IGNORECASE):
                return True
        return False

    def normalize_model_name(self, model_name: str) -> str:
        """Strip provider prefixes (google/, anthropic/, openai/) and trailing qualifiers."""
        m = (model_name or "").lower().strip()
        if "/" in m and not m.startswith("hf.co/"):
            m = m.split("/", 1)[1]
        return m

    def get_model_pricing(self, model_name: str, billing_provider: str = "") -> Optional[ModelPricing]:
        """Retrieve rate card for a given model."""
        if self.is_local_model(model_name, billing_provider):
            return ModelPricing(
                input_per_million=0.0,
                output_per_million=0.0,
                is_local=True,
                source="local_apu_zero_cost",
            )

        norm_name = self.normalize_model_name(model_name)
        if norm_name in self.rate_cards:
            return self.rate_cards[norm_name]

        # Prefix / partial match
        for key, card in self.rate_cards.items():
            if norm_name.startswith(key) or key.startswith(norm_name):
                return card

        return None

    def calculate_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        billing_provider: str = "",
    ) -> float:
        """Calculate estimated USD cost given token counts and model pricing."""
        if self.is_local_model(model_name, billing_provider):
            return 0.0

        card = self.get_model_pricing(model_name, billing_provider)
        if not card:
            return 0.0

        prompt_total = input_tokens + cache_read_tokens + cache_write_tokens
        is_above_tier = (
            card.tier_threshold_tokens is not None
            and prompt_total > card.tier_threshold_tokens
        )

        inp_rate = card.input_per_million_above if is_above_tier and card.input_per_million_above is not None else card.input_per_million
        out_rate = card.output_per_million_above if is_above_tier and card.output_per_million_above is not None else card.output_per_million
        cread_rate = card.cache_read_per_million_above if is_above_tier and card.cache_read_per_million_above is not None else card.cache_read_per_million
        cwrite_rate = card.cache_write_per_million

        cost = (
            (input_tokens * inp_rate)
            + (output_tokens * out_rate)
            + (cache_read_tokens * cread_rate)
            + (cache_write_tokens * cwrite_rate)
        ) / _ONE_MILLION

        return round(cost, 6)

    def reconcile_usage(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        stored_cost_usd: float = 0.0,
        billing_provider: str = "",
    ) -> UsageCostEstimate:
        """Reconcile stored cost against token volume.
        
        If stored_cost_usd <= 0.0001 and tokens exist on a non-local model,
        or if stored_cost is severely undercounting due to unrecorded cache reads,
        reconcile with real model pricing.
        """
        raw_cost = float(stored_cost_usd or 0.0)
        is_local = self.is_local_model(model_name, billing_provider)
        if is_local:
            return UsageCostEstimate(
                cost_usd=0.0,
                raw_stored_cost_usd=raw_cost,
                is_reconciled=False,
                is_local=True,
                pricing_source="local_apu",
            )

        card = self.get_model_pricing(model_name, billing_provider)
        calc_cost = self.calculate_cost(
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            billing_provider=billing_provider,
        )

        total_tokens = input_tokens + output_tokens + cache_read_tokens

        # Reconcile if:
        # 1. Stored cost is practically 0 but tokens exist.
        # 2. Or stored cost is undercounting real API spend (e.g. unrecorded cache reads or unpriced historical calls).
        needs_reconciliation = False
        if total_tokens > 0 and card is not None:
            if raw_cost <= 0.0001:
                needs_reconciliation = True
            elif calc_cost > 1.0 and calc_cost > (raw_cost * 1.05):
                # Historical WAL missed cache reads or had unpriced sessions
                needs_reconciliation = True

        final_cost = calc_cost if needs_reconciliation else raw_cost

        return UsageCostEstimate(
            cost_usd=round(final_cost, 6),
            raw_stored_cost_usd=raw_cost,
            is_reconciled=needs_reconciliation,
            is_local=False,
            pricing_source=card.source if card else "unknown",
            rate_card=card,
        )


# Global singleton
pricing_engine = PricingEngine()
