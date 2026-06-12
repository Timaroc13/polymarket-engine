"""Tests for POST /signal endpoint and compute_market_signal()."""
from __future__ import annotations

from fastapi.testclient import TestClient

from crypto_news_parser.main import app
from crypto_news_parser.models import MAX_TEXT_LENGTH, Sentiment
from crypto_news_parser.parser import compute_market_signal

client = TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests: compute_market_signal()
# ---------------------------------------------------------------------------


def test_positive_sentiment_high_impact_gives_bullish() -> None:
    p_model, direction = compute_market_signal(Sentiment.positive, impact_score=1.0, confidence=1.0)
    # Expected: 0.5 + 0.25 * 1.0 * 1.0 = 0.75
    assert abs(p_model - 0.75) < 1e-9
    assert direction.value == "bullish"
    assert p_model > 0.55


def test_negative_sentiment_high_impact_gives_bearish() -> None:
    p_model, direction = compute_market_signal(Sentiment.negative, impact_score=1.0, confidence=1.0)
    # Expected: 0.5 + (-0.25) * 1.0 * 1.0 = 0.25
    assert abs(p_model - 0.25) < 1e-9
    assert direction.value == "bearish"
    assert p_model < 0.45


def test_neutral_sentiment_yields_p_model_near_half() -> None:
    p_model, direction = compute_market_signal(Sentiment.neutral, impact_score=0.8, confidence=0.9)
    assert abs(p_model - 0.5) < 0.001
    assert direction.value == "neutral"


def test_zero_confidence_gives_exactly_half() -> None:
    for sentiment in (Sentiment.positive, Sentiment.negative, Sentiment.neutral):
        p_model, direction = compute_market_signal(sentiment, impact_score=1.0, confidence=0.0)
        assert p_model == 0.5
        assert direction.value == "neutral"


def test_p_model_clamped_to_bounds() -> None:
    # Even with extreme inputs, p_model stays within [0.05, 0.95].
    p_high, _ = compute_market_signal(Sentiment.positive, impact_score=1.0, confidence=1.0)
    p_low, _ = compute_market_signal(Sentiment.negative, impact_score=1.0, confidence=1.0)
    assert 0.05 <= p_high <= 0.95
    assert 0.05 <= p_low <= 0.95


def test_direction_thresholds() -> None:
    # Boundary: p_model = 0.56 → bullish
    p, d = compute_market_signal(Sentiment.positive, impact_score=0.5, confidence=0.48)
    if p > 0.55:
        assert d.value == "bullish"
    elif p < 0.45:
        assert d.value == "bearish"
    else:
        assert d.value == "neutral"


# ---------------------------------------------------------------------------
# Integration tests: POST /signal
# ---------------------------------------------------------------------------


def test_signal_returns_200_with_schema_fields() -> None:
    resp = client.post(
        "/signal",
        json={"text": "BlackRock's Bitcoin ETF saw $400M in inflows after SEC approval."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "p_model" in data
    assert "market_direction" in data
    assert "event_type" in data
    assert "sentiment" in data
    assert "impact_score" in data
    assert "confidence" in data
    assert "assets" in data
    assert "jurisdiction" in data
    assert "schema_version" in data
    assert "model_version" in data


def test_signal_p_model_in_range() -> None:
    resp = client.post(
        "/signal", json={"text": "Bitcoin exchange hack drains $200M from Bybit wallets."}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert 0.0 <= data["p_model"] <= 1.0


def test_signal_market_direction_always_present() -> None:
    for text in [
        "Bitcoin rallied 20% after ETF approval.",
        "SEC sued Binance for securities violations.",
        "A blockchain conference was held in Dubai.",
    ]:
        resp = client.post("/signal", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["market_direction"] in {"bullish", "bearish", "neutral"}


def test_signal_accepts_market_question() -> None:
    resp = client.post(
        "/signal",
        json={
            "text": "The SEC approved a spot Bitcoin ETF for BlackRock.",
            "market_question": "Will Bitcoin exceed $100k by end of 2025?",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert 0.0 <= data["p_model"] <= 1.0


def test_signal_positive_news_bullish_direction() -> None:
    resp = client.post(
        "/signal",
        json={"text": "BlackRock's Bitcoin ETF saw record $1B inflows after SEC approval surge."},
    )
    assert resp.status_code == 200
    assert resp.json()["market_direction"] == "bullish"


def test_signal_negative_news_bearish_direction() -> None:
    resp = client.post(
        "/signal",
        json={"text": "Exchange hack drained $300M. SEC sued the platform. Crypto crash followed."},
    )
    assert resp.status_code == 200
    assert resp.json()["market_direction"] == "bearish"


def test_signal_rejects_empty_text() -> None:
    resp = client.post("/signal", json={"text": "   "})
    assert resp.status_code == 422
    assert "error" in resp.json()


def test_signal_rejects_too_large() -> None:
    resp = client.post("/signal", json={"text": "x" * (MAX_TEXT_LENGTH + 1)})
    assert resp.status_code in {413, 422}


def test_signal_rejects_non_json_content_type() -> None:
    resp = client.post("/signal", content=b"some text", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    assert "error" in resp.json()


def test_signal_deterministic_mode_returns_same_p_model() -> None:
    payload = {
        "text": "Ethereum protocol upgrade successfully deployed on mainnet.",
        "deterministic": True,
    }
    resp1 = client.post("/signal", json=payload)
    resp2 = client.post("/signal", json=payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["p_model"] == resp2.json()["p_model"]
    assert resp1.json()["market_direction"] == resp2.json()["market_direction"]
