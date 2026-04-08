"""Stock Bot v2 — EMA 크로스(국장) + 갭 페이딩(미장) 자동매매 봇"""

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
    logger.info("Stock Bot v2 시작 (국장 EMA + 미장 갭페이딩)")
    scheduler.start()
    yield
    logger.info("Stock Bot v2 종료")
    await scheduler.shutdown()


app = FastAPI(title="Stock Bot v2", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/positions")
async def positions():
    return scheduler.risk_manager.get_positions()


@app.get("/report")
async def report():
    return {"report": scheduler.risk_manager.daily_report()}


@app.get("/state")
async def state():
    return scheduler.risk_manager.state
