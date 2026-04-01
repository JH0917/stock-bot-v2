"""장 스케줄러 — 장전/장중/장후 자동 워크플로우"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from strategy.rsi_strategy import RSIStrategy
from strategy.etf_momentum import ETFMomentumStrategy
from trader.risk_manager import RiskManager
from trader.executor import Executor

logger = logging.getLogger(__name__)

DOW = "mon-fri"


class MarketScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.executor = Executor()
        self.risk_manager = RiskManager()
        self.rsi_strategy = RSIStrategy(self.risk_manager, self.executor)
        self.etf_strategy = ETFMomentumStrategy(self.risk_manager, self.executor)
        self._pending_rsi_candidates = []

    def start(self):
        # ─── 장전 ───
        self.scheduler.add_job(self._pre_market_scan, CronTrigger(hour=8, minute=30, day_of_week=DOW), id="pre_market")

        # ─── 장 시작 ───
        self.scheduler.add_job(self._etf_capture_open, CronTrigger(hour=9, minute=0, day_of_week=DOW), id="etf_open")
        self.scheduler.add_job(self._rsi_entry, CronTrigger(hour=9, minute=5, day_of_week=DOW), id="rsi_entry")
        self.scheduler.add_job(self._etf_entry, CronTrigger(hour=9, minute=31, day_of_week=DOW), id="etf_entry")

        # ─── 장중 모니터링 (1분마다, 09:10~15:20) ───
        self.scheduler.add_job(self._monitor_positions, CronTrigger(hour=9, minute="10-59", second=0, day_of_week=DOW), id="monitor_09")
        self.scheduler.add_job(self._monitor_positions, CronTrigger(hour="10-14", minute="*", second=0, day_of_week=DOW), id="monitor_10_14")
        self.scheduler.add_job(self._monitor_positions, CronTrigger(hour=15, minute="0-20", second=0, day_of_week=DOW), id="monitor_15")

        # ─── 장 마감 ───
        self.scheduler.add_job(self._etf_close_all, CronTrigger(hour=15, minute=15, day_of_week=DOW), id="etf_close")
        self.scheduler.add_job(self._rsi_exit_check, CronTrigger(hour=15, minute=35, day_of_week=DOW), id="rsi_exit_check")
        self.scheduler.add_job(self._daily_report, CronTrigger(hour=15, minute=40, day_of_week=DOW), id="daily_report")

        self.scheduler.start()
        logger.info("스케줄러 시작")

    async def shutdown(self):
        self.scheduler.shutdown()
        await self.executor.close()

    # ─── 작업 정의 ───

    async def _pre_market_scan(self):
        """08:30 — RSI(2) 매수 후보 스크리닝"""
        logger.info("=== 장전 스크리닝 시작 ===")
        self._pending_rsi_candidates = await self.rsi_strategy.scan_entry()
        if self._pending_rsi_candidates:
            symbols = [c["symbol"] for c in self._pending_rsi_candidates]
            logger.info(f"매수 예정 종목: {symbols}")
        else:
            logger.info("매수 후보 없음")

    async def _rsi_entry(self):
        """09:05 — RSI 매수 + RSI 익절 매도"""
        await self.rsi_strategy.execute_rsi_exit()
        if self._pending_rsi_candidates:
            logger.info(f"=== RSI 매수 실행: {len(self._pending_rsi_candidates)}종목 ===")
            await self.rsi_strategy.execute_entry(self._pending_rsi_candidates)
            self._pending_rsi_candidates = []

    async def _etf_capture_open(self):
        await self.etf_strategy.capture_open()

    async def _etf_entry(self):
        logger.info("=== ETF 모멘텀 진입 체크 ===")
        await self.etf_strategy.check_entry()

    async def _monitor_positions(self):
        """1분마다 — 손절 체크만 (RSI 익절은 장 마감 후)"""
        await self.rsi_strategy.check_exit()
        await self.etf_strategy.check_exit()

    async def _etf_close_all(self):
        logger.info("=== ETF 시간 청산 ===")
        await self.etf_strategy.close_all()

    async def _rsi_exit_check(self):
        """15:35 — RSI(2) > 70 익절 체크 (다음날 매도 예약)"""
        await self.rsi_strategy.check_rsi_exit_all()

    async def _daily_report(self):
        report = self.risk_manager.daily_report()
        logger.info(f"\n{report}")
