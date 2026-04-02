"""미국 주식 과거 데이터 수집 — yfinance 기반 + 로컬 캐시"""

from __future__ import annotations

import json
import os
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
import yfinance as yf
import config

# Docker 환경에서 TzCache 폴더 충돌 방지
_tz_cache_dir = os.path.join(config.DATA_DIR, "yf_cache")
os.makedirs(_tz_cache_dir, exist_ok=True)
yf.set_tz_cache_location(_tz_cache_dir)

logger = logging.getLogger(__name__)

US_DAILY_DIR = os.path.join(config.DATA_DIR, "us_daily")


def _ensure_dir():
    os.makedirs(US_DAILY_DIR, exist_ok=True)


def _cache_path(symbol: str) -> str:
    return os.path.join(US_DAILY_DIR, f"{symbol}.json")


def get_us_daily_ohlcv(symbol: str, days: int = 120) -> dict:
    """미국 주식 일봉 OHLCV 조회 (캐시 우선, 없으면 yfinance)

    Returns: {dates, opens, highs, lows, closes, volumes} 또는 빈 dict
    """
    _ensure_dir()
    cache = _load_cache(symbol)

    if cache and _is_cache_fresh(cache):
        return _trim_data(cache["data"], days)

    # yfinance에서 다운로드
    data = _download_from_yfinance(symbol, days + 30)
    if not data:
        # 캐시가 있으면 오래되었어도 사용
        if cache:
            return _trim_data(cache["data"], days)
        return {}

    _save_cache(symbol, data)
    return _trim_data(data, days)


def bulk_download(symbols: list[str], days: int = 120, chunk_size: int = 100) -> dict[str, dict]:
    """여러 종목 일봉 데이터 일괄 다운로드

    Returns: {symbol: {dates, opens, highs, lows, closes, volumes}}
    """
    _ensure_dir()
    result = {}
    need_download = []

    # 캐시 확인
    for sym in symbols:
        cache = _load_cache(sym)
        if cache and _is_cache_fresh(cache):
            result[sym] = _trim_data(cache["data"], days)
        else:
            need_download.append(sym)

    logger.info(f"캐시 히트: {len(result)}개, 다운로드 필요: {len(need_download)}개")

    failed_count = 0

    # 청크별로 다운로드 (재시도 포함)
    for i in range(0, len(need_download), chunk_size):
        chunk = need_download[i:i + chunk_size]
        tickers = " ".join(chunk)
        period = f"{days + 30}d"

        df = None
        for attempt in range(3):
            try:
                df = yf.download(tickers, period=period, group_by="ticker",
                                 auto_adjust=True, threads=True, progress=False)
                if df is not None and not df.empty:
                    break
            except Exception as e:
                logger.warning(f"yfinance bulk 시도 {attempt+1}/3 실패: {e}")
            wait = 10 * (2 ** attempt)  # 10초, 20초, 40초 대기
            logger.info(f"yfinance 재시도 대기 {wait}초...")
            time.sleep(wait)

        if df is None or df.empty:
            # 벌크 실패 → 개별 다운로드 폴백 (종목 사이 5초 딜레이)
            logger.warning(f"bulk 실패, 개별 다운로드 전환: {len(chunk)}종목")
            for idx, sym in enumerate(chunk):
                if idx > 0:
                    time.sleep(5)  # 종목 간 5초 딜레이
                data = _download_single_with_retry(sym, days + 30)
                if data:
                    _save_cache(sym, data)
                    result[sym] = _trim_data(data, days)
                else:
                    failed_count += 1
            continue

        for sym in chunk:
            try:
                if len(chunk) == 1:
                    sym_df = df
                else:
                    sym_df = df[sym] if sym in df.columns.get_level_values(0) else None

                if sym_df is None or sym_df.empty:
                    failed_count += 1
                    continue

                sym_df = sym_df.dropna(subset=["Close"])
                if len(sym_df) < 20:
                    failed_count += 1
                    continue

                data = {
                    "dates": sym_df.index.strftime("%Y%m%d").tolist(),
                    "opens": [round(float(x), 2) for x in sym_df["Open"]],
                    "highs": [round(float(x), 2) for x in sym_df["High"]],
                    "lows": [round(float(x), 2) for x in sym_df["Low"]],
                    "closes": [round(float(x), 2) for x in sym_df["Close"]],
                    "volumes": [int(x) for x in sym_df["Volume"]],
                }
                _save_cache(sym, data)
                result[sym] = _trim_data(data, days)
            except Exception as e:
                logger.debug(f"{sym} 파싱 실패: {e}")

    logger.info(f"총 {len(result)}개 종목 데이터 확보 (실패: {failed_count}개)")
    return result


def _download_single_with_retry(symbol: str, days: int, retries: int = 3) -> dict:
    """개별 종목 재시도 다운로드 (rate limit 대응)"""
    for attempt in range(retries):
        data = _download_from_yfinance(symbol, days)
        if data:
            return data
        wait = 10 * (2 ** attempt)  # 10초, 20초, 40초
        logger.info(f"{symbol} 재시도 대기 {wait}초... ({attempt+1}/{retries})")
        time.sleep(wait)
    # 캐시가 있으면 오래되었어도 사용
    cache = _load_cache(symbol)
    if cache:
        logger.info(f"{symbol} 오래된 캐시 사용 (다운로드 {retries}회 실패)")
        return cache["data"]
    return {}


def _download_from_yfinance(symbol: str, days: int) -> dict:
    """단일 종목 yfinance 다운로드"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{days}d", auto_adjust=True)
        if df.empty or len(df) < 20:
            return {}
        return {
            "dates": df.index.strftime("%Y%m%d").tolist(),
            "opens": [round(float(x), 2) for x in df["Open"]],
            "highs": [round(float(x), 2) for x in df["High"]],
            "lows": [round(float(x), 2) for x in df["Low"]],
            "closes": [round(float(x), 2) for x in df["Close"]],
            "volumes": [int(x) for x in df["Volume"]],
        }
    except Exception as e:
        logger.error(f"{symbol} yfinance 다운로드 실패: {e}")
        return {}


def _load_cache(symbol: str) -> Optional[dict]:
    """캐시 파일 로드"""
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None


def _save_cache(symbol: str, data: dict):
    """캐시 파일 저장"""
    cache = {
        "updated": datetime.now().strftime("%Y%m%d %H%M"),
        "data": data,
    }
    with open(_cache_path(symbol), "w") as f:
        json.dump(cache, f)


def _is_cache_fresh(cache: dict, max_hours: int = 20) -> bool:
    """캐시가 max_hours 이내인지"""
    try:
        updated = datetime.strptime(cache["updated"], "%Y%m%d %H%M")
        return (datetime.now() - updated).total_seconds() < max_hours * 3600
    except (KeyError, ValueError):
        return False


async def kis_download(kis_client, symbols: list[str], days: int = 90,
                       exchange_map: dict = None) -> dict[str, dict]:
    """KIS API로 해외주식 일봉 다운로드 (yfinance 폴백용)

    Args:
        kis_client: KISClient 인스턴스
        symbols: 종목 리스트
        days: 필요한 일수
        exchange_map: {symbol: exchange} 매핑 (없으면 NAS 기본)

    Returns: {symbol: {dates, opens, highs, lows, closes, volumes}}
    """
    _ensure_dir()
    result = {}
    exchange_map = exchange_map or {}

    for i, sym in enumerate(symbols):
        if i > 0:
            time.sleep(0.3)  # KIS API 초당 20건 제한 대응

        exchange = exchange_map.get(sym, "NAS")
        try:
            resp = await kis_client.get_us_daily_chart(sym, exchange=exchange)
            if resp.get("rt_cd") != "0":
                logger.warning(f"[KIS] {sym} 일봉 조회 실패: {resp.get('msg1', '')}")
                continue

            output2 = resp.get("output2", [])
            if not output2 or len(output2) < 20:
                logger.warning(f"[KIS] {sym} 데이터 부족: {len(output2)}건")
                continue

            # 최신순 → 오래된순으로 뒤집기
            output2 = list(reversed(output2))

            # 빈 데이터 필터링
            output2 = [r for r in output2 if r.get("clos") and float(r["clos"]) > 0]
            if len(output2) < 20:
                continue

            data = {
                "dates": [r["xymd"] for r in output2],
                "opens": [round(float(r["open"]), 2) for r in output2],
                "highs": [round(float(r["high"]), 2) for r in output2],
                "lows": [round(float(r["low"]), 2) for r in output2],
                "closes": [round(float(r["clos"]), 2) for r in output2],
                "volumes": [int(r["tvol"]) for r in output2],
            }
            _save_cache(sym, data)
            result[sym] = _trim_data(data, days)
            logger.info(f"[KIS] {sym} 일봉 {len(output2)}건 확보")

        except Exception as e:
            logger.error(f"[KIS] {sym} 다운로드 실패: {e}")

    return result


def _trim_data(data: dict, days: int) -> dict:
    """최근 N일만 잘라서 반환"""
    if not data or not data.get("dates"):
        return {}
    n = min(days, len(data["dates"]))
    return {
        "dates": data["dates"][-n:],
        "opens": data["opens"][-n:],
        "highs": data["highs"][-n:],
        "lows": data["lows"][-n:],
        "closes": data["closes"][-n:],
        "volumes": data["volumes"][-n:],
    }
