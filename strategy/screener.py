"""종목 스크리너 — 4중 필터"""

import logging
from collector.market_data import (
    get_kospi200_symbols,
    get_kosdaq150_symbols,
    get_daily_ohlcv,
    get_trade_value,
)
from strategy.indicators import sma, rsi, adx
import config

logger = logging.getLogger(__name__)


def screen_rsi_candidates() -> list[dict]:
    """주전략(RSI(2)) 매수 후보 스크리닝

    필터:
    1. KOSPI200 + KOSDAQ150 종목만
    2. 종가 > 200일 이동평균 (장기 상승 추세)
    3. ADX(14) > 30 (추세 존재)
    4. RSI(2) < 5 (단기 과매도)
    5. 종가 >= 5,000원
    """
    symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
    logger.info(f"스크리닝 대상: {len(symbols)}종목")

    candidates = []

    for sym in symbols:
        data = get_daily_ohlcv(sym, days=250)
        if not data or len(data["closes"]) < 200:
            continue

        closes = data["closes"]
        highs = data["highs"]
        lows = data["lows"]
        last_close = closes[-1]

        # 필터 0: 최소 거래대금
        if get_trade_value(sym) < config.MIN_TRADE_VALUE:
            continue

        # 필터 1: 최소 주가
        if last_close < config.MIN_PRICE:
            continue

        # 필터 2: 종가 > 200일 이동평균
        ma200 = sma(closes, config.MA_LONG)
        if ma200[-1] == 0 or last_close <= ma200[-1]:
            continue

        # 필터 3: ADX(14) > 30
        adx_values = adx(highs, lows, closes, config.ADX_PERIOD)
        if adx_values[-1] < config.ADX_MIN:
            continue

        # 필터 4: RSI(2) < 5
        rsi_values = rsi(closes, config.RSI_PERIOD)
        if rsi_values[-1] >= config.RSI_ENTRY_THRESHOLD:
            continue

        candidates.append({
            "symbol": sym,
            "close": last_close,
            "rsi2": round(rsi_values[-1], 2),
            "adx": round(adx_values[-1], 2),
            "ma200": round(ma200[-1], 2),
        })

    # RSI(2)가 가장 낮은 순으로 정렬 (가장 과매도 종목 우선)
    candidates.sort(key=lambda x: x["rsi2"])
    result = candidates[:config.SCREEN_TOP_N]
    logger.info(f"스크리닝 결과: {len(result)}종목 통과")
    for c in result:
        logger.info(f"  {c['symbol']} | 종가={c['close']:,} | RSI(2)={c['rsi2']} | ADX={c['adx']}")
    return result


def check_rsi_exit(symbol: str) -> bool:
    """RSI(2) > 70 익절 조건 확인"""
    data = get_daily_ohlcv(symbol, days=30)
    if not data or len(data["closes"]) < 5:
        return False

    rsi_values = rsi(data["closes"], config.RSI_PERIOD)
    return rsi_values[-1] > config.RSI_EXIT_THRESHOLD
