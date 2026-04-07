"""Stock Bot v2 — 갭 페이딩(롱) 전략 봇

전략: 코인주/레버리지ETF 3%+ 갭다운 → 시가 매수 → 당일 청산
스케줄: 22:00 캐시 → 22:30 매수 → 15분 모니터 → 04:50 청산
"""

import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from scheduler.market_scheduler import MarketScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

scheduler = MarketScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Stock Bot v2 시작 — 갭 페이딩 전략")
    scheduler.start()
    yield
    logger.info("Stock Bot v2 종료")
    await scheduler.shutdown()


app = FastAPI(title="Stock Bot v2 — Gap Fade", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "strategy": "gap_fade"}


@app.get("/positions")
async def positions():
    return scheduler.gap_fade.get_status()


@app.get("/report")
async def report():
    gf = scheduler.gap_fade
    return {
        "daily_pnl_usd": round(gf.daily_pnl, 2),
        "daily_trades": gf.daily_trades,
        "positions": len(gf.positions),
    }
