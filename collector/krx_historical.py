"""KRX 과거 시점별 인덱스 구성종목 수집

KOSPI200: 매년 6월/12월 정기변경
KOSDAQ150: 매년 6월/12월 정기변경

pykrx 1.2.8 + KRX_ID/KRX_PW 환경변수 필요
"""

import os
import json
import logging
from pykrx import stock as pykrx

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# 정기변경 직후 영업일 (2022~2026)
# KOSPI200/KOSDAQ150 모두 6월/12월 두 번째 금요일 이후 첫 영업일에 변경
REBALANCE_DATES = [
    "20220613",  # 2022년 6월
    "20221212",  # 2022년 12월
    "20230612",  # 2023년 6월
    "20231211",  # 2023년 12월
    "20240610",  # 2024년 6월
    "20241209",  # 2024년 12월
    "20250609",  # 2025년 6월
    "20251208",  # 2025년 12월
    "20260608",  # 2026년 6월
]

# KRX 인덱스 티커
KOSPI200_TICKER = "1028"
KOSDAQ150_TICKER = "2203"


def fetch_index_constituents(ticker: str, date: str) -> list[str]:
    """특정 날짜의 인덱스 구성종목 조회"""
    try:
        symbols = pykrx.get_index_portfolio_deposit_file(ticker, date, alternative=True)
        if symbols is not None and len(symbols) > 0:
            return list(symbols)
        # alternative=False도 시도
        symbols = pykrx.get_index_portfolio_deposit_file(ticker, date)
        if symbols is not None and len(symbols) > 0:
            return list(symbols)
        return []
    except Exception as e:
        logger.error(f"구성종목 조회 실패 {ticker} @ {date}: {e}")
        return []


def collect_all_historical() -> dict:
    """모든 정기변경 시점의 구성종목 수집

    Returns:
        {
            "20220613": {"kospi200": [...], "kosdaq150": [...]},
            "20221212": {"kospi200": [...], "kosdaq150": [...]},
            ...
        }
    """
    result = {}
    for date in REBALANCE_DATES:
        logger.info(f"구성종목 수집: {date}")
        kospi = fetch_index_constituents(KOSPI200_TICKER, date)
        kosdaq = fetch_index_constituents(KOSDAQ150_TICKER, date)
        if kospi or kosdaq:
            result[date] = {
                "kospi200": kospi,
                "kosdaq150": kosdaq,
            }
            logger.info(f"  KOSPI200: {len(kospi)}종목, KOSDAQ150: {len(kosdaq)}종목")
        else:
            logger.warning(f"  {date} 데이터 없음")
    return result


def save_historical(data: dict, filename: str = "index_constituents.json"):
    """수집 결과를 파일로 저장"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"저장 완료: {path}")
    return path


def load_historical(filename: str = "index_constituents.json") -> dict:
    """저장된 구성종목 데이터 로드"""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_symbols_for_date(date: str, historical: dict = None) -> list[str]:
    """특정 날짜에 유효한 인덱스 구성종목 반환

    해당 날짜 직전의 정기변경 시점 종목을 반환
    """
    if historical is None:
        historical = load_historical()
    if not historical:
        return []

    # date 이전의 가장 최근 리밸런싱 날짜 찾기
    valid_dates = sorted(d for d in historical.keys() if d <= date)
    if not valid_dates:
        # date가 첫 리밸런싱보다 이전이면 첫 번째 데이터 사용
        valid_dates = sorted(historical.keys())

    latest = valid_dates[-1]
    entry = historical[latest]
    return entry.get("kospi200", []) + entry.get("kosdaq150", [])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logger.info("KRX 과거 구성종목 수집 시작")
    data = collect_all_historical()
    if data:
        path = save_historical(data)
        print(f"\n수집 완료: {len(data)}개 시점")
        for date, entry in sorted(data.items()):
            print(f"  {date}: KOSPI200 {len(entry['kospi200'])}종목, "
                  f"KOSDAQ150 {len(entry['kosdaq150'])}종목")
    else:
        print("수집 실패 — KRX_ID/KRX_PW 환경변수를 확인하세요")
