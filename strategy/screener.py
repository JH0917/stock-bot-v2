"""종목 스크리너 — EMA 크로스 + RSI 필터"""

import logging
from collector.market_data import (
    get_kospi200_symbols,
    get_kosdaq150_symbols,
    get_daily_ohlcv,
    get_trade_value,
)
from strategy.indicators import sma, ema, rsi, adx
import config

logger = logging.getLogger(__name__)


def screen_rsi_candidates() -> list[dict]:
    """주전략(RSI(2)) 매수 후보 스크리닝

    필터:
    1. KOSPI200 + KOSDAQ150 종목만
    2. 종가 > 200일 이동평균 (장기 상승 추세)
    3. ADX(14) > 20 (추세 존재)
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


def screen_ema_candidates() -> list[dict]:
    """주전략(EMA 크로스) 매수 후보 스크리닝

    필터:
    1. KOSPI200 + KOSDAQ150 종목
    2. EMA(13) > EMA(21) 골든크로스 (최근 3일 이내 크로스)
    3. RSI(14) > 50
    4. 최소 거래대금, 최소 주가
    """
    symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
    logger.info(f"[EMA] 스크리닝 대상: {len(symbols)}종목")

    candidates = []
    stats = {"data_insufficient": 0, "low_trade_value": 0, "low_price": 0,
             "no_golden_cross": 0, "low_rsi": 0, "passed": 0}

    for sym in symbols:
        data = get_daily_ohlcv(sym, days=50)
        if not data or len(data["closes"]) < config.EMA_LONG + 2:
            stats["data_insufficient"] += 1
            continue

        closes = data["closes"]
        last_close = closes[-1]

        # 필터 0: 최소 거래대금
        if get_trade_value(sym) < config.MIN_TRADE_VALUE:
            stats["low_trade_value"] += 1
            continue

        # 필터 1: 최소 주가
        if last_close < config.MIN_PRICE:
            stats["low_price"] += 1
            continue

        # 필터 2: EMA 골든크로스 (최근 3일 이내 크로스 발생)
        ema_short = ema(closes, config.EMA_SHORT)
        ema_long = ema(closes, config.EMA_LONG)

        # 현재 EMA13 > EMA21이어야 함
        if ema_short[-1] <= ema_long[-1]:
            stats["no_golden_cross"] += 1
            continue

        # 최근 3일 이내에 크로스가 발생했는지 확인
        cross_found = False
        for lookback in range(1, 4):  # [-2], [-3], [-4] 체크
            idx = -(lookback + 1)
            if len(ema_short) >= abs(idx) and ema_short[idx] <= ema_long[idx]:
                cross_found = True
                break

        if not cross_found:
            stats["no_golden_cross"] += 1
            continue

        # 필터 3: RSI(14) > 50
        rsi_values = rsi(closes, config.RSI_PERIOD)
        if rsi_values[-1] <= config.RSI_ENTRY_THRESHOLD:
            stats["low_rsi"] += 1
            continue

        stats["passed"] += 1
        candidates.append({
            "symbol": sym,
            "close": last_close,
            "ema_short": round(ema_short[-1], 2),
            "ema_long": round(ema_long[-1], 2),
            "rsi": round(rsi_values[-1], 2),
        })

    # 필터링 통계 로그
    logger.info(f"[EMA] 필터 통계: 데이터부족={stats['data_insufficient']}, "
                f"거래대금미달={stats['low_trade_value']}, 저가={stats['low_price']}, "
                f"크로스없음={stats['no_golden_cross']}, RSI미달={stats['low_rsi']}, "
                f"통과={stats['passed']}")

    # RSI가 높은 순 (모멘텀 강한 종목 우선)
    candidates.sort(key=lambda x: x["rsi"], reverse=True)
    result = candidates[:config.SCREEN_TOP_N]
    logger.info(f"[EMA] 스크리닝 결과: {len(result)}종목 통과")
    for c in result:
        logger.info(f"  {c['symbol']} | 종가={c['close']:,} | EMA13={c['ema_short']} | EMA21={c['ema_long']} | RSI={c['rsi']}")
    return result


def check_ema_dead_cross(symbol: str) -> bool:
    """EMA 데드크로스 확인 (EMA13 < EMA21)"""
    data = get_daily_ohlcv(symbol, days=50)
    if not data or len(data["closes"]) < config.EMA_LONG + 2:
        return False

    closes = data["closes"]
    ema_short = ema(closes, config.EMA_SHORT)
    ema_long = ema(closes, config.EMA_LONG)

    return ema_short[-1] < ema_long[-1]
