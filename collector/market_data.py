"""과거 데이터 수집 (pykrx + KRX 직접 조회)"""

import logging
import io
from datetime import datetime, timedelta
from pykrx import stock as pykrx
import httpx

logger = logging.getLogger(__name__)

# KRX 종목 목록 캐시 (하루 1회만 조회)
_symbol_cache = {"date": "", "kospi": [], "kosdaq": []}


def _fetch_krx_symbols(market: str) -> list[str]:
    """KRX에서 종목 코드 직접 조회 (KOSPI/KOSDAQ)"""
    try:
        # KRX 정보데이터시스템 API
        url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        mkt_id = "STK" if market == "KOSPI" else "KSQ"
        resp = httpx.post(url, data={
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
            "mktId": mkt_id,
            "share": "1",
        }, timeout=15)
        data = resp.json()
        rows = data.get("OutBlock_1", [])
        # 종목코드 추출 (보통주만, 우선주/ETF/ETN 제외)
        symbols = []
        for row in rows:
            code = row.get("ISU_SRT_CD", "")
            name = row.get("ISU_ABBRV", "")
            # 6자리 숫자 코드만 (ETF/ETN 제외)
            if len(code) == 6 and code.isdigit():
                symbols.append(code)
        return symbols
    except Exception as e:
        logger.error(f"KRX {market} 종목 조회 실패: {e}")
        return []


def _get_cached_symbols(market: str) -> list[str]:
    """캐시된 종목 목록 반환 (하루 1회 갱신)"""
    today = datetime.now().strftime("%Y%m%d")
    key = "kospi" if market == "KOSPI" else "kosdaq"

    if _symbol_cache["date"] != today or not _symbol_cache[key]:
        symbols = _fetch_krx_symbols(market)
        if symbols:
            _symbol_cache[key] = symbols
            _symbol_cache["date"] = today
            logger.info(f"{market} 종목 목록 갱신: {len(symbols)}종목")

    return _symbol_cache[key]


def get_kospi200_symbols() -> list[str]:
    """KOSPI 상위 200 종목 코드 리스트"""
    all_symbols = _get_cached_symbols("KOSPI")
    # 시총 상위 200개 근사 (KRX 전체 목록에서 앞쪽이 대형주)
    return all_symbols[:200] if len(all_symbols) > 200 else all_symbols


def get_kosdaq150_symbols() -> list[str]:
    """KOSDAQ 상위 150 종목 코드 리스트"""
    all_symbols = _get_cached_symbols("KOSDAQ")
    return all_symbols[:150] if len(all_symbols) > 150 else all_symbols


def get_daily_ohlcv(symbol: str, days: int = 250) -> dict:
    """일봉 OHLCV 데이터 (pykrx)
    Returns: {dates, opens, highs, lows, closes, volumes}
    """
    end = datetime.now()
    start = end - timedelta(days=days + 50)  # 영업일 보정
    try:
        df = pykrx.get_market_ohlcv(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            symbol,
        )
        if df.empty:
            return {}
        return {
            "dates": df.index.strftime("%Y%m%d").tolist(),
            "opens": df["시가"].tolist(),
            "highs": df["고가"].tolist(),
            "lows": df["저가"].tolist(),
            "closes": df["종가"].tolist(),
            "volumes": df["거래량"].tolist(),
        }
    except Exception as e:
        logger.error(f"{symbol} 일봉 조회 실패: {e}")
        return {}


def get_market_cap(symbol: str) -> int:
    """시가총액 조회"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        df = pykrx.get_market_cap(today, today, symbol)
        if df.empty:
            return 0
        return int(df["시가총액"].iloc[-1])
    except Exception:
        return 0


def get_trade_value(symbol: str) -> int:
    """전일 거래대금 조회"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        df = pykrx.get_market_ohlcv(today, today, symbol)
        if df.empty:
            return 0
        # pykrx 버전에 따라 '거래대금' 컬럼이 없을 수 있음
        if "거래대금" in df.columns:
            return int(df["거래대금"].iloc[-1])
        # 거래대금 = 종가 * 거래량으로 근사
        return int(df["종가"].iloc[-1] * df["거래량"].iloc[-1])
    except Exception:
        return 0
