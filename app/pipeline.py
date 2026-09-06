from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

import httpx

from app.audit.beavercheck import BeaverCheckClient, missing_audit
from app.audit.service import AuditService, unique_website_urls
from app.catalog import load_cities, load_countries, load_industries
from app.combinations import load_geo, next_combination
from app.config import AppConfig
from app.contacts.discover import discover_contacts
from app.discovery.factory import build_discovery
from app.models import (
    AuditResult,
    BusinessRecord,
    Combination,
    CombinationState,
    DomainState,
    Lead,
    ReportHistoryEntry,
)
from app.rate_limit import RateLimiter
from app.reports.csv_report import hourly_report_filename, hourly_summary, report_filename, summarize, write_csv
from app.reports.telegram import TelegramClient
from app.scheduler import report_due, utcnow
from app.scoring import build_lead
from app.state import StateStore
from app.urls import business_key, domain_from_url

logger = logging.getLogger(__name__)


class CombinationDeferred(Exception):
    def __init__(self, combination: Combination) -> None:
        self.combination = combination
        super().__init__(f"Deferred combination {combination.key}")


class LeadApp:
    def __init__(self, config: AppConfig, rng: Random | None = None) -> None:
        self.config = config
        self.rng = rng or Random()
        self.countries = load_countries(Path(config.countries_file))
        self.cities = load_cities(Path(config.cities_file))
        self.industries = load_industries(Path(config.industries_file))
        self.combinations = load_geo(self.countries, self.cities, self.industries)
        self.store = StateStore(config.state_path)
        config.reports_dir.mkdir(parents=True, exist_ok=True)
        self._hourly_leads: list[Lead] = []
        self._hourly_keys: set[str] = set()
        self._hourly_interval_seconds = 3600.0
        self._hourly_report_lock = asyncio.Lock()
        self._last_hourly_report_monotonic: float | None = None
        self._deferred_until: dict[str, float] = {}
        self._defer_cooldown_seconds = 300.0

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": self.config.discovery.user_agent},
            follow_redirects=True,
        )

    def _save_businesses(self, records: list[BusinessRecord]) -> None:
        for record in records:
            key = business_key(record.domain, record.name, record.city, record.country)
            self.store.state.businesses[key] = record
            if record.domain:
                existing = self.store.state.domains.get(record.domain)
                if existing is None:
                    self.store.state.domains[record.domain] = DomainState(
                        domain=record.domain,
                        first_seen=utcnow(),
                        website=record.website,
                    )

    def _mark_complete(self, combination: Combination, business_count: int) -> None:
        previous_in_progress = self.store.state.in_progress_combination
        self.store.state.processed_combinations[combination.key] = CombinationState(
            key=combination.key,
            country=combination.country,
            city=combination.city,
            industry=combination.industry,
            completed_at=utcnow(),
            business_count=business_count,
        )
        self.store.state.in_progress_combination = None
        try:
            self.store.persist()
        except OSError:
            self.store.state.processed_combinations.pop(combination.key, None)
            self.store.state.in_progress_combination = previous_in_progress
            raise

    def _begin_combination(self, combination: Combination) -> None:
        self.store.state.in_progress_combination = combination.key

    def _active_deferred_keys(self) -> set[str]:
        now = time.monotonic()
        ready = [key for key, until in self._deferred_until.items() if until <= now]
        for key in ready:
            del self._deferred_until[key]
            logger.info("Deferred combination %s is eligible again", key)
        return set(self._deferred_until)

    def _defer_combination(self, combination: Combination) -> None:
        if self.store.state.in_progress_combination == combination.key:
            self.store.state.in_progress_combination = None
        self._deferred_until[combination.key] = time.monotonic() + self._defer_cooldown_seconds
        logger.warning(
            "Deferring %s after Overpass retries exhausted; will retry later",
            combination.key,
        )

    async def discover_combination(self, combination: Combination) -> list[BusinessRecord]:
        async with self._client() as client:
            provider = build_discovery(self.config, client, self.industries, self.cities)
            return await provider.discover(combination)

    async def _record_lead(
        self,
        record: BusinessRecord,
        audit: AuditResult | None,
        client: httpx.AsyncClient,
    ) -> Lead:
        if record.domain and audit is not None:
            domain = self.store.state.domains.get(record.domain)
            if domain:
                domain.last_audit_at = audit.fetched_at
                domain.last_score = audit.score
                self.store.state.domains[record.domain] = domain
        contacts = []
        if record.website:
            try:
                contacts = await discover_contacts(
                    client,
                    record.website,
                    self.config.contacts,
                )
            except Exception:
                logger.exception("Contact discovery failed for %s; continuing", record.website)
                contacts = []
        lead = build_lead(
            record,
            audit,
            self.config.scoring,
            self.config.minimum_score,
            contacts=contacts,
        )
        key = business_key(
            lead.business.domain,
            lead.business.name,
            lead.business.city,
            lead.business.country,
        )
        self.store.state.leads[key] = lead
        if lead.qualified and key not in self._hourly_keys:
            self._hourly_leads.append(lead)
            self._hourly_keys.add(key)
        if lead.business.domain:
            domain = self.store.state.domains.get(lead.business.domain)
            if domain:
                domain.last_score = lead.score
        return lead

    async def scan_records(self, records: list[BusinessRecord] | None = None) -> list[Lead]:
        targets = list(self.store.state.businesses.values()) if records is None else records
        leads: list[Lead] = []
        with_sites = [record for record in targets if record.website]
        without_sites = [record for record in targets if not record.website]
        urls = unique_website_urls([record.website or "" for record in with_sites])
        logger.info("Found %s businesses with websites", len(urls))
        batch_size = max(1, min(5, self.config.audit.batch_size))
        async with self._client() as client:
            for record in without_sites:
                leads.append(await self._record_lead(record, None, client))
            limiter = RateLimiter(self.config.audit.rate_limit_per_minute)
            beaver = BeaverCheckClient(client, self.config.audit, limiter)
            audits = AuditService(beaver, self.config.audit)
            total_batches = (len(urls) + batch_size - 1) // batch_size if urls else 0
            processed_urls = 0
            by_domain: dict[str, AuditResult] = {}
            for index in range(0, len(urls), batch_size):
                chunk = urls[index : index + batch_size]
                batch_no = (index // batch_size) + 1
                logger.info(
                    "BeaverCheck batch %s/%s: %s URLs",
                    batch_no,
                    total_batches,
                    len(chunk),
                )
                try:
                    batch = await audits.audit_batch(chunk)
                except httpx.HTTPError:
                    logger.exception(
                        "BeaverCheck batch %s/%s failed after retries; continuing without those audits",
                        batch_no,
                        total_batches,
                    )
                    batch = {url: missing_audit(url) for url in chunk}
                by_domain.update({item.domain: item for item in batch.values()})
                processed_urls += len(chunk)
                chunk_domains = {domain_from_url(url) for url in chunk}
                for record in with_sites:
                    if record.domain in chunk_domains and record.domain in by_domain:
                        leads.append(
                            await self._record_lead(record, by_domain[record.domain], client)
                        )
            if urls:
                logger.info(
                    "Finished BeaverCheck processing: %s/%s URLs",
                    processed_urls,
                    len(urls),
                )
            if processed_urls != len(urls):
                msg = "BeaverCheck did not finish every website URL for this combination"
                raise RuntimeError(msg)
            scored_domains = {lead.business.domain for lead in leads}
            for record in with_sites:
                if record.domain not in scored_domains:
                    leads.append(await self._record_lead(record, None, client))
        return leads

    def qualified_leads(self) -> list[Lead]:
        return [lead for lead in self.store.state.leads.values() if lead.qualified]

    async def generate_and_send_report(
        self,
        telegram: TelegramClient | None = None,
        *,
        send_telegram: bool = True,
    ) -> Path:
        leads = self.qualified_leads()
        filename = report_filename(utcnow(), self.config.timezone)
        path = self.config.reports_dir / filename
        write_csv(path, leads)
        summary = summarize(leads)
        sent = False
        client = telegram
        owned_client: httpx.AsyncClient | None = None
        try:
            if send_telegram:
                if client is None:
                    owned_client = self._client()
                    client = TelegramClient(owned_client, self.config.telegram)
                if client.enabled():
                    await client.send_document(path, caption=summary)
                    sent = True
                else:
                    logger.warning("Telegram is not configured; report saved to %s", path)
            else:
                logger.info("Daily report saved to %s", path)
        finally:
            if owned_client is not None:
                await owned_client.aclose()

        website = sum(1 for lead in leads if lead.website_status == "has_website")
        none = sum(1 for lead in leads if lead.website_status == "no_website")
        self.store.state.report_history.append(
            ReportHistoryEntry(
                generated_at=utcnow(),
                path=str(path),
                qualified_count=len(leads),
                website_leads=website,
                no_website_leads=none,
                sent=sent,
            )
        )
        self.store.state.last_daily_report_date = datetime.now(
            tz=ZoneInfo(self.config.timezone)
        ).date().isoformat()
        self.store.persist()
        return path

    def _combination_by_key(self, key: str) -> Combination | None:
        for item in self.combinations:
            if item.key == key:
                return item
        return None

    def _next_combination(self) -> Combination | None:
        deferred = self._active_deferred_keys()
        in_progress = self.store.state.in_progress_combination
        if (
            in_progress
            and in_progress not in self.store.state.processed_combinations
            and in_progress not in deferred
        ):
            current = self._combination_by_key(in_progress)
            if current is not None:
                return current
        processed = set(self.store.state.processed_combinations) | deferred
        return next_combination(self.combinations, processed, self.rng)

    async def discover_next(self) -> tuple[Combination | None, list[BusinessRecord]]:
        combination = self._next_combination()
        if combination is None:
            logger.info("No unprocessed country/city/industry combinations remain")
            return None, []
        logger.info("Discovering %s", combination.key)
        self._begin_combination(combination)
        try:
            records = await self.discover_combination(combination)
        except httpx.HTTPError as exc:
            self._defer_combination(combination)
            raise CombinationDeferred(combination) from exc
        self._save_businesses(records)
        logger.info("Found %s businesses", len(records))
        return combination, records

    async def process_one_combination(self) -> Combination | None:
        combination, records = await self.discover_next()
        if combination is None:
            return None
        await self.scan_records(records)
        self._mark_complete(combination, len(records))
        logger.info("Completed %s", combination.key)
        return combination

    async def maybe_report(self) -> None:
        if report_due(utcnow(), self.config, self.store.state.last_daily_report_date):
            logger.info("Generating daily report")
            await self.generate_and_send_report(send_telegram=False)

    async def send_hourly_telegram_report(
        self,
        telegram: TelegramClient | None = None,
    ) -> None:
        leads = list(self._hourly_leads)
        summary = hourly_summary(leads)
        client = telegram
        owned_client: httpx.AsyncClient | None = None
        tmp_dir = Path(tempfile.mkdtemp(prefix="hourly-leads-"))
        filename = hourly_report_filename(utcnow(), self.config.timezone)
        path = tmp_dir / filename
        try:
            if client is None:
                owned_client = self._client()
                client = TelegramClient(owned_client, self.config.telegram)
            if not client.enabled():
                logger.error(
                    "Hourly Telegram report not sent; TELEGRAM_BOT_TOKEN and "
                    "TELEGRAM_CHAT_ID are required"
                )
                return
            write_csv(path, leads)
            await client.send_document(path, caption=summary)
            self._hourly_leads.clear()
            self._hourly_keys.clear()
            self._last_hourly_report_monotonic = time.monotonic()
            logger.info("Sent hourly Telegram report (%s leads)", len(leads))
        except Exception:
            logger.exception("Hourly Telegram report failed; keeping accumulated leads")
            raise
        finally:
            if owned_client is not None:
                await owned_client.aclose()
            try:
                if path.exists():
                    path.unlink()
                tmp_dir.rmdir()
            except OSError:
                logger.warning("Could not delete temporary hourly report file %s", path)

    async def maybe_send_hourly_telegram_report(
        self,
        telegram: TelegramClient | None = None,
    ) -> None:
        async with self._hourly_report_lock:
            now = time.monotonic()
            last = self._last_hourly_report_monotonic
            if last is not None and (now - last) < self._hourly_interval_seconds:
                return
            await self.send_hourly_telegram_report(telegram)

    async def _hourly_report_loop(self) -> None:
        while True:
            await asyncio.sleep(self._hourly_interval_seconds)
            try:
                await self.maybe_send_hourly_telegram_report()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hourly Telegram report loop will retry next hour")

    async def run_forever(self) -> None:
        logger.info(
            "Starting continuous mode with %s combinations",
            len(self.combinations),
        )
        hourly = asyncio.create_task(self._hourly_report_loop(), name="hourly-telegram")
        try:
            while True:
                await self.maybe_report()
                try:
                    combination = await self.process_one_combination()
                except CombinationDeferred as exc:
                    logger.warning(
                        "Skipped %s after Overpass failure; continuing to the next combination",
                        exc.combination.key,
                    )
                    await asyncio.sleep(self.config.loop_delay_seconds)
                    continue
                except httpx.HTTPError:
                    key = self.store.state.in_progress_combination
                    current = self._combination_by_key(key) if key else None
                    if current is not None:
                        self._defer_combination(current)
                        logger.exception(
                            "HTTP failure for %s; deferring and continuing",
                            current.key,
                        )
                        await asyncio.sleep(self.config.loop_delay_seconds)
                        continue
                    logger.exception("Transient HTTP failure; continuing")
                    await asyncio.sleep(self.config.loop_delay_seconds)
                    continue
                if combination is None:
                    await self.maybe_report()
                    await asyncio.sleep(max(self.config.loop_delay_seconds, 15))
                    continue
                await asyncio.sleep(self.config.loop_delay_seconds)
        finally:
            hourly.cancel()
            try:
                await hourly
            except asyncio.CancelledError:
                pass
