"""장 스케줄러 — 장전/장중/장후 자동 워크플로우"""

import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from strategy.rsi_strategy import RSIStrategy
from strategy.etf_momentum import ETFMomentumStrategy
from strategy.us_box_strategy import USBoxStrategy
from trader.risk_manager import RiskManager
from trader.executor import Executor
import config

logger = logging.getLogger(__name__)

DOW = "mon-fri"


class MarketScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.executor = Executor()
        self.risk_manager = RiskManager()
        self.rsi_strategy = RSIStrategy(self.risk_manager, self.executor)
        self.etf_strategy = ETFMomentumStrategy(self.risk_manager, self.executor)
        self.us_box_strategy = USBoxStrategy(self.risk_manager, self.executor)
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

        # ─── 미국장 (서머타임 기준, 한국시간) ───
        # 22:00 스캔, 22:35 매수, 0:05/2:05 재스캔+매수, 30분마다 모니터링
        self.scheduler.add_job(self._us_box_scan, CronTrigger(hour=22, minute=0, day_of_week=DOW), id="us_scan")
        self.scheduler.add_job(self._us_box_entry, CronTrigger(hour=22, minute=35, day_of_week=DOW), id="us_entry")
        # 장중 재스캔 (0시, 2시) — 새로운 Buy Zone 진입 기회 포착
        self.scheduler.add_job(self._us_box_rescan, CronTrigger(hour="0,2", minute=5, day_of_week="tue-sat"), id="us_rescan")
        self.scheduler.add_job(self._us_box_monitor, CronTrigger(hour=22, minute="30,59", day_of_week=DOW), id="us_mon_22")
        self.scheduler.add_job(self._us_box_monitor, CronTrigger(hour="23", minute="0,30", day_of_week=DOW), id="us_mon_23")
        self.scheduler.add_job(self._us_box_monitor, CronTrigger(hour="0-4", minute="0,30", day_of_week="tue-sat"), id="us_mon_0_4")
        self.scheduler.add_job(self._us_box_cancel, CronTrigger(hour=4, minute=50, day_of_week="tue-sat"), id="us_cancel")
        self.scheduler.add_job(self._us_box_split_check, CronTrigger(hour="0,2,4", minute=0, day_of_week="tue-sat"), id="us_split")

        self.scheduler.start()
        logger.info("스케줄러 시작 (국내장 + 미국장)")

        # 시작 시 장중이면 즉시 스캔
        asyncio.ensure_future(self._startup_catch_up())

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
        if not self.risk_manager.get_positions():
            return  # 포지션 없으면 스킵
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

    # ─── 미국 박스권 전략 ───

    async def _us_box_scan(self):
        """22:00 — 미국 박스권 일일 스캔"""
        logger.info("=== [US Box] 일일 스캔 시작 ===")
        await self.us_box_strategy.daily_scan()

    async def _us_box_entry(self):
        """22:35 — 미국 박스권 매수 실행"""
        logger.info("=== [US Box] 매수 실행 ===")
        await self.us_box_strategy.execute_entry()

    async def _us_box_monitor(self):
        """30분마다 — 청산 조건 확인 (포지션 있을 때만)"""
        if not self.risk_manager.get_positions(strategy="us_box"):
            return
        await self.us_box_strategy.check_exit()

    async def _us_box_split_check(self):
        """3시간마다 — 분할매수 추가 진입 확인 (포지션 있을 때만)"""
        if not self.risk_manager.get_positions(strategy="us_box"):
            return
        await self.us_box_strategy.check_split_entry()

    async def _us_box_rescan(self):
        """0:05, 2:05 — 장중 재스캔 + 매수 (빈 슬롯 있을 때만)"""
        us_positions = self.risk_manager.get_positions(strategy="us_box")
        if len(us_positions) >= config.US_BOX_MAX_POSITIONS:
            logger.info("[US Box] 재스캔 스킵 (최대 포지션 도달)")
            return
        logger.info("=== [US Box] 장중 재스캔 시작 ===")
        await self.us_box_strategy.daily_scan()
        await self.us_box_strategy.execute_entry()

    async def _us_box_cancel(self):
        """04:50 — 미체결 주문 취소"""
        logger.info("=== [US Box] 미체결 주문 정리 ===")
        await self.us_box_strategy.cancel_stale_orders()

    async def _startup_catch_up(self):
        """시작 시 장중이면 즉시 스캔 (재배포 대응)"""
        await asyncio.sleep(3)  # 스케줄러 안정화 대기
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()  # 0=월 ~ 6=일

        # 미국장 시간: 22:00~04:50 (월~금 22시 = weekday 0~4, 화~토 0~4시 = weekday 1~5)
        is_us_session = False
        if weekday <= 4 and hour >= 22:
            is_us_session = True
        elif 1 <= weekday <= 5 and hour < 5:
            is_us_session = True

        if is_us_session and not self.us_box_strategy._candidates:
            logger.info("=== [US Box] 시작 시 즉시 스캔 (장중 재배포 감지) ===")
            await self.us_box_strategy.daily_scan()
            await self.us_box_strategy.execute_entry()
