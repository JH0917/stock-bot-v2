"""과거 데이터 수집 (pykrx + 네이버 금융)"""

import logging
from datetime import datetime, timedelta
from pykrx import stock as pykrx
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 종목 목록 캐시 (하루 1회만 조회)
_symbol_cache = {"date": "", "kospi": [], "kosdaq": []}


def _fetch_naver_symbols(sosok: int, pages: int) -> list[str]:
    """네이버 금융 시총 상위 종목 조회
    sosok: 0=KOSPI, 1=KOSDAQ
    """
    symbols = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for page in range(1, pages + 1):
        try:
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
            resp = httpx.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(resp.content.decode("euc-kr", errors="replace"), "html.parser")
            for a in soup.select("a.tltle"):
                href = a.get("href", "")
                if "code=" in href:
                    code = href.split("code=")[1][:6]
                    if code.isdigit():
                        symbols.append(code)
        except Exception as e:
            logger.error(f"네이버 금융 종목 조회 실패 (sosok={sosok}, page={page}): {e}")
    return symbols


def _get_cached_symbols(market: str) -> list[str]:
    """캐시된 종목 목록 반환 (하루 1회 갱신)"""
    today = datetime.now().strftime("%Y%m%d")
    key = "kospi" if market == "KOSPI" else "kosdaq"

    if _symbol_cache["date"] != today or not _symbol_cache[key]:
        sosok = 0 if market == "KOSPI" else 1
        pages = 4 if market == "KOSPI" else 3  # 50종목/페이지
        symbols = _fetch_naver_symbols(sosok, pages)
        if symbols:
            _symbol_cache[key] = symbols
            _symbol_cache["date"] = today
            logger.info(f"{market} 종목 목록 갱신: {len(symbols)}종목")

    return _symbol_cache[key]


def get_kospi200_symbols() -> list[str]:
    """KOSPI 시총 상위 200 종목"""
    return _get_cached_symbols("KOSPI")


def get_kosdaq150_symbols() -> list[str]:
    """KOSDAQ 시총 상위 150 종목"""
    return _get_cached_symbols("KOSDAQ")


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


def get_trade_value(symbol: str) -> int:
    """전일 거래대금 조회"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        df = pykrx.get_market_ohlcv(today, today, symbol)
        if df.empty:
            return 0
        # 거래대금 = 종가 * 거래량으로 근사
        if "거래대금" in df.columns:
            return int(df["거래대금"].iloc[-1])
        return int(float(df["종가"].iloc[-1]) * float(df["거래량"].iloc[-1]))
    except Exception:
        return 0
