"""미국 종목 유니버스 관리 — NASDAQ Trader FTP 기반"""

import csv
import io
import json
import os
import logging
from datetime import datetime
import httpx
import config

logger = logging.getLogger(__name__)

UNIVERSE_FILE = os.path.join(config.DATA_DIR, "us_universe.json")
NASDAQ_FTP_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_FTP_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _parse_nasdaq_listed(text: str) -> list[dict]:
    """nasdaqlisted.txt 파싱 (파이프 구분)"""
    symbols = []
    for line in text.strip().split("\n")[1:]:  # 헤더 스킵
        parts = line.split("|")
        if len(parts) < 7:
            continue
        symbol = parts[0].strip()
        name = parts[1].strip()
        is_etf = parts[5].strip() == "Y"
        is_test = parts[6].strip() == "Y"
        if is_test or not symbol or " " in symbol:
            continue
        # 워런트, 우선주 등 제외 (심볼에 특수문자 포함)
        if any(c in symbol for c in ["$", ".", "-"]):
            continue
        symbols.append({
            "symbol": symbol,
            "name": name,
            "exchange": "NAS",
            "is_etf": is_etf,
        })
    return symbols


def _parse_other_listed(text: str) -> list[dict]:
    """otherlisted.txt 파싱 (파이프 구분) — NYSE, AMEX 등"""
    exchange_map = {"N": "NYS", "A": "AMS", "P": "NYS", "Z": "NYS"}
    symbols = []
    for line in text.strip().split("\n")[1:]:
        parts = line.split("|")
        if len(parts) < 7:
            continue
        symbol = parts[7].strip() if len(parts) > 7 else parts[0].strip()  # ACT Symbol
        if not symbol:
            symbol = parts[0].strip()
        name = parts[1].strip()
        exch_code = parts[2].strip()
        is_etf = parts[4].strip() == "Y"
        is_test = parts[6].strip() == "Y"
        if is_test or not symbol or " " in symbol:
            continue
        if any(c in symbol for c in ["$", ".", "-"]):
            continue
        exchange = exchange_map.get(exch_code, "NYS")
        symbols.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "is_etf": is_etf,
        })
    return symbols


async def fetch_us_universe() -> list[dict]:
    """NASDAQ Trader FTP에서 전체 미국 종목 리스트 다운로드"""
    symbols = []
    async with httpx.AsyncClient(timeout=30) as client:
        # NASDAQ 종목
        resp = await client.get(NASDAQ_FTP_URL)
        resp.raise_for_status()
        nasdaq = _parse_nasdaq_listed(resp.text)
        symbols.extend(nasdaq)
        logger.info(f"NASDAQ 종목: {len(nasdaq)}개")

        # NYSE, AMEX 종목
        resp = await client.get(OTHER_FTP_URL)
        resp.raise_for_status()
        other = _parse_other_listed(resp.text)
        symbols.extend(other)
        logger.info(f"기타 거래소 종목: {len(other)}개")

    # 중복 제거
    seen = set()
    unique = []
    for s in symbols:
        if s["symbol"] not in seen:
            seen.add(s["symbol"])
            unique.append(s)

    logger.info(f"전체 미국 종목 유니버스: {len(unique)}개")
    return unique


def save_universe(symbols: list[dict]):
    """유니버스 캐시 저장"""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    data = {
        "updated": datetime.now().strftime("%Y%m%d"),
        "count": len(symbols),
        "symbols": symbols,
    }
    with open(UNIVERSE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info(f"유니버스 저장 완료: {len(symbols)}개 -> {UNIVERSE_FILE}")


def load_universe() -> list[dict]:
    """캐시된 유니버스 로드"""
    if not os.path.exists(UNIVERSE_FILE):
        return []
    with open(UNIVERSE_FILE) as f:
        data = json.load(f)
    logger.info(f"유니버스 로드: {data['count']}개 (갱신일: {data['updated']})")
    return data["symbols"]


def is_universe_stale(max_age_days: int = 7) -> bool:
    """유니버스가 max_age_days 이상 지났으면 True"""
    if not os.path.exists(UNIVERSE_FILE):
        return True
    with open(UNIVERSE_FILE) as f:
        data = json.load(f)
    updated = datetime.strptime(data["updated"], "%Y%m%d")
    return (datetime.now() - updated).days >= max_age_days
