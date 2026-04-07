"""스케줄러 — 갭 페이딩(롱) 전략 전용

미국장 시간 (한국시간):
  22:00  전일 종가 캐시
  22:30  장 시작 → 갭다운 매수
  22:45~ 15분마다 손절 모니터링
  04:50  전량 매도 (당일 청산 필수)
"""

import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from strategy.us_gap_fade_strategy import USGapFadeStrategy
from trader.risk_manager import RiskManager
from trader.executor import Executor

logger = logging.getLogger(__name__)

DOW = "mon-fri"          # 미국장 개장일 (한국 기준 월~금 밤)
DOW_LATE = "tue-sat"     # 자정 넘긴 후 (한국 기준 화~토 새벽)


class MarketScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.executor = Executor()
        self.risk_manager = RiskManager()
        self.gap_fade = USGapFadeStrategy(self.risk_manager, self.executor)

    def start(self):
        # ─── 1. 전일 종가 캐시 (22:00) ───
        self.scheduler.add_job(
            self._cache_prev_close,
            CronTrigger(hour=22, minute=0, day_of_week=DOW),
            id="gf_cache"
        )

        # ─── 2. 갭다운 매수 (22:30) ───
        self.scheduler.add_job(
            self._entry,
            CronTrigger(hour=22, minute=30, day_of_week=DOW),
            id="gf_entry"
        )

        # ─── 3. 장중 모니터링 (22:45 ~ 04:45, 15분마다) ───
        self.scheduler.add_job(
            self._monitor,
            CronTrigger(hour=22, minute="45", day_of_week=DOW),
            id="gf_mon_2245"
        )
        self.scheduler.add_job(
            self._monitor,
            CronTrigger(hour=23, minute="0,15,30,45", day_of_week=DOW),
            id="gf_mon_23"
        )
        self.scheduler.add_job(
            self._monitor,
            CronTrigger(hour="0-4", minute="0,15,30,45", day_of_week=DOW_LATE),
            id="gf_mon_0_4"
        )

        # ─── 4. 전량 청산 (04:50) ───
        self.scheduler.add_job(
            self._close_all,
            CronTrigger(hour=4, minute=50, day_of_week=DOW_LATE),
            id="gf_close"
        )

        self.scheduler.start()
        logger.info("스케줄러 시작 — 갭 페이딩 전략")
        asyncio.ensure_future(self._startup_catch_up())

    async def shutdown(self):
        self.scheduler.shutdown()
        await self.executor.close()

    # ─── 작업 정의 ───

    async def _cache_prev_close(self):
        logger.info("=== [갭페이드] 전일 종가 캐시 ===")
        await self.gap_fade.cache_prev_close()

    async def _entry(self):
        logger.info("=== [갭페이드] 갭다운 매수 ===")
        await self.gap_fade.execute_entry()

    async def _monitor(self):
        if not self.gap_fade.positions:
            return
        await self.gap_fade.check_exit()

    async def _close_all(self):
        logger.info("=== [갭페이드] 전량 매도 ===")
        await self.gap_fade.close_all()

    async def _startup_catch_up(self):
        """재배포 시 22:00~22:35 사이면 즉시 캐시+매수"""
        await asyncio.sleep(3)
        now = datetime.now()
        h, m = now.hour, now.minute
        wd = now.weekday()

        # 갭다운 매수는 시가 근처에서만 의미 있음 (22:00~22:35)
        if wd <= 4 and h == 22 and m <= 35:
            logger.info("=== [갭페이드] 개장 시간 재시작 — 즉시 스캔 ===")
            await self.gap_fade.cache_prev_close()
            await self.gap_fade.execute_entry()
        else:
            logger.info(f"=== [갭페이드] 재시작 감지 (시각 {h}:{m:02d}) — 개장 시간 아님, 스캔 생략 ===")
