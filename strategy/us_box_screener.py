"""미국 박스권 종목 스크리너 — 전체 시장 스캔 파이프라인

스캔 로직:
1. 유니버스 (~2,000개) → yfinance 60~90일 일봉
2. ADX(14) < 20 (횡보 확인)
3. 박스폭 5~15%
4. 지지/저항 각 2회+ 터치
5. Buy Zone (하단 25%) 필터
6. 반등 확인 시그널 (3개 중 2개)
"""

import logging
from strategy.indicators import (
    adx, atr, rsi, bollinger_bandwidth,
    find_support_resistance, box_position_pct,
)
import config

logger = logging.getLogger(__name__)


def scan_box_candidates(universe: list[dict], daily_data: dict[str, dict]) -> list[dict]:
    """박스권 종목 스캔 전체 파이프라인

    Args:
        universe: [{symbol, name, exchange, is_etf}]
        daily_data: {symbol: {dates, opens, highs, lows, closes, volumes}}

    Returns: 박스권 Buy Zone 종목 리스트 (스코어 내림차순)
    """
    stage1 = _filter_box_range(universe, daily_data)
    logger.info(f"[스크리너] 1단계 박스권 필터 통과: {len(stage1)}종목")

    stage2 = _filter_buy_zone(stage1)
    logger.info(f"[스크리너] 2단계 Buy Zone 필터 통과: {len(stage2)}종목")

    stage3 = _filter_bounce_signal(stage2, daily_data)
    logger.info(f"[스크리너] 3단계 반등 시그널 통과: {len(stage3)}종목")

    # 스코어 내림차순 (시그널 수 많고, 박스 위치가 낮을수록 우선)
    stage3.sort(key=lambda x: (-x["signal_count"], x["box_pct"]))

    for c in stage3[:20]:
        logger.info(
            f"  {c['symbol']:6s} | 지지=${c['support']:.2f} 저항=${c['resistance']:.2f} "
            f"| 박스폭={c['box_width_pct']:.1f}% | 위치={c['box_pct']:.0f}% "
            f"| 시그널={c['signal_count']}/3 | ATR=${c['atr']:.2f}"
        )

    return stage3


def _filter_box_range(universe: list[dict], daily_data: dict[str, dict]) -> list[dict]:
    """1단계: 박스권 종목 필터"""
    candidates = []

    for item in universe:
        symbol = item["symbol"]
        data = daily_data.get(symbol)
        if not data or len(data.get("closes", [])) < config.US_BOX_LOOKBACK_DAYS:
            continue

        closes = data["closes"]
        highs = data["highs"]
        lows = data["lows"]
        volumes = data["volumes"]
        last_close = closes[-1]

        # 기본 필터: 최소 가격, 최소 거래량
        if last_close < config.US_MIN_PRICE:
            continue
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
        if avg_vol < config.US_MIN_AVG_VOLUME:
            continue

        # ADX(14) < 20 — 횡보 확인
        adx_values = adx(highs, lows, closes, config.ADX_PERIOD)
        last_adx = adx_values[-1]
        if last_adx <= 0 or last_adx >= config.US_BOX_ADX_MAX:
            continue

        # 지지/저항 계산 (lookback 기간만)
        lb = config.US_BOX_LOOKBACK_DAYS
        sr = find_support_resistance(
            highs[-lb:], lows[-lb:], closes[-lb:],
            tolerance=config.US_BOX_TOUCH_TOLERANCE,
        )
        if not sr:
            continue

        # 박스폭 5~15%
        if sr["box_width_pct"] < config.US_BOX_MIN_WIDTH_PCT:
            continue
        if sr["box_width_pct"] > config.US_BOX_MAX_WIDTH_PCT:
            continue

        # 지지/저항 각 2회+ 터치
        if sr["support_touches"] < config.US_BOX_MIN_TOUCHES:
            continue
        if sr["resistance_touches"] < config.US_BOX_MIN_TOUCHES:
            continue

        # ATR 계산
        atr_values = atr(highs, lows, closes, 14)
        last_atr = atr_values[-1]

        candidates.append({
            "symbol": symbol,
            "name": item.get("name", ""),
            "exchange": item.get("exchange", "NAS"),
            "close": last_close,
            "support": sr["support"],
            "resistance": sr["resistance"],
            "support_touches": sr["support_touches"],
            "resistance_touches": sr["resistance_touches"],
            "box_width_pct": sr["box_width_pct"],
            "box_pct": box_position_pct(last_close, sr["support"], sr["resistance"]),
            "adx": round(last_adx, 2),
            "atr": round(last_atr, 2),
        })

    return candidates


def _filter_buy_zone(candidates: list[dict]) -> list[dict]:
    """2단계: Buy Zone (하단 25%) 필터"""
    return [c for c in candidates if c["box_pct"] <= config.US_BOX_BUY_ZONE_PCT]


def _filter_bounce_signal(candidates: list[dict], daily_data: dict[str, dict]) -> list[dict]:
    """3단계: 반등 확인 시그널 (3개 중 2개 이상)

    1. 지지선 근처 양봉 (종가 > 시가)
    2. 반등일 거래량 >= 20일 평균 × 1.2
    3. RSI(14) 30~40에서 상승 전환
    """
    result = []

    for c in candidates:
        data = daily_data.get(c["symbol"])
        if not data or len(data["closes"]) < 21:
            continue

        closes = data["closes"]
        opens = data["opens"]
        volumes = data["volumes"]
        signals = 0

        # 시그널 1: 최근 양봉 (종가 > 시가)
        if closes[-1] > opens[-1]:
            signals += 1

        # 시그널 2: 거래량 증가 (최근 > 20일 평균 × 1.2)
        avg_vol = sum(volumes[-21:-1]) / 20
        if avg_vol > 0 and volumes[-1] >= avg_vol * 1.2:
            signals += 1

        # 시그널 3: RSI(14) 30~40에서 상승 전환
        rsi_values = rsi(closes, 14)
        if len(rsi_values) >= 2:
            current_rsi = rsi_values[-1]
            prev_rsi = rsi_values[-2]
            if 25 <= current_rsi <= 45 and current_rsi > prev_rsi:
                signals += 1

        c["signal_count"] = signals
        if signals >= config.US_BOX_SIGNAL_MIN:
            result.append(c)

    return result
