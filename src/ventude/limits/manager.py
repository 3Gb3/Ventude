"""Agenda as consultas, guarda o último valor e avisa quando algo muda."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ventude.core.config import Settings
from ventude.core.paths import user_home
from ventude.core.store import Store
from ventude.core.tasks import Tasks
from ventude.core.timefmt import resets_text
from ventude.limits import antigravity, claude, grok
from ventude.limits.model import FetchContext, ProviderError, Report

log = logging.getLogger(__name__)

Fetcher = Callable[[FetchContext], Report]
DEFAULT_FETCHERS: dict[str, Fetcher] = {
    "claude": claude.fetch,
    "antigravity": antigravity.fetch,
    "grok": grok.fetch,
}
LOCAL_INTERVAL = {"antigravity": 120}  # consulta local, barata
IDLE_LOCAL_INTERVAL = 240  # app local fechado: procura com menos frequência
CACHE_TTL = 30 * 86400
THRESHOLDS = (80, 95)


class LimitsManager(QObject):
    changed = Signal(str)
    ticked = Signal()
    alert = Signal(str, str)

    def __init__(
        self,
        store: Store,
        tasks: Tasks,
        settings: Callable[[], Settings],
        *,
        home: Path | None = None,
        fetchers: dict[str, Fetcher] | None = None,
    ) -> None:
        super().__init__()
        self.store, self.tasks, self.settings = store, tasks, settings
        self.home = home or user_home()
        self.fetchers = fetchers or DEFAULT_FETCHERS
        self.reports: dict[str, Report] = {}
        self._last_attempt: dict[str, float] = {}
        self._busy: set[str] = set()
        self._notified: set[tuple[str, str, float]] = set()
        now = time.time()
        for provider in self.fetchers:
            cached = store.cache_get(f"limits:{provider}", allow_expired=True)
            if not cached:
                continue
            try:
                report = Report.from_dict(cached)
            except (TypeError, ValueError):
                continue
            if report.state == "ok":
                report.state = "stale"
                report.message = ""
            self.reports[provider] = report
            for window in report.windows:
                if window.resets_at and window.resets_at <= now:
                    self._notified.add((provider, window.key, window.resets_at))
        self.timer = QTimer(self, interval=30_000)
        self.timer.timeout.connect(self.tick)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(600, self.tick)

    def providers(self) -> list[str]:
        return list(self.fetchers)

    def report(self, provider: str) -> Report:
        existing = self.reports.get(provider)
        if existing:
            return existing
        if self.settings().provider_mode(provider) == "off":
            return Report(provider, "off")
        return Report(provider, "loading", message="Consultando…")

    def busy(self, provider: str) -> bool:
        return provider in self._busy

    def visible_reports(self) -> list[Report]:
        return [r for r in (self.report(p) for p in self.fetchers) if r.state != "off"]

    def summary(self) -> str:
        parts = []
        for report in self.visible_reports():
            head = report.headline()
            parts.append(f"{report.name} {head.used_at():.0f}%" if head else f"{report.name} —")
        return " · ".join(parts) if parts else "Nenhuma IA ativa"

    # ---------- ciclo ----------
    def tick(self) -> None:
        now = time.time()
        settings = self.settings()
        for provider in self.fetchers:
            if settings.provider_mode(provider) == "off":
                if self.reports.get(provider, Report(provider, "")).state != "off":
                    self.reports[provider] = Report(provider, "off")
                    self.changed.emit(provider)
                continue
            self._check_resets(provider, now)
            interval = LOCAL_INTERVAL.get(provider, settings.limits_interval_min * 60)
            if provider in LOCAL_INTERVAL and self.report(provider).state != "ok":
                interval = IDLE_LOCAL_INTERVAL
            if now - self._last_attempt.get(provider, 0) >= interval:
                self.refresh(provider)
        self.ticked.emit()

    def refresh_all(self) -> None:
        for provider in self.fetchers:
            self.refresh(provider, force=True)

    def refresh(self, provider: str, force: bool = False) -> None:
        settings = self.settings()
        mode = settings.provider_mode(provider)
        now = time.time()
        if provider in self._busy:
            return
        if mode == "off":
            self.reports[provider] = Report(provider, "off")
            self.changed.emit(provider)
            return
        if not force and now - self._last_attempt.get(provider, 0) < 15:
            return
        self._busy.add(provider)
        self._last_attempt[provider] = now
        ctx = FetchContext(mode, self.home, self.store.secret, self.reports.get(provider))
        self.changed.emit(provider)
        self.tasks.run(
            self._fetch,
            provider,
            ctx,
            done=lambda report, p=provider: self._apply(p, report),
            owner=self,
        )

    def _fetch(self, provider: str, ctx: FetchContext) -> Report:
        """Roda numa thread de trabalho: nunca toca na interface."""
        try:
            report = self.fetchers[provider](ctx)
        except ProviderError as exc:
            return self._failed(provider, ctx.previous, str(exc))
        except Exception:  # noqa: BLE001
            log.exception("Consulta de limites (%s) falhou", provider)
            return self._failed(provider, ctx.previous, "Falha inesperada; veja o log do Ventude.")
        if report.state == "ok":
            report.fetched_at = time.time()
        return report

    @staticmethod
    def _failed(provider: str, previous: Report | None, message: str) -> Report:
        if previous and previous.windows:
            return replace(previous, state="stale", message=message)
        return Report(provider, "error", message=message)

    def _apply(self, provider: str, report: Report) -> None:
        self._busy.discard(provider)
        if self.settings().provider_mode(provider) == "off":
            return
        old = self.reports.get(provider)
        self.reports[provider] = report
        if report.state == "ok":
            self._threshold_alerts(old, report)
        if report.windows or report.state in ("setup", "error"):
            self.store.cache_set(f"limits:{provider}", report.to_dict(), CACHE_TTL)
        self.changed.emit(provider)

    # ---------- avisos ----------
    def _notify(self, title: str, message: str) -> None:
        if self.settings().limits_notify:
            self.alert.emit(title, message)

    def _threshold_alerts(self, old: Report | None, new: Report) -> None:
        if not old or not old.windows:
            return  # primeira leitura: nada a comparar
        before = {w.key: w for w in old.windows}
        for window in new.windows:
            previous = before.get(window.key)
            was = previous.used_at() if previous else None
            now_used = window.used_at()
            if was is None or now_used is None:
                continue
            crossed = [t for t in THRESHOLDS if was < t <= now_used]
            if crossed:
                when = resets_text(window.resets_at)
                self._notify(
                    f"{new.name}: {now_used:.0f}% usado",
                    f"{window.label} chegou a {now_used:.0f}%." + (f" {when}." if when else ""),
                )

    def _check_resets(self, provider: str, now: float) -> None:
        report = self.reports.get(provider)
        if not report:
            return
        renewed = False
        for window in report.windows:
            key = (provider, window.key, window.resets_at or 0)
            if not window.resets_at or window.resets_at > now or key in self._notified:
                continue
            self._notified.add(key)
            renewed = True
            if (window.used or 0) >= 50:
                self._notify(f"{report.name}: limite renovado", f"{window.label} está livre de novo.")
        if renewed:
            self.changed.emit(provider)
            self.refresh(provider, force=True)
