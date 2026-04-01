"""과거 데이터 수집 (pykrx + FinanceDataReader)"""

import logging
from datetime import datetime, timedelta
from pykrx import stock as pykrx
import FinanceDataReader as fdr

logger = logging.getLogger(__name__)


def get_kospi200_symbols() -> list[str]:
    """KOSPI 200 구성종목 코드 리스트"""
    try:
        df = fdr.StockListing("KOSPI")
        # KOSPI 200은 시총 상위 대형주이므로 시총 기준 상위 200개로 근사
        df = df.nlargest(200, "Marcap") if "Marcap" in df.columns else df.head(200)
        return df["Code"].tolist()
    except Exception as e:
        logger.error(f"KOSPI 200 목록 조회 실패: {e}")
        return []


def get_kosdaq150_symbols() -> list[str]:
    """KOSDAQ 150 구성종목 코드 리스트"""
    try:
        df = fdr.StockListing("KOSDAQ")
        df = df.nlargest(150, "Marcap") if "Marcap" in df.columns else df.head(150)
        return df["Code"].tolist()
    except Exception as e:
        logger.error(f"KOSDAQ 150 목록 조회 실패: {e}")
        return []


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
        return int(df["거래대금"].iloc[-1]) if "거래대금" in df.columns else 0
    except Exception:
        return 0
