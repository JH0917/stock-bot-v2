"""장 스케줄러 — 국내장(EMA) + 미국장(갭 페이딩) 통합

국내장 (09:00~15:40):
  09:05  EMA 골든크로스 스캔 + 매수
  09:10~ 매분 모니터링 (손절/익절/추적손절)
  15:35  데드크로스 체크
  15:40  일일 리포트

미국장 (22:00~04:50, 한국시간):
  22:00  전일 종가 캐시
  22:30  갭다운 매수
  22:45~ 15분마다 손절 모니터링
  04:50  전량 청산
"""

import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from strategy.ema_strategy import EMAStrategy
from strategy.us_gap_fade_strategy import USGapFadeStrategy
from trader.risk_manager import RiskManager
from trader.executor import Executor
import config

logger = logging.getLogger(__name__)

DOW = "mon-fri"
DOW_LATE = "tue-sat"


class MarketScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.executor = Executor()
        self.risk_manager = RiskManager()
        self.ema_strategy = EMAStrategy(self.risk_manager, self.executor)
        self.gap_fade = USGapFadeStrategy(self.risk_manager, self.executor)

    def start(self):
        # ═══ 국내장: EMA 크로스 ═══
        self.scheduler.add_job(self._ema_entry, CronTrigger(hour=9, minute=5, day_of_week=DOW), id="ema_entry")
        self.scheduler.add_job(self._ema_dead_cross_check, CronTrigger(hour=15, minute=35, day_of_week=DOW), id="ema_dead_cross")

        # ═══ 국내장: 장중 모니터링 (1분마다, 09:10~15:20) ═══
        self.scheduler.add_job(self._monitor_kr, CronTrigger(hour=9, minute="10-59", second=0, day_of_week=DOW), id="monitor_09")
        self.scheduler.add_job(self._monitor_kr, CronTrigger(hour="10-14", minute="*", second=0, day_of_week=DOW), id="monitor_10_14")
        self.scheduler.add_job(self._monitor_kr, CronTrigger(hour=15, minute="0-20", second=0, day_of_week=DOW), id="monitor_15")

        # ═══ 국내장: 장 마감 ═══
        self.scheduler.add_job(self._daily_report, CronTrigger(hour=15, minute=40, day_of_week=DOW), id="daily_report")

        # ═══ 미국장: 갭 페이딩 ═══
        self.scheduler.add_job(self._gf_cache, CronTrigger(hour=22, minute=0, day_of_week=DOW), id="gf_cache")
        self.scheduler.add_job(self._gf_entry, CronTrigger(hour=22, minute=30, day_of_week=DOW), id="gf_entry")
        self.scheduler.add_job(self._gf_monitor, CronTrigger(hour=22, minute="45", day_of_week=DOW), id="gf_mon_2245")
        self.scheduler.add_job(self._gf_monitor, CronTrigger(hour=23, minute="0,15,30,45", day_of_week=DOW), id="gf_mon_23")
        self.scheduler.add_job(self._gf_monitor, CronTrigger(hour="0-4", minute="0,15,30,45", day_of_week=DOW_LATE), id="gf_mon_0_4")
        self.scheduler.add_job(self._gf_close, CronTrigger(hour=4, minute=50, day_of_week=DOW_LATE), id="gf_close")

        self.scheduler.start()
        logger.info("스케줄러 시작 (국내장 EMA + 미국장 갭페이딩)")
        asyncio.ensure_future(self._startup_catch_up())

    async def shutdown(self):
        self.scheduler.shutdown()
        await self.executor.close()

    # ─── 국내장: EMA 전략 ───

    async def _ema_entry(self):
        """09:05 — EMA 크로스 스캔 + 매수 실행 + 데드크로스 매도"""
        logger.info("=== [EMA] 매수/매도 실행 ===")
        await self.ema_strategy.execute_dead_cross_exit()
        candidates = await self.ema_strategy.scan_entry()
        if candidates:
            await self.ema_strategy.execute_entry(candidates)

    async def _ema_dead_cross_check(self):
        """15:35 — 데드크로스 체크 (다음날 매도 예정)"""
        logger.info("=== [EMA] 데드크로스 체크 ===")
        await self.ema_strategy.check_dead_cross_exit()

    async def _monitor_kr(self):
        """1분마다 — 국내 포지션 손절/익절 체크"""
        if not self.risk_manager.get_positions(strategy="ema"):
            return
        await self.ema_strategy.check_exit()

    async def _daily_report(self):
        report = self.risk_manager.daily_report()
        logger.info(f"\n{report}")

    # ─── 미국장: 갭 페이딩 ───

    async def _gf_cache(self):
        logger.info("=== [갭페이드] 전일 종가 캐시 ===")
        await self.gap_fade.cache_prev_close()

    async def _gf_entry(self):
        logger.info("=== [갭페이드] 갭다운 매수 ===")
        await self.gap_fade.execute_entry()

    async def _gf_monitor(self):
        if not self.gap_fade.positions:
            return
        await self.gap_fade.check_exit()

    async def _gf_close(self):
        logger.info("=== [갭페이드] 전량 매도 ===")
        await self.gap_fade.close_all()

    # ─── 공통 ───

    async def _startup_catch_up(self):
        """시작 시 장중이면 즉시 스캔 (재배포 대응)"""
        await asyncio.sleep(3)
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()

        # 국내장 시간: 09:05~15:20 (월~금)
        if weekday <= 4 and 9 <= hour < 15:
            logger.info("=== [EMA] 장중 재시작 감지 — 포지션 모니터링 재개 ===")

        # 미국장 시간: 22:00~04:50
        is_us = (weekday <= 4 and hour >= 22) or (1 <= weekday <= 5 and hour < 5)
        if is_us and hour == 22 and now.minute <= 35:
            logger.info("=== [갭페이드] 개장 시간 재시작 — 즉시 스캔 ===")
            await self.gap_fade.cache_prev_close()
            await self.gap_fade.execute_entry()
