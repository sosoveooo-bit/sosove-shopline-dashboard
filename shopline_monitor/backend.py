from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import threading
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SUPPORTED_RANGES = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
DEFAULT_CURRENCY = "USD"
DEFAULT_API_VERSION = "v20260301"
DEFAULT_TIMEZONE = "Asia/Tokyo"
ORDER_PAGE_LIMIT = 100
DEFAULT_MAX_ORDER_PAGES = 5
DEFAULT_PRODUCT_COST_RATE = 0.35
DEFAULT_PAYMENT_FEE_RATE = 0.036
DEFAULT_SHIPPING_COST_PER_ORDER = 0.0
DEFAULT_CONVERSION_TRAFFIC_FIELD = "visitors"
DEFAULT_GA4_KEY_EVENT_NAME = "purchase"
DEFAULT_GA4_CONVERSION_METRIC = "userKeyEventRate"
DEFAULT_GA4_CONVERSION_MODE = "key_event_rate"
DEFAULT_DASHBOARD_CACHE_SECONDS = 180
DEFAULT_SHOPLINE_TIMEOUT_SECONDS = 30.0
DEFAULT_SHOPLINE_RETRY_ATTEMPTS = 3
GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
CLICK_ID_KEYS = (
    "gclid",
    "dclid",
    "wbraid",
    "gbraid",
    "gad_source",
    "fbclid",
    "ttclid",
    "msclkid",
    "twclid",
    "epik",
)
UTM_REQUIRED_FIELDS = (
    ("sourceUtm", "utm_source"),
    ("sourceMedium", "utm_medium"),
    ("sourceCampaign", "utm_campaign"),
)
UTM_NAMING_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
UTM_PLACEHOLDERS = {
    "(not set)",
    "not set",
    "none",
    "null",
    "undefined",
    "unknown",
    "n/a",
    "na",
    "--",
    "未设置",
}
UTM_SOURCE_ALIASES = {
    "fb": "facebook",
    "fb.com": "facebook",
    "facebook.com": "facebook",
    "meta": "facebook",
    "ig": "instagram",
    "insta": "instagram",
    "instagram.com": "instagram",
    "googleads": "google",
    "google_ads": "google",
    "tiktok.com": "tiktok",
    "tt": "tiktok",
    "newsletter": "email",
    "mail": "email",
    "direct / none": "direct",
}
UTM_MEDIUM_ALIASES = {
    "paid social": "paid_social",
    "paid-social": "paid_social",
    "paidsocial": "paid_social",
    "social paid": "paid_social",
    "cost per click": "cpc",
    "e-mail": "email",
    "organic social": "organic_social",
}
UTM_MEDIUM_VALUES = {
    "affiliate",
    "cpc",
    "cpm",
    "display",
    "email",
    "organic",
    "organic_social",
    "paid",
    "paid_social",
    "ppc",
    "referral",
    "retargeting",
    "social",
}
CLICK_SOURCE_EXPECTATIONS = {
    "gclid": {"Google", "Google Ads"},
    "dclid": {"Google", "Google Ads"},
    "wbraid": {"Google", "Google Ads"},
    "gbraid": {"Google", "Google Ads"},
    "gad_source": {"Google", "Google Ads"},
    "fbclid": {"Facebook", "Instagram"},
    "ttclid": {"TikTok"},
    "msclkid": {"Bing"},
    "twclid": {"X / Twitter"},
    "epik": {"Pinterest"},
}
TRAFFIC_SOURCE_BUCKETS = {
    "Facebook",
    "Instagram",
    "Google",
    "Google Ads",
    "TikTok",
    "YouTube",
    "LINE",
    "X / Twitter",
    "Pinterest",
    "Bing",
    "Yahoo",
    "Email",
    "SmartPush",
    "SMS / Push",
    "AI / Chat",
    "Direct",
    "Organic",
    "Ad",
    "Affiliate",
    "Referral",
    "Other",
}

_DASHBOARD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_CACHE_LOCK = threading.Lock()
_DATA_CACHE: dict[str, tuple[float, Any]] = {}
_LAST_GOOD_DATA: dict[str, tuple[float, Any]] = {}
_DATA_CACHE_LOCK = threading.Lock()
_CLICK_CAMPAIGN_MAP_CACHE: tuple[str, dict[str, dict[str, str]]] = ("", {})


def load_local_env_files() -> None:
    """Load machine-local .env files without overriding already-set variables."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
        Path.cwd() / "config" / "shopline.env",
        Path.home() / ".openclaw" / "shopline" / ".env",
        Path.home() / ".openclaw" / "credentials" / "shopline.env",
    ]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class ShoplineConfig:
    base_url: str = ""
    access_token: str = ""
    store_domain: str = ""
    orders_path: str = ""
    attribution_path: str = ""
    products_path: str = ""
    token_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    timeout_seconds: float = DEFAULT_SHOPLINE_TIMEOUT_SECONDS
    retry_attempts: int = DEFAULT_SHOPLINE_RETRY_ATTEMPTS
    default_currency: str = DEFAULT_CURRENCY
    timezone_name: str = DEFAULT_TIMEZONE
    conversion_traffic_field: str = DEFAULT_CONVERSION_TRAFFIC_FIELD
    max_order_pages: int = DEFAULT_MAX_ORDER_PAGES

    @classmethod
    def from_env(cls) -> "ShoplineConfig":
        timeout_raw = os.getenv(
            "SHOPLINE_TIMEOUT_SECONDS", str(DEFAULT_SHOPLINE_TIMEOUT_SECONDS)
        )
        retry_attempts_raw = os.getenv(
            "SHOPLINE_RETRY_ATTEMPTS", str(DEFAULT_SHOPLINE_RETRY_ATTEMPTS)
        )
        max_order_pages_raw = os.getenv("SHOPLINE_MAX_ORDER_PAGES", str(DEFAULT_MAX_ORDER_PAGES))
        api_version = os.getenv("SHOPLINE_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
        try:
            timeout = max(1.0, float(timeout_raw))
        except ValueError:
            timeout = DEFAULT_SHOPLINE_TIMEOUT_SECONDS
        try:
            retry_attempts = max(1, min(5, int(retry_attempts_raw)))
        except ValueError:
            retry_attempts = DEFAULT_SHOPLINE_RETRY_ATTEMPTS
        try:
            max_order_pages = max(1, min(25, int(max_order_pages_raw)))
        except ValueError:
            max_order_pages = DEFAULT_MAX_ORDER_PAGES

        raw_base_url = os.getenv("SHOPLINE_API_BASE_URL", "").strip()
        raw_orders_path = os.getenv("SHOPLINE_ORDERS_ENDPOINT", "").strip()
        raw_attribution_path = os.getenv(
            "SHOPLINE_ORDER_ATTRIBUTION_ENDPOINT",
            "/orders/order_attribution_info.json",
        ).strip()
        raw_products_path = os.getenv("SHOPLINE_PRODUCTS_ENDPOINT", "").strip()

        return cls(
            base_url=normalize_base_url(raw_base_url, api_version),
            access_token=os.getenv("SHOPLINE_ACCESS_TOKEN", "").strip(),
            store_domain=os.getenv("SHOPLINE_STORE_DOMAIN", "").strip(),
            orders_path=normalize_endpoint_path(raw_orders_path, "orders"),
            attribution_path=normalize_endpoint_path(raw_attribution_path, "order_attribution"),
            products_path=normalize_endpoint_path(raw_products_path, "products"),
            token_header=os.getenv("SHOPLINE_TOKEN_HEADER", "Authorization").strip()
            or "Authorization",
            auth_prefix=os.getenv("SHOPLINE_AUTH_PREFIX", "Bearer").strip(),
            timeout_seconds=timeout,
            retry_attempts=retry_attempts,
            default_currency=(
                os.getenv("SHOPLINE_DEFAULT_CURRENCY", DEFAULT_CURRENCY).strip()
                or DEFAULT_CURRENCY
            ).upper(),
            timezone_name=(
                os.getenv("SHOPLINE_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
            ),
            conversion_traffic_field=normalize_traffic_field(
                os.getenv("SHOPLINE_CONVERSION_TRAFFIC_FIELD", DEFAULT_CONVERSION_TRAFFIC_FIELD)
            ),
            max_order_pages=max_order_pages,
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.base_url and self.access_token)

    @property
    def live_ready(self) -> bool:
        return bool(self.has_credentials and (self.orders_path or self.products_path))


@dataclass(frozen=True)
class CostConfig:
    product_cost_rate: float = DEFAULT_PRODUCT_COST_RATE
    payment_fee_rate: float = DEFAULT_PAYMENT_FEE_RATE
    shipping_cost_per_order: float = DEFAULT_SHIPPING_COST_PER_ORDER

    @classmethod
    def from_env(cls) -> "CostConfig":
        return cls(
            product_cost_rate=env_float("SHOPLINE_PRODUCT_COST_RATE", DEFAULT_PRODUCT_COST_RATE),
            payment_fee_rate=env_float("SHOPLINE_PAYMENT_FEE_RATE", DEFAULT_PAYMENT_FEE_RATE),
            shipping_cost_per_order=env_float(
                "SHOPLINE_SHIPPING_COST_PER_ORDER", DEFAULT_SHIPPING_COST_PER_ORDER
            ),
        )


@dataclass(frozen=True)
class Ga4Config:
    property_id: str = ""
    service_account_json: str = ""
    service_account_file: str = ""
    key_event_name: str = DEFAULT_GA4_KEY_EVENT_NAME
    conversion_metric: str = DEFAULT_GA4_CONVERSION_METRIC
    conversion_mode: str = DEFAULT_GA4_CONVERSION_MODE
    timeout_seconds: float = 12.0

    @classmethod
    def from_env(cls) -> "Ga4Config":
        timeout_raw = os.getenv("GA4_TIMEOUT_SECONDS", "12")
        try:
            timeout_seconds = max(1.0, float(timeout_raw))
        except ValueError:
            timeout_seconds = 12.0
        return cls(
            property_id=os.getenv("GA4_PROPERTY_ID", "").strip(),
            service_account_json=os.getenv("GA4_SERVICE_ACCOUNT_JSON", "").strip(),
            service_account_file=os.getenv("GA4_SERVICE_ACCOUNT_FILE", "").strip(),
            key_event_name=(
                os.getenv("GA4_KEY_EVENT_NAME", DEFAULT_GA4_KEY_EVENT_NAME).strip()
                or DEFAULT_GA4_KEY_EVENT_NAME
            ),
            conversion_metric=normalize_ga4_metric(
                os.getenv("GA4_CONVERSION_METRIC", DEFAULT_GA4_CONVERSION_METRIC)
            ),
            conversion_mode=normalize_ga4_conversion_mode(
                os.getenv("GA4_CONVERSION_MODE", DEFAULT_GA4_CONVERSION_MODE)
            ),
            timeout_seconds=timeout_seconds,
        )

    @property
    def credential_source(self) -> str:
        if self.service_account_json:
            return "json"
        if self.service_account_file:
            return "file"
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
            return "adc"
        return "missing"

    @property
    def configured(self) -> bool:
        return bool(self.property_id and self.credential_source != "missing")

    @property
    def metric_name(self) -> str:
        return f"{self.conversion_metric}:{self.key_event_name}"


@dataclass(frozen=True)
class Ga4TrafficResult:
    rows: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True)
class Ga4ChannelResult:
    rows: list[dict[str, Any]]
    error: str | None = None


class ShoplineClient:
    def __init__(self, config: ShoplineConfig | None = None):
        self.config = config or ShoplineConfig.from_env()

    def connector_status(self) -> dict[str, Any]:
        missing = []
        if not self.config.base_url:
            missing.append("SHOPLINE_API_BASE_URL")
        if not self.config.access_token:
            missing.append("SHOPLINE_ACCESS_TOKEN")
        if not self.config.orders_path:
            missing.append("SHOPLINE_ORDERS_ENDPOINT")
        if not self.config.products_path:
            missing.append("SHOPLINE_PRODUCTS_ENDPOINT")

        mode = "live" if self.config.live_ready else "sample"
        ga4_config = Ga4Config.from_env()
        return {
            "mode": mode,
            "configured": self.config.live_ready,
            "hasCredentials": self.config.has_credentials,
            "baseUrl": mask_url(self.config.base_url),
            "storeDomain": self.config.store_domain,
            "ordersEndpoint": self.config.orders_path,
            "orderAttributionEndpoint": self.config.attribution_path,
            "productsEndpoint": self.config.products_path,
            "tokenHeader": self.config.token_header,
            "tokenPreview": mask_secret(self.config.access_token),
            "defaultCurrency": self.config.default_currency,
            "timezoneName": self.config.timezone_name,
            "trafficConfigured": bool(load_traffic_from_env()),
            "conversionTrafficField": self.config.conversion_traffic_field,
            "maxOrderPages": self.config.max_order_pages,
            "requestTimeoutSeconds": self.config.timeout_seconds,
            "retryAttempts": self.config.retry_attempts,
            "clickCampaignMapCount": len(load_click_campaign_map_from_env()),
            "missing": missing,
            "ga4": {
                "configured": ga4_config.configured,
                "propertyId": ga4_config.property_id,
                "keyEventName": ga4_config.key_event_name,
                "metricName": ga4_config.metric_name,
                "conversionMode": ga4_config.conversion_mode,
                "credentialSource": ga4_config.credential_source,
                "timeoutSeconds": ga4_config.timeout_seconds,
            },
        }

    def load_orders(self, days: int, today: date | None = None) -> dict[str, Any]:
        today = today or current_dashboard_date(self.config.timezone_name)
        if not self.config.has_credentials or not self.config.orders_path:
            items = sample_orders(days, today=today, currency=self.config.default_currency)
            return {
                "items": items,
                "source": "sample",
                "error": None,
                "pages": 1,
                "rawCount": len(items),
                "normalizedCount": len(items),
                "duplicateCount": 0,
                "chunks": 1,
                "pageLimitReached": False,
                "attributionRequested": 0,
                "attributionCount": 0,
                "attributionError": None,
            }

        start = today - timedelta(days=max(days - 1, 0))
        cache_key = f"shopline:orders:{self.config.base_url}:{start.isoformat()}:{today.isoformat()}"
        use_cache = type(self) is ShoplineClient
        cached = get_data_cache(cache_key) if use_cache else None
        if cached is not None:
            cached["cached"] = True
            return cached
        try:
            windows = build_date_chunks(start, today, order_chunk_days())
            if len(windows) > 1:
                with ThreadPoolExecutor(
                    max_workers=min(4, len(windows)),
                    thread_name_prefix="shopline-chunk",
                ) as pool:
                    chunks = list(
                        pool.map(lambda window: self._load_order_window_cached(*window), windows)
                    )
            else:
                chunks = [self._load_order_window_cached(start, today)]
            orders = [order for chunk in chunks for order in chunk["orders"]]
            pages = sum(int(chunk["pages"]) for chunk in chunks)
            raw_count = sum(int(chunk["rawCount"]) for chunk in chunks)
            page_limit_reached = any(bool(chunk["pageLimitReached"]) for chunk in chunks)
            attribution_requested = sum(int(chunk["attributionRequested"]) for chunk in chunks)
            attribution_count = sum(int(chunk["attributionCount"]) for chunk in chunks)
            attribution_errors = list(
                dict.fromkeys(
                    str(chunk["attributionError"])
                    for chunk in chunks
                    if chunk.get("attributionError")
                )
            )
            unique_orders, duplicate_count = deduplicate_orders(orders)
            result = {
                "items": unique_orders,
                "source": "live",
                "error": None,
                "pages": pages,
                "rawCount": raw_count,
                "normalizedCount": len(unique_orders),
                "duplicateCount": duplicate_count,
                "chunks": len(windows),
                "windowCacheHits": sum(bool(chunk.get("cacheHit")) for chunk in chunks),
                "pageLimitReached": page_limit_reached,
                "attributionRequested": attribution_requested,
                "attributionCount": attribution_count,
                "attributionError": " | ".join(attribution_errors) or None,
                "cached": False,
            }
            if use_cache:
                set_data_cache(cache_key, result)
            return result
        except Exception as exc:  # pragma: no cover - network-specific branch
            error = f"{exc.__class__.__name__}: {exc}"
            stale = get_last_good_data(cache_key) if use_cache else None
            if stale is not None:
                stale.update(
                    {
                        "source": "stale",
                        "error": f"Shopline 实时刷新失败，已保留上次成功数据：{error}",
                        "cached": True,
                        "stale": True,
                    }
                )
                return stale
            return {
                "items": [],
                "source": "error",
                "error": f"Shopline 实时订单拉取失败：{error}",
                "pages": 0,
                "rawCount": 0,
                "normalizedCount": 0,
                "duplicateCount": 0,
                "chunks": 0,
                "windowCacheHits": 0,
                "pageLimitReached": False,
                "attributionRequested": 0,
                "attributionCount": 0,
                "attributionError": None,
                "cached": False,
                "stale": False,
            }

    def _load_order_window_cached(self, start: date, end: date) -> dict[str, Any]:
        cache_key = (
            f"shopline:order-window:{self.config.base_url}:"
            f"{start.isoformat()}:{end.isoformat()}"
        )
        use_cache = type(self) is ShoplineClient
        cached = get_data_cache(cache_key) if use_cache else None
        if cached is not None:
            cached["cacheHit"] = True
            return cached
        result = self._load_order_window(start, end)
        result["cacheHit"] = False
        if use_cache:
            set_data_cache(cache_key, result)
        return result

    def _load_order_window(self, start: date, end: date) -> dict[str, Any]:
        params = build_order_query_params(start, end, timezone_name=self.config.timezone_name)
        raw_orders: list[dict[str, Any]] = []
        seen_page_info: set[str] = set()
        pages = 0
        raw_count = 0
        has_next_page = False
        for _ in range(self.config.max_order_pages):
            payload, headers = self.request_json_with_headers(self.config.orders_path, params=params)
            page_orders = [
                order
                for order in extract_collection(
                    payload,
                    ["orders", "order_list", "items", "data", "results"],
                )
                if isinstance(order, dict)
            ]
            pages += 1
            raw_count += len(page_orders)
            raw_orders.extend(page_orders)
            page_info = next_page_info_from_link(headers.get("Link") or headers.get("link", ""))
            has_next_page = bool(page_info)
            if not page_info or page_info in seen_page_info:
                break
            seen_page_info.add(page_info)
            params = {"limit": str(ORDER_PAGE_LIMIT), "page_info": page_info}

        order_ids = list(
            dict.fromkeys(
                order_id
                for index, order in enumerate(raw_orders, start=1)
                if (order_id := extract_raw_order_id(order, index))
            )
        )
        attribution_by_order_id: dict[str, dict[str, Any]] = {}
        attribution_error: str | None = None
        if self.config.attribution_path and order_ids:
            try:
                attribution_by_order_id = self.load_order_attribution(order_ids)
            except Exception as exc:  # pragma: no cover - network-specific branch
                attribution_error = f"Shopline attribution {exc.__class__.__name__}: {exc}"

        orders = normalize_shopline_orders(
            {"orders": raw_orders},
            self.config.default_currency,
            attribution_by_order_id=attribution_by_order_id,
        )
        return {
            "orders": orders,
            "pages": pages,
            "rawCount": raw_count,
            "pageLimitReached": has_next_page and pages >= self.config.max_order_pages,
            "attributionRequested": len(order_ids),
            "attributionCount": len(attribution_by_order_id),
            "attributionError": attribution_error,
        }

    def load_order_attribution(self, order_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(order_id).strip() for order_id in order_ids if str(order_id).strip()))
        attribution_by_order_id: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(unique_ids), 500):
            payload, _ = self.post_json_with_headers(
                self.config.attribution_path,
                {"orders": unique_ids[offset : offset + 500]},
            )
            rows = extract_collection(payload, ["data", "orders", "items", "results"])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                order_id = str(pick(row, "order_seq", "order_id", "id", default="") or "").strip()
                if order_id:
                    attribution_by_order_id[order_id] = row
        return attribution_by_order_id

    def load_products(self, today: date | None = None) -> dict[str, Any]:
        today = today or current_dashboard_date(self.config.timezone_name)
        if not self.config.has_credentials or not self.config.products_path:
            items = sample_products(today=today, currency=self.config.default_currency)
            return {
                "items": items,
                "source": "sample",
                "error": None,
                "rawCount": len(items),
            }

        cache_key = f"shopline:products:{self.config.base_url}:{today.isoformat()}"
        use_cache = type(self) is ShoplineClient
        cached = get_data_cache(cache_key) if use_cache else None
        if cached is not None:
            cached["cached"] = True
            return cached
        try:
            payload = self.request_json(self.config.products_path, params={"limit": "50"})
            products = normalize_shopline_products(payload, self.config.default_currency)
            result = {
                "items": products,
                "source": "live",
                "error": None,
                "rawCount": len(products),
                "cached": False,
            }
            if use_cache:
                set_data_cache(cache_key, result)
            return result
        except Exception as exc:  # pragma: no cover - network-specific branch
            error = f"{exc.__class__.__name__}: {exc}"
            stale = get_last_good_data(cache_key) if use_cache else None
            if stale is not None:
                stale.update(
                    {
                        "source": "stale",
                        "error": f"Shopline 商品刷新失败，已保留上次成功数据：{error}",
                        "cached": True,
                        "stale": True,
                    }
                )
                return stale
            return {
                "items": [],
                "source": "error",
                "error": f"Shopline 商品拉取失败：{error}",
                "rawCount": 0,
                "cached": False,
                "stale": False,
            }

    def request_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        payload, _ = self.request_json_with_headers(path, params=params)
        return payload

    def request_json_with_headers(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        url = build_url(self.config.base_url, path, params=params)
        return self._open_json_with_retry(
            lambda: urllib.request.Request(url, headers=self.request_headers(), method="GET")
        )

    def post_json_with_headers(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[Any, dict[str, str]]:
        url = build_url(self.config.base_url, path)
        encoded_payload = json.dumps(payload).encode("utf-8")
        return self._open_json_with_retry(
            lambda: urllib.request.Request(
                url,
                data=encoded_payload,
                headers=self.request_headers(),
                method="POST",
            )
        )

    def _open_json_with_retry(self, request_factory: Any) -> tuple[Any, dict[str, str]]:
        attempts = max(1, int(self.config.retry_attempts))
        for attempt in range(attempts):
            try:
                request = request_factory()
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_seconds
                ) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt + 1 >= attempts:
                    raise
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                if attempt + 1 >= attempts:
                    raise
            time_module.sleep(min(0.5 * (2**attempt), 2.0))

        raise RuntimeError("Shopline request retry loop exited unexpectedly")

    def request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.config.access_token:
            token = self.config.access_token
            if self.config.token_header.lower() == "authorization":
                token = f"{self.config.auth_prefix} {token}".strip()
            headers[self.config.token_header] = token
        if self.config.store_domain:
            headers["X-Shopline-Store-Domain"] = self.config.store_domain
        return headers

    def test_connection(self) -> dict[str, Any]:
        if not self.config.live_ready:
            return {
                "ok": True,
                "mode": "sample",
                "message": "sample mode",
                "checkedAt": now_iso(timezone_name=self.config.timezone_name),
            }

        probe_path = self.config.orders_path or self.config.products_path
        try:
            payload = self.request_json(probe_path, params={"limit": "1"})
            count = len(extract_collection(payload, ["orders", "products", "items", "data"]))
            return {
                "ok": True,
                "mode": "live",
                "message": f"received {count} item(s)",
                "checkedAt": now_iso(timezone_name=self.config.timezone_name),
            }
        except Exception as exc:  # pragma: no cover - network-specific branch
            return {
                "ok": False,
                "mode": "live",
                "message": f"{exc.__class__.__name__}: {exc}",
                "checkedAt": now_iso(timezone_name=self.config.timezone_name),
            }


def build_dashboard_payload(
    range_key: str = "7d",
    client: ShoplineClient | None = None,
    today: date | None = None,
    filters: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    started_at = time_module.monotonic()
    cache_enabled = client is None
    client = client or ShoplineClient()
    today = today or current_dashboard_date(client.config.timezone_name)
    days = resolve_range_days(range_key)
    normalized_filters = normalize_dashboard_filters(filters or {})
    cache_key = dashboard_cache_key(range_key, today, normalized_filters)
    if cache_enabled and force_refresh:
        clear_dashboard_cache()
        # A manual/automatic live refresh only needs to invalidate requests whose
        # window ends today. Historical comparison windows stay immutable and can
        # be reused, which keeps a live refresh fast without serving stale orders.
        clear_live_data_cache(today)
    if cache_enabled and not force_refresh:
        cached = get_dashboard_cache(cache_key)
        if cached is not None:
            cached["source"]["cached"] = True
            return cached
    lookback_days = max(days, 30)

    current_start = today - timedelta(days=days - 1)
    lookback_start = today - timedelta(days=lookback_days - 1)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start - timedelta(days=1)
    year_end = today - timedelta(days=365)
    year_start = year_end - timedelta(days=days - 1)

    if isinstance(client, ShoplineClient):
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="shopline-load") as pool:
            current_future = pool.submit(client.load_orders, lookback_days, today=today)
            previous_future = pool.submit(client.load_orders, days, today=previous_end)
            year_future = pool.submit(client.load_orders, days, today=year_end)
            products_future = pool.submit(client.load_products, today=today)
            current_orders_result = current_future.result()
            previous_orders_result = previous_future.result()
            year_orders_result = year_future.result()
            products_result = products_future.result()
    else:
        current_orders_result = client.load_orders(lookback_days, today=today)
        previous_orders_result = client.load_orders(days, today=previous_end)
        year_orders_result = client.load_orders(days, today=year_end)
        products_result = client.load_products(today=today)
    products = list(products_result["items"])

    current_order_items = list(current_orders_result["items"])
    unfiltered_current_orders = filter_orders_by_window(
        current_order_items, current_start, today
    )
    filter_options = build_filter_options(unfiltered_current_orders)
    current_orders = apply_dashboard_filters(unfiltered_current_orders, normalized_filters)
    lookback_orders = apply_dashboard_filters(filter_orders_by_window(
        current_order_items, lookback_start, today
    ), normalized_filters)
    previous_orders = apply_dashboard_filters(filter_orders_by_window(
        list(previous_orders_result["items"]), previous_start, previous_end
    ), normalized_filters)
    year_orders = apply_dashboard_filters(filter_orders_by_window(
        list(year_orders_result["items"]), year_start, year_end
    ), normalized_filters)
    traffic_overrides = load_traffic_from_env()
    lookback_traffic = build_traffic_series(
        lookback_start,
        today,
        lookback_orders,
        traffic_overrides=traffic_overrides,
        sample_mode=current_orders_result.get("source") == "sample",
    )
    current_traffic = build_traffic_series(
        current_start,
        today,
        current_orders,
        traffic_overrides=traffic_overrides,
        sample_mode=current_orders_result.get("source") == "sample",
    )
    previous_traffic = build_traffic_series(
        previous_start,
        previous_end,
        previous_orders,
        traffic_overrides=traffic_overrides,
        sample_mode=previous_orders_result.get("source") == "sample",
    )
    year_traffic = build_traffic_series(
        year_start,
        year_end,
        year_orders,
        traffic_overrides=traffic_overrides,
        sample_mode=year_orders_result.get("source") == "sample",
    )
    ga4_config = Ga4Config.from_env()
    if isinstance(client, ShoplineClient):
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ga4-load") as pool:
            ga4_lookback_future = pool.submit(load_ga4_traffic_for_window, lookback_start, today)
            ga4_previous_future = pool.submit(load_ga4_traffic_for_window, previous_start, previous_end)
            ga4_year_future = pool.submit(load_ga4_traffic_for_window, year_start, year_end)
            ga4_channels_future = pool.submit(load_ga4_channels_for_window, current_start, today)
            ga4_lookback = ga4_lookback_future.result()
            ga4_previous = ga4_previous_future.result()
            ga4_year = ga4_year_future.result()
            ga4_channels = ga4_channels_future.result()
    else:
        ga4_lookback = load_ga4_traffic_for_window(lookback_start, today)
        ga4_previous = load_ga4_traffic_for_window(previous_start, previous_end)
        ga4_year = load_ga4_traffic_for_window(year_start, year_end)
        ga4_channels = load_ga4_channels_for_window(current_start, today)
    ga4_current = Ga4TrafficResult(
        rows=filter_ga4_rows_by_window(ga4_lookback.rows, current_start, today),
        error=ga4_lookback.error,
    )
    lookback_ga4_rows = apply_ga4_conversion_mode(
        ga4_lookback.rows,
        lookback_orders,
        ga4_config.conversion_mode,
    )
    current_ga4_rows = apply_ga4_conversion_mode(
        ga4_current.rows,
        current_orders,
        ga4_config.conversion_mode,
    )
    previous_ga4_rows = apply_ga4_conversion_mode(
        ga4_previous.rows,
        previous_orders,
        ga4_config.conversion_mode,
    )
    year_ga4_rows = apply_ga4_conversion_mode(
        ga4_year.rows,
        year_orders,
        ga4_config.conversion_mode,
    )
    lookback_traffic = merge_traffic_series(lookback_traffic, lookback_ga4_rows)
    current_traffic = merge_traffic_series(current_traffic, current_ga4_rows)
    previous_traffic = merge_traffic_series(previous_traffic, previous_ga4_rows)
    year_traffic = merge_traffic_series(year_traffic, year_ga4_rows)

    current_kpis = calculate_kpis(
        current_orders,
        products,
        current_traffic,
        conversion_traffic_field=client.config.conversion_traffic_field,
        conversion_override=ga4_conversion_rate(current_ga4_rows, ga4_config.conversion_mode),
    )
    previous_kpis = calculate_kpis(
        previous_orders,
        products,
        previous_traffic,
        conversion_traffic_field=client.config.conversion_traffic_field,
        conversion_override=ga4_conversion_rate(previous_ga4_rows, ga4_config.conversion_mode),
    )
    year_kpis = calculate_kpis(
        year_orders,
        products,
        year_traffic,
        conversion_traffic_field=client.config.conversion_traffic_field,
        conversion_override=ga4_conversion_rate(year_ga4_rows, ga4_config.conversion_mode),
    )
    series = build_series(current_start, today, current_orders, current_traffic)
    lookback_series = build_series(lookback_start, today, lookback_orders, lookback_traffic)
    ga4_channel_rows = apply_ga4_channel_filter(ga4_channels.rows, normalized_filters)
    channels = build_channels(current_orders, ga4_channel_rows)
    campaigns = build_campaign_breakdown(current_orders)
    attribution_diagnostics = build_attribution_diagnostics(current_orders)
    cost_config = CostConfig.from_env()
    ad_spend = load_ad_spend_from_env()
    profit = build_profit_summary(current_orders, channels, cost_config, ad_spend)
    daily_summary = build_daily_operating_summary(
        today,
        lookback_orders,
        products,
        cost_config,
        ad_spend,
        spend_window_days=days,
    )
    ad_performance = build_ad_performance(channels, ad_spend)
    customers = build_customer_summary(current_orders)
    order_status = build_order_status_summary(current_orders)
    product_rows = build_product_rows(current_orders, products)
    source_mode = source_mode_from_results(
        current_orders_result, previous_orders_result, products_result
    )
    errors = [
        error
        for error in (
            current_orders_result.get("error"),
            current_orders_result.get("attributionError"),
            previous_orders_result.get("error"),
            previous_orders_result.get("attributionError"),
            year_orders_result.get("error"),
            year_orders_result.get("attributionError"),
            products_result.get("error"),
            ga4_lookback.error,
            ga4_current.error,
            ga4_previous.error,
            ga4_year.error,
            ga4_channels.error,
        )
        if error
    ]
    currency = detect_currency(current_orders, products, client.config.default_currency)
    ga4_status = build_ga4_status(
        ga4_config,
        current_ga4_rows,
        ga4_channels.rows,
        errors=[ga4_current.error, ga4_channels.error],
        start=current_start,
        end=today,
        timezone_name=client.config.timezone_name,
    )
    reconciliation = build_data_reconciliation(
        current_orders,
        current_ga4_rows,
        ga4_status=ga4_status,
    )
    sync_quality = build_sync_quality(
        current_orders_result,
        current_orders,
        products_result,
        channels,
        errors,
    )
    alerts = build_alerts_v2(
        current_kpis,
        current_orders,
        products,
        source_mode,
        errors,
        previous_kpis=previous_kpis,
        product_rows=product_rows,
        ad_performance=ad_performance,
        order_status=order_status,
        series=series,
    )
    alert_delivery = deliver_alert_webhook(alerts, force_refresh=force_refresh)

    result = {
        "range": {
            "key": range_key if range_key in SUPPORTED_RANGES else "7d",
            "days": days,
            "start": current_start.isoformat(),
            "end": today.isoformat(),
        },
        "source": {
            "mode": source_mode,
            "label": source_label(source_mode),
            "syncedAt": now_iso(timezone_name=client.config.timezone_name),
            "errors": errors,
            "cached": False,
            "loadMs": round_number((time_module.monotonic() - started_at) * 1000),
        },
        "currency": currency,
        "filters": {
            "active": normalized_filters,
            "options": filter_options,
        },
        "kpis": {
            "revenue": kpi_item(
                "销售额", current_kpis["revenue"], previous_kpis["revenue"], "currency"
            ),
            "orders": kpi_item("订单数", current_kpis["orders"], previous_kpis["orders"], "number"),
            "conversion": kpi_item(
                "转化率",
                current_kpis["conversion"],
                previous_kpis["conversion"],
                "percent",
                note=conversion_note(current_kpis["conversion"]),
            ),
            "aov": kpi_item("客单价", current_kpis["aov"], previous_kpis["aov"], "currency"),
            "units": kpi_item("售出件数", current_kpis["units"], previous_kpis["units"], "number"),
            "lowStock": kpi_item("低库存 SKU", current_kpis["lowStock"], previous_kpis["lowStock"], "number"),
        },
        "series": series,
        "analytics": build_chart_analytics(
            current_kpis,
            previous_kpis,
            year_kpis,
            lookback_series,
            current_orders,
            current_ga4_rows,
        ),
        "channels": channels,
        "channelAnalytics": build_channel_analytics(current_orders, channels, ga4_channel_rows),
        "campaigns": campaigns,
        "attributionDiagnostics": attribution_diagnostics,
        "reconciliation": reconciliation,
        "ga4Status": ga4_status,
        "syncQuality": sync_quality,
        "profit": profit,
        "dailySummary": daily_summary,
        "adPerformance": ad_performance,
        "customers": customers,
        "orderStatus": order_status,
        "products": product_rows,
        "orders": build_recent_orders(filter_orders_by_window(current_orders, today, today)),
        "alerts": alerts,
        "alertConfig": load_alert_thresholds(),
        "alertDelivery": alert_delivery,
        "events": build_events(source_mode, current_kpis, errors, client.config.timezone_name),
        "connector": client.connector_status(),
    }
    if cache_enabled:
        set_dashboard_cache(cache_key, result)
    return result


def resolve_range_days(range_key: str) -> int:
    return SUPPORTED_RANGES.get(range_key, SUPPORTED_RANGES["7d"])


def source_mode_from_results(*results: dict[str, Any]) -> str:
    sources = {result.get("source") for result in results}
    if sources == {"live"}:
        return "live"
    if "stale" in sources:
        return "stale"
    if sources == {"error"}:
        return "error"
    if "live" in sources:
        return "mixed"
    return "sample"


def source_label(mode: str) -> str:
    return {
        "live": "实时接口数据",
        "stale": "上次成功的真实数据",
        "error": "实时接口暂不可用",
        "mixed": "混合数据",
        "sample": "示例数据",
    }.get(mode, "示例数据")


def normalize_dashboard_filters(filters: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(filters.get(key) or "").strip()
        for key in ("channel", "status", "market", "product")
    }


def dashboard_cache_key(range_key: str, today: date, filters: dict[str, str]) -> str:
    return json.dumps(
        {
            "range": range_key if range_key in SUPPORTED_RANGES else "7d",
            "date": today.isoformat(),
            "filters": filters,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def dashboard_cache_seconds() -> int:
    try:
        return max(0, min(3600, int(os.getenv("DASHBOARD_CACHE_SECONDS", DEFAULT_DASHBOARD_CACHE_SECONDS))))
    except ValueError:
        return DEFAULT_DASHBOARD_CACHE_SECONDS


def get_dashboard_cache(key: str) -> dict[str, Any] | None:
    ttl = dashboard_cache_seconds()
    if ttl <= 0:
        return None
    with _DASHBOARD_CACHE_LOCK:
        cached = _DASHBOARD_CACHE.get(key)
        if not cached:
            return None
        created_at, payload = cached
        if time_module.monotonic() - created_at > ttl:
            _DASHBOARD_CACHE.pop(key, None)
            return None
        return copy.deepcopy(payload)


def set_dashboard_cache(key: str, payload: dict[str, Any]) -> None:
    if dashboard_cache_seconds() <= 0:
        return
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[key] = (time_module.monotonic(), copy.deepcopy(payload))
        if len(_DASHBOARD_CACHE) > 64:
            oldest_key = min(_DASHBOARD_CACHE, key=lambda item: _DASHBOARD_CACHE[item][0])
            _DASHBOARD_CACHE.pop(oldest_key, None)


def clear_dashboard_cache() -> None:
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE.clear()


def get_data_cache(key: str) -> Any | None:
    ttl = dashboard_cache_seconds()
    if ttl <= 0:
        return None
    with _DATA_CACHE_LOCK:
        cached = _DATA_CACHE.get(key)
        if not cached:
            return None
        created_at, value = cached
        if time_module.monotonic() - created_at > ttl:
            _DATA_CACHE.pop(key, None)
            return None
        return copy.deepcopy(value)


def set_data_cache(key: str, value: Any) -> None:
    created_at = time_module.monotonic()
    with _DATA_CACHE_LOCK:
        _LAST_GOOD_DATA[key] = (created_at, copy.deepcopy(value))
        if dashboard_cache_seconds() > 0:
            _DATA_CACHE[key] = (created_at, copy.deepcopy(value))
            if len(_DATA_CACHE) > 128:
                oldest_key = min(_DATA_CACHE, key=lambda item: _DATA_CACHE[item][0])
                _DATA_CACHE.pop(oldest_key, None)
        if len(_LAST_GOOD_DATA) > 128:
            oldest_key = min(_LAST_GOOD_DATA, key=lambda item: _LAST_GOOD_DATA[item][0])
            _LAST_GOOD_DATA.pop(oldest_key, None)


def get_last_good_data(key: str) -> Any | None:
    with _DATA_CACHE_LOCK:
        cached = _LAST_GOOD_DATA.get(key)
        return copy.deepcopy(cached[1]) if cached else None


def clear_data_cache() -> None:
    with _DATA_CACHE_LOCK:
        _DATA_CACHE.clear()
        _LAST_GOOD_DATA.clear()


def clear_live_data_cache(today: date) -> None:
    """Invalidate cache entries whose query window ends on the live store date."""
    date_suffix = f":{today.isoformat()}"
    with _DATA_CACHE_LOCK:
        stale_keys = [key for key in _DATA_CACHE if key.endswith(date_suffix)]
        for key in stale_keys:
            _DATA_CACHE.pop(key, None)


def build_order_query_params(
    start: date,
    end: date,
    limit: int = ORDER_PAGE_LIMIT,
    timezone_name: str | None = None,
) -> dict[str, str]:
    safe_limit = max(1, min(limit, ORDER_PAGE_LIMIT))
    return {
        "limit": str(safe_limit),
        "status": "any",
        "hidden_order": "false",
        "sort_condition": "order_at:desc",
        "created_at_min": local_midnight(start, timezone_name).isoformat(timespec="seconds"),
        "created_at_max": local_end_of_day(end, timezone_name).isoformat(timespec="seconds"),
    }


def next_page_info_from_link(link_header: str) -> str:
    if not link_header:
        return ""
    for part in link_header.split(","):
        if 'rel="next"' not in part and "rel=next" not in part:
            continue
        match = re.search(r"<([^>]+)>", part)
        if not match:
            continue
        query = urllib.parse.parse_qs(urllib.parse.urlparse(match.group(1)).query)
        values = query.get("page_info")
        if values:
            return values[0]
    return ""


def kpi_item(
    label: str,
    value: float | None,
    previous: float | None,
    value_type: str,
    note: str = "",
) -> dict[str, Any]:
    delta = calculate_delta(value, previous)
    return {
        "label": label,
        "value": round_number(value) if value is not None else None,
        "previous": round_number(previous) if previous is not None else None,
        "delta": round(delta, 1) if delta is not None else None,
        "type": value_type,
        "tone": "neutral" if delta is None else "positive" if delta >= 0 else "negative",
        "note": note,
    }


def calculate_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / abs(previous)) * 100


def calculate_kpis(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    traffic: list[dict[str, Any]],
    conversion_traffic_field: str = DEFAULT_CONVERSION_TRAFFIC_FIELD,
    conversion_override: float | None = None,
) -> dict[str, float | None]:
    revenue = sum(float(order.get("total", 0)) for order in orders)
    order_count = len(orders)
    conversion = (
        conversion_override
        if conversion_override is not None
        else calculate_conversion_rate(orders, traffic, conversion_traffic_field)
    )
    units = sum(int(order.get("units", 0)) for order in orders)
    low_stock = sum(1 for product in products if int(product.get("inventory", 0)) <= 5)
    return {
        "revenue": revenue,
        "orders": float(order_count),
        "conversion": conversion,
        "aov": (revenue / order_count) if order_count else 0.0,
        "units": float(units),
        "lowStock": float(low_stock),
    }


def calculate_conversion_rate(
    orders: list[dict[str, Any]],
    traffic: list[dict[str, Any]],
    traffic_field: str = DEFAULT_CONVERSION_TRAFFIC_FIELD,
) -> float | None:
    field = normalize_traffic_field(traffic_field)
    rows = [row for row in traffic if isinstance(row.get(field), (int, float))]
    denominator = sum(float(row.get(field) or 0) for row in rows)
    if denominator <= 0:
        return None

    traffic_dates = {str(row.get("date")) for row in rows}
    matching_orders = [
        order
        for order in orders
        if str(order.get("createdAt", ""))[:10] in traffic_dates
    ]
    return len(matching_orders) / denominator * 100


def normalize_shopline_orders(
    payload: Any,
    default_currency: str = DEFAULT_CURRENCY,
    attribution_by_order_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    default_currency = (default_currency or DEFAULT_CURRENCY).upper()
    attribution_by_order_id = attribution_by_order_id or {}
    records = extract_collection(payload, ["orders", "order_list", "items", "data", "results"])
    orders = []
    for index, record in enumerate(records, start=1):
        if isinstance(record, dict):
            order_id = extract_raw_order_id(record, index)
            orders.append(
                normalize_order(
                    record,
                    index,
                    default_currency,
                    attribution_info=attribution_by_order_id.get(order_id),
                )
            )
    return orders


def extract_raw_order_id(order: dict[str, Any], index: int = 0) -> str:
    fallback = f"sample-{index}" if index else ""
    return str(
        pick(order, "id", "order_id", "orderNo", "order_no", "name", default=fallback) or fallback
    ).strip()


def deduplicate_orders(orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for index, order in enumerate(orders):
        order_id = str(order.get("id") or "").strip()
        key = order_id or f"{order.get('createdAt')}:{order.get('total')}:{index}"
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        unique.append(order)
    return unique, duplicate_count


def normalize_shopline_products(
    payload: Any,
    default_currency: str = DEFAULT_CURRENCY,
) -> list[dict[str, Any]]:
    default_currency = (default_currency or DEFAULT_CURRENCY).upper()
    records = extract_collection(payload, ["products", "product_list", "items", "data", "results"])
    products = []
    for index, record in enumerate(records, start=1):
        if isinstance(record, dict):
            products.append(normalize_product(record, index, default_currency))
    return products


def normalize_order(
    order: dict[str, Any],
    index: int,
    default_currency: str = DEFAULT_CURRENCY,
    attribution_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    customer_profile = normalize_customer_profile(order)
    line_items = pick(order, "line_items", "items", "products", "order_items", default=[])
    if not isinstance(line_items, list):
        line_items = []

    normalized_items = []
    for item in line_items:
        if not isinstance(item, dict):
            continue
        quantity = parse_int(pick(item, "quantity", "qty", "count", default=1), default=1)
        price = parse_amount(pick(item, "price", "unit_price", "sale_price", "amount", default=0))
        title = str(pick(item, "title", "name", "product_title", "product_name", default="Unknown item"))
        sku = str(pick(item, "sku", "variant_sku", "product_sku", default=""))
        normalized_items.append(
            {
                "title": title,
                "sku": sku,
                "quantity": quantity,
                "price": round_number(price),
                "revenue": round_number(price * quantity),
            }
        )

    total = parse_amount(
        pick(
            order,
            "total_price",
            "current_total_price",
            "total_amount",
            "amount",
            "total",
            "subtotal_price",
            default=0,
        )
    )
    if total == 0 and normalized_items:
        total = sum(float(item["revenue"]) for item in normalized_items)

    order_date = parse_date(
        pick(
            order,
            "created_at",
            "createdAt",
            "order_at",
            "orderAt",
            "created_time",
            "order_time",
            "paid_at",
        )
    ) or current_dashboard_date()

    official_tracking = extract_shopline_attribution_tracking(attribution_info)
    attribution = extract_order_attribution(order, attribution_info=attribution_info)
    click_ids = extract_order_click_ids(order)
    click_campaign = resolve_click_campaign_mapping(click_ids)
    source_campaign_id = (
        tracking_value(order, "campaign_id", "sl_source_campaign_id")
        or click_campaign.get("campaignId", "")
    )
    source_campaign = (
        attribution["campaign"]
        or click_campaign.get("campaignName", "")
        or source_campaign_id
    )

    return {
        "id": str(
            extract_raw_order_id(order, index)
        ),
        "createdAt": order_date.isoformat(),
        "total": round_number(total),
        "currency": str(
            pick(order, "currency", "currency_code", "presentment_currency", default=default_currency)
        ).upper(),
        "status": str(
            pick(order, "financial_status", "payment_status", "status", "order_status", default="paid")
        ),
        "fulfillmentStatus": str(
            pick(order, "fulfillment_status", "shipping_status", "delivery_status", default="unfulfilled")
        ),
        "source": attribution["source"],
        "sourceRaw": attribution["raw"] or attribution["source"],
        "sourceUtm": official_tracking.get("utm_source") or tracking_value(order, "utm_source"),
        "sourceMedium": attribution["medium"],
        "sourceCampaign": source_campaign,
        "sourceCampaignId": source_campaign_id,
        "sourceAdset": tracking_value(order, "utm_adset", "adset_id", "sl_source_adset_id"),
        "sourceAd": tracking_value(order, "utm_ad", "ad_id", "utm_term", "sl_source_ad_id"),
        "sourceContent": official_tracking.get("utm_content") or tracking_value(order, "utm_content", "content"),
        "sourceTerm": official_tracking.get("utm_term") or tracking_value(order, "utm_term", "term"),
        "clickIds": click_ids,
        "clickCampaignMapped": bool(click_campaign),
        "attributionMethod": attribution["method"],
        "attributionConfidence": attribution["confidence"],
        "market": normalize_order_market(order),
        "subtotal": round_number(parse_amount(pick(order, "subtotal_price", "current_subtotal_price", default=total))),
        "discounts": round_number(parse_amount(pick(order, "total_discounts", "current_total_discounts", default=0))),
        "refundTotal": round_number(extract_refund_total(order)),
        "taxTotal": round_number(parse_amount(pick(order, "total_tax", "current_total_tax", default=0))),
        "shippingTotal": round_number(extract_shipping_total(order)),
        "customer": customer_profile["name"],
        "customerId": customer_profile["id"],
        "customerEmail": customer_profile["email"],
        "customerPhone": customer_profile["phone"],
        "customerKey": customer_profile["key"],
        "units": sum(int(item["quantity"]) for item in normalized_items) or 1,
        "items": normalized_items,
    }


def normalize_customer_profile(order: dict[str, Any]) -> dict[str, str]:
    customer = first_dict(
        order.get("customer"),
        order.get("customerInfo"),
        order.get("customer_info"),
        order.get("buyer"),
        order.get("buyerInfo"),
        order.get("buyer_info"),
        order.get("user"),
    )
    first_name = str(pick(customer, "first_name", "firstName", default="") or "").strip()
    last_name = str(pick(customer, "last_name", "lastName", default="") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    name = str(
        pick(
            customer,
            "name",
            "full_name",
            "fullName",
            "nickname",
            default=pick(order, "customer_name", "customerName", "buyer_name", default=full_name),
        )
        or ""
    ).strip()
    email = str(
        pick(
            customer,
            "email",
            "customer_email",
            "email_address",
            default=pick(order, "email", "customer_email", "customerEmail", "buyer_email", default=""),
        )
        or ""
    ).strip()
    phone = str(
        pick(
            customer,
            "phone",
            "mobile",
            "telephone",
            "customer_phone",
            default=pick(order, "phone", "mobile", "customer_phone", "customerPhone", "buyer_phone", default=""),
        )
        or ""
    ).strip()
    customer_id = str(
        pick(
            customer,
            "id",
            "customer_id",
            "customerId",
            "buyer_id",
            "user_id",
            default=pick(order, "customer_id", "customerId", "buyer_id", "user_id", default=""),
        )
        or ""
    ).strip()

    if not name:
        name = email or phone or "Guest"

    key = customer_id or email.lower() or normalize_phone_key(phone) or name.lower()
    if key == "guest":
        key = ""

    return {
        "id": customer_id,
        "email": email,
        "phone": phone,
        "name": name,
        "key": key,
    }


def normalize_product(
    product: dict[str, Any],
    index: int,
    default_currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any]:
    variants = pick(product, "variants", "skus", default=[])
    first_variant = variants[0] if isinstance(variants, list) and variants else {}
    if not isinstance(first_variant, dict):
        first_variant = {}

    inventory = pick(
        product,
        "inventory_quantity",
        "inventory",
        "stock",
        "quantity",
        default=pick(first_variant, "inventory_quantity", "inventory", "stock", default=0),
    )
    price = pick(
        product,
        "price",
        "sale_price",
        "min_price",
        default=pick(first_variant, "price", "sale_price", default=0),
    )

    return {
        "id": str(pick(product, "id", "product_id", "spu_id", default=f"product-{index}")),
        "title": str(pick(product, "title", "name", "product_title", default=f"Product {index}")),
        "sku": str(
            pick(product, "sku", "product_sku", default=pick(first_variant, "sku", "variant_sku", default=""))
        ),
        "category": str(pick(product, "category", "product_type", "vendor", default="General")),
        "price": round_number(parse_amount(price)),
        "currency": str(pick(product, "currency", "currency_code", default=default_currency)).upper(),
        "inventory": parse_int(inventory),
        "status": str(pick(product, "status", "state", default="active")),
        "updatedAt": (
            parse_date(pick(product, "updated_at", "updatedAt", "created_at"))
            or current_dashboard_date()
        ).isoformat(),
    }


def normalize_marketing_source(source: Any) -> str:
    raw = str(source or "").strip()
    if not raw:
        return "Direct"

    compact = re.sub(r"[\s\-_./]+", " ", raw.lower()).strip()
    tokens = set(compact.split())

    if "facebook" in compact or "meta" in tokens or "fb" in tokens:
        return "Facebook"
    if "instagram" in compact or "insta" in tokens or "ins" in tokens or "ig" in tokens:
        return "Instagram"
    if "google" in compact or "gads" in tokens or "gdn" in tokens or "search" in tokens:
        return "Google"
    if "tiktok" in compact or "tik tok" in compact or "tt" in tokens:
        return "TikTok"
    if "youtube" in compact or "youtu be" in compact or "youtu" in tokens:
        return "YouTube"
    if "line" in tokens or "line ads" in compact or "line me" in compact:
        return "LINE"
    if "twitter" in compact or compact == "x" or "x.com" in compact:
        return "X / Twitter"
    if "pinterest" in compact or "pinterest" in tokens:
        return "Pinterest"
    if "bing" in compact or "microsoft ads" in compact:
        return "Bing"
    if "yahoo" in compact:
        return "Yahoo"
    if "smartpush" in compact or "smart push" in compact:
        return "SmartPush"
    if "email" in compact or "newsletter" in compact or "edm" in tokens:
        return "Email"
    if any(label in compact for label in ("sms", "web push", "notification")):
        return "SMS / Push"
    if any(label in compact for label in ("chatgpt", "openai", "perplexity", "gemini", "copilot")):
        return "AI / Chat"
    if "affiliate" in compact or "affiliates" in compact or "partner" in tokens:
        return "Affiliate"
    if "referral" in compact or "referrer" in compact:
        return "Referral"
    if "organic" in compact or "natural" in compact or "seo" in tokens:
        return "Organic"
    if "direct" in compact or "(direct)" in compact or "shopline" in compact or "website" in compact:
        return "Direct"
    if any(token in tokens for token in {"ad", "ads", "paid", "cpc", "ppc", "campaign"}):
        return "Ad"

    return raw[:1].upper() + raw[1:] if raw[:1].islower() else raw


def normalize_shopline_attribution_source(source: Any) -> str:
    raw = str(source or "").strip()
    compact = re.sub(r"[\s\-_./]+", "", raw.lower())
    if compact == "googleads":
        return "Google Ads"
    return normalize_marketing_source(raw)


def normalize_phone_key(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    return digits[-12:] if digits else ""


def extract_order_traffic_source(order: dict[str, Any]) -> tuple[str, str]:
    attribution = extract_order_attribution(order)
    return attribution["source"], attribution["raw"]


def extract_shopline_attribution_tracking(
    attribution_info: dict[str, Any] | None,
) -> dict[str, str]:
    if not isinstance(attribution_info, dict):
        return {}
    interaction = first_dict(
        attribution_info.get("last_interaction"),
        attribution_info.get("lastInteraction"),
        attribution_info,
    )
    parameters = first_dict(
        interaction.get("last_utm_parameters"),
        interaction.get("lastUtmParameters"),
    )
    candidates = {
        "utm_source": ("last_utm_source", "lastUtmSource", "utm_source"),
        "utm_medium": ("last_utm_medium", "lastUtmMedium", "utm_medium"),
        "utm_campaign": (
            "last_utm_campaign",
            "lastUtmCampaign",
            "last_utm_name",
            "lastUtmName",
            "utm_campaign",
        ),
        "utm_content": ("last_utm_content", "lastUtmContent", "utm_content"),
        "utm_term": ("last_utm_term", "lastUtmTerm", "utm_term"),
    }
    tracking: dict[str, str] = {}
    for key, source_keys in candidates.items():
        value = str(pick(parameters, *source_keys, default="") or "").strip()
        if value:
            tracking[key] = value
    return tracking


def extract_shopline_last_touch_attribution(
    attribution_info: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not isinstance(attribution_info, dict):
        return None
    interaction = first_dict(
        attribution_info.get("last_interaction"),
        attribution_info.get("lastInteraction"),
    )
    if not interaction:
        return None

    source_name = str(
        pick(interaction, "last_referrer_name", "lastReferrerName", default="") or ""
    ).strip()
    source_url = str(
        pick(interaction, "last_referrer_url", "lastReferrerUrl", default="") or ""
    ).strip()
    landing_page = str(
        pick(interaction, "last_landing_page", "lastLandingPage", default="") or ""
    ).strip()
    interaction_type = str(
        pick(interaction, "last_interaction_source", "lastInteractionSource", default="") or ""
    ).strip()
    tracking = extract_shopline_attribution_tracking(attribution_info)

    source = normalize_shopline_attribution_source(source_name) if source_name else ""
    if source not in TRAFFIC_SOURCE_BUCKETS:
        source = infer_traffic_source_from_url(source_url or landing_page)
    if source not in TRAFFIC_SOURCE_BUCKETS:
        source = normalize_marketing_source(
            f"{tracking.get('utm_source', '')} {tracking.get('utm_medium', '')}".strip()
        )
    if source not in TRAFFIC_SOURCE_BUCKETS:
        source = {
            "direct": "Direct",
            "search": "Organic",
            "social": "Referral",
            "other": "Other",
            "ai chat": "AI / Chat",
        }.get(interaction_type.lower(), "Other")

    return attribution_result(
        source,
        source_name or source_url or landing_page or source,
        tracking.get("utm_medium", ""),
        tracking.get("utm_campaign", ""),
        "shopline_attribution",
        "high",
    )


def extract_order_attribution(
    order: dict[str, Any],
    attribution_info: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve last-touch attribution from Shopline's flat, JSON and URL fields."""
    official_attribution = extract_shopline_last_touch_attribution(attribution_info)
    if official_attribution:
        return official_attribution

    source_url = str(pick(order, "source_url", default="") or "").strip()
    referring_site = str(pick(order, "referring_site", default="") or "").strip()
    landing_site = str(pick(order, "landing_site", default="") or "").strip()
    url_values = [value for value in (source_url, referring_site, landing_site) if value]

    nested_utm = parse_utm_parameters(order.get("utm_parameters"))
    query_utm: dict[str, str] = {}
    for url_value in url_values:
        for key, value in extract_url_tracking_values(url_value).items():
            if value and key not in query_utm:
                query_utm[key] = value

    explicit_utm = {
        key: str(pick(order, key, camelize_key(key), default="") or "").strip()
        for key in (
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
            "campaign_id",
            "adset_id",
            "ad_id",
        )
    }
    tracking = dict(query_utm)
    tracking.update({key: value for key, value in nested_utm.items() if value})
    tracking.update({key: value for key, value in explicit_utm.items() if value})

    utm_source = tracking.get("utm_source", "")
    utm_medium = tracking.get("utm_medium", "")
    campaign = (
        tracking.get("utm_campaign")
        or tracking.get("sl_source_campaign_id")
        or tracking.get("campaign_id")
    )

    # Meta's click id is shared by Facebook and Instagram. The actual social
    # referrer is the best discriminator when Shopline captured it.
    referrer_source = infer_referrer_source(referring_site or source_url)
    if referrer_source in {"Facebook", "Instagram"}:
        return attribution_result(
            referrer_source,
            referring_site or source_url,
            utm_medium,
            campaign,
            "referrer",
            "high",
        )

    click_source = infer_click_id_source(tracking)
    if click_source:
        return attribution_result(
            click_source,
            landing_site or source_url or referring_site,
            utm_medium,
            campaign,
            "click_id",
            "high",
        )

    combined_source = normalize_marketing_source(f"{utm_source} {utm_medium}".strip())
    if combined_source in TRAFFIC_SOURCE_BUCKETS and combined_source not in {"Direct", "Ad", "Other"}:
        return attribution_result(
            combined_source,
            f"{utm_source} / {utm_medium}".strip(" /"),
            utm_medium,
            campaign,
            "utm",
            "high",
        )

    for field, value in (
        ("source_url", source_url),
        ("referring_site", referring_site),
        ("landing_site", landing_site),
    ):
        if field != "landing_site" and same_url_host(value, landing_site):
            continue
        source = infer_traffic_source(field, value) if value else ""
        if field == "landing_site" and source == "Referral":
            source = ""
        if source:
            confidence = "medium" if source not in {"Direct", "Ad"} else "low"
            return attribution_result(source, value, utm_medium, campaign, field, confidence)

    for field in ("source_identifier", "source", "channel", "sales_channel", "source_name"):
        value = str(pick(order, field, camelize_key(field), default="") or "").strip()
        source = infer_traffic_source(field, value) if value else ""
        if source:
            return attribution_result(source, value, utm_medium, campaign, field, "low")

    raw = source_url or referring_site or landing_site or "Direct"
    return attribution_result("Direct", raw, utm_medium, campaign, "fallback", "low")


def attribution_result(
    source: str,
    raw: str,
    medium: str,
    campaign: str,
    method: str,
    confidence: str,
) -> dict[str, str]:
    return {
        "source": source if source in TRAFFIC_SOURCE_BUCKETS else "Other",
        "raw": str(raw or "").strip(),
        "medium": str(medium or "").strip(),
        "campaign": str(campaign or "").strip(),
        "method": method,
        "confidence": confidence,
    }


def order_chunk_days() -> int:
    try:
        return max(1, min(31, int(os.getenv("SHOPLINE_ORDER_CHUNK_DAYS", "7"))))
    except ValueError:
        return 7


def build_date_chunks(start: date, end: date, chunk_days: int = 7) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    size = max(1, chunk_days)
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=size - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def camelize_key(key: str) -> str:
    first, *rest = key.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def parse_utm_parameters(value: Any) -> dict[str, str]:
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = dict(urllib.parse.parse_qsl(text.lstrip("?"), keep_blank_values=False))
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key).strip().lower(): str(raw_value or "").strip()
        for key, raw_value in parsed.items()
        if str(raw_value or "").strip()
    }


def tracking_value(order: dict[str, Any], *keys: str) -> str:
    nested = parse_utm_parameters(order.get("utm_parameters"))
    urls = [
        str(order.get(field) or "").strip()
        for field in ("source_url", "referring_site", "landing_site")
    ]
    for key in keys:
        direct = str(pick(order, key, camelize_key(key), default="") or "").strip()
        if direct:
            return direct
        if nested.get(key):
            return nested[key]
        for url_value in urls:
            value = extract_url_tracking_values(url_value).get(key)
            if value:
                return value
    return ""


def extract_order_click_ids(order: dict[str, Any]) -> dict[str, str]:
    return {
        key: value
        for key in CLICK_ID_KEYS
        if (value := tracking_value(order, key))
    }


def load_click_campaign_map_from_env() -> dict[str, dict[str, str]]:
    global _CLICK_CAMPAIGN_MAP_CACHE

    raw = os.getenv("SHOPLINE_CLICK_CAMPAIGN_MAP_JSON", "").strip()
    if raw == _CLICK_CAMPAIGN_MAP_CACHE[0]:
        return _CLICK_CAMPAIGN_MAP_CACHE[1]

    mappings: dict[str, dict[str, str]] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for raw_key, raw_value in parsed.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                key_type = key.lower()
                is_nested = (
                    key_type in CLICK_ID_KEYS
                    and isinstance(raw_value, dict)
                    and not any(
                        field in raw_value
                        for field in ("campaignId", "campaign_id", "campaignName", "campaign_name", "name", "campaign")
                    )
                )
                if is_nested:
                    for click_id, campaign_value in raw_value.items():
                        mapping = normalize_click_campaign_entry(campaign_value)
                        if mapping and str(click_id or "").strip():
                            mappings[f"{key_type}:{str(click_id).strip()}"] = mapping
                    continue
                mapping = normalize_click_campaign_entry(raw_value)
                if not mapping:
                    continue
                if ":" in key:
                    prefix, click_id = key.split(":", 1)
                    normalized_key = f"{prefix.strip().lower()}:{click_id.strip()}"
                else:
                    normalized_key = key
                mappings[normalized_key] = mapping

    _CLICK_CAMPAIGN_MAP_CACHE = (raw, mappings)
    return mappings


def normalize_click_campaign_entry(value: Any) -> dict[str, str]:
    if isinstance(value, str) or isinstance(value, (int, float)):
        text = str(value).strip()
        return {"campaignId": text, "campaignName": text} if text else {}
    if not isinstance(value, dict):
        return {}
    campaign_id = str(
        pick(value, "campaignId", "campaign_id", "id", default="") or ""
    ).strip()
    campaign_name = str(
        pick(value, "campaignName", "campaign_name", "campaign", "name", default="") or ""
    ).strip()
    if not campaign_id and not campaign_name:
        return {}
    return {
        "campaignId": campaign_id,
        "campaignName": campaign_name or campaign_id,
    }


def resolve_click_campaign_mapping(
    click_ids: dict[str, str],
    mappings: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    mappings = mappings if mappings is not None else load_click_campaign_map_from_env()
    for click_type, click_id in click_ids.items():
        mapping = mappings.get(f"{click_type.lower()}:{click_id}") or mappings.get(click_id)
        if mapping:
            return {
                **mapping,
                "clickType": click_type.lower(),
                "clickId": click_id,
            }
    return {}


def normalize_order_market(order: dict[str, Any]) -> str:
    shipping = first_dict(order.get("shipping_address"), order.get("shippingAddress"))
    billing = first_dict(order.get("billing_address"), order.get("billingAddress"))
    value = pick(
        order,
        "country_code",
        "country",
        "market",
        "shipping_country",
        default=pick(
            shipping,
            "country_code",
            "countryCode",
            "country",
            default=pick(billing, "country_code", "countryCode", "country", default="Online"),
        ),
    )
    return str(value or "Online").strip() or "Online"


def extract_refund_total(order: dict[str, Any]) -> float:
    refunds = order.get("refunds")
    if not isinstance(refunds, list):
        return 0.0
    total = 0.0
    for refund in refunds:
        if not isinstance(refund, dict):
            continue
        amount = pick(refund, "amount", "refund_amount", "total", default=None)
        if amount is not None:
            total += parse_amount(amount)
            continue
        transactions = refund.get("transactions")
        if isinstance(transactions, list):
            total += sum(
                parse_amount(pick(item, "amount", "total", default=0))
                for item in transactions
                if isinstance(item, dict)
            )
    return total


def extract_shipping_total(order: dict[str, Any]) -> float:
    direct = pick(order, "shipping_price", "total_shipping_price", "shipping_amount", default=None)
    if direct is not None:
        return parse_amount(direct)
    value_set = first_dict(order.get("total_shipping_price_set"), order.get("shipping_price_set"))
    money = first_dict(value_set.get("shop_money"), value_set.get("shopMoney"), value_set)
    return parse_amount(pick(money, "amount", "value", default=0))


def extract_url_tracking_values(url_value: str) -> dict[str, str]:
    parsed = parse_url_value(url_value)
    values = urllib.parse.parse_qs(parsed.query)
    return {
        str(key).lower(): str(raw_values[0]).strip()
        for key, raw_values in values.items()
        if raw_values and str(raw_values[0]).strip()
    }


def infer_click_id_source(tracking: dict[str, str]) -> str:
    if any(tracking.get(key) for key in ("gclid", "dclid", "wbraid", "gbraid", "gad_source")):
        return "Google"
    if tracking.get("ttclid"):
        return "TikTok"
    if tracking.get("msclkid"):
        return "Bing"
    if tracking.get("twclid"):
        return "X / Twitter"
    if tracking.get("epik"):
        return "Pinterest"
    if tracking.get("fbclid"):
        return "Facebook"
    return ""


def infer_referrer_source(url_value: str) -> str:
    if not url_value:
        return ""
    parsed = parse_url_value(url_value)
    host = parsed.netloc.lower().removeprefix("www.")
    if not host:
        return ""
    if "instagram.com" in host:
        return "Instagram"
    if "facebook.com" in host or host.endswith("fb.com"):
        return "Facebook"
    return infer_traffic_source_from_url(url_value)


def infer_traffic_source(field: str, value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    if field == "source_identifier":
        source = normalize_marketing_source(text)
        return source if source in TRAFFIC_SOURCE_BUCKETS else ""

    if field in {"source_url", "referring_site", "landing_site"}:
        source = infer_traffic_source_from_url(text)
        if source:
            return source
        source = normalize_marketing_source(text)
        return source if source in TRAFFIC_SOURCE_BUCKETS else ""

    source = normalize_marketing_source(text)
    return source if source in TRAFFIC_SOURCE_BUCKETS else ""


def infer_traffic_source_from_url(url_value: str) -> str:
    parsed = parse_url_value(url_value)
    host = parsed.netloc.lower()
    query = urllib.parse.parse_qs(parsed.query)

    if any(query.get(key) for key in ("fbclid",)):
        return "Facebook"
    if any(query.get(key) for key in ("igshid", "igsh")):
        return "Instagram"
    if any(query.get(key) for key in ("ttclid",)):
        return "TikTok"
    if any(query.get(key) for key in ("gclid", "dclid", "wbraid", "gbraid")):
        return "Google"
    if any(query.get(key) for key in ("msclkid",)):
        return "Bing"
    if any(query.get(key) for key in ("twclid",)):
        return "X / Twitter"
    if any(query.get(key) for key in ("epik",)):
        return "Pinterest"

    if "facebook.com" in host or host.endswith("fb.com"):
        return "Facebook"
    if "instagram.com" in host:
        return "Instagram"
    if "tiktok.com" in host:
        return "TikTok"
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "line.me" in host:
        return "LINE"
    if "twitter.com" in host or host.endswith("x.com"):
        return "X / Twitter"
    if "pinterest." in host or "pin.it" in host:
        return "Pinterest"
    if "bing.com" in host:
        return "Bing"
    if "yahoo." in host:
        return "Yahoo"
    if "google." in host or "googleadservices.com" in host:
        return "Google"
    if "mailchimp.com" in host or "sendgrid.com" in host or "mail.google.com" in host:
        return "Email"

    query_sources = []
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        for raw_value in query.get(key, []):
            source = normalize_marketing_source(raw_value)
            if source in TRAFFIC_SOURCE_BUCKETS:
                query_sources.append(source)

    for source in query_sources:
        if source not in {"Ad", "Direct"}:
            return source
    if query_sources:
        return query_sources[0]

    if host and not is_internal_store_host(host):
        return "Referral"

    return ""


def parse_url_value(url_value: str) -> urllib.parse.ParseResult:
    text = str(url_value or "").strip()
    if text and "://" not in text and not text.startswith("/"):
        text = f"https://{text}"
    return urllib.parse.urlparse(text)


def is_internal_store_host(host: str) -> bool:
    clean = str(host or "").lower().split(":", 1)[0].removeprefix("www.")
    configured_hosts = {
        urllib.parse.urlparse(os.getenv("SHOPLINE_API_BASE_URL", "")).netloc.lower(),
        os.getenv("SHOPLINE_STORE_DOMAIN", "").strip().lower(),
    }
    configured_hosts.update(
        value.strip().lower()
        for value in re.split(r",|;|\s+", os.getenv("SHOPLINE_STOREFRONT_DOMAINS", ""))
        if value.strip()
    )
    configured_hosts = {value.removeprefix("www.") for value in configured_hosts if value}
    if clean.endswith(".myshopline.com"):
        return True
    return any(clean == value or clean.endswith(f".{value}") for value in configured_hosts)


def same_url_host(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_host = parse_url_value(left).netloc.lower().removeprefix("www.")
    right_host = parse_url_value(right).netloc.lower().removeprefix("www.")
    return bool(left_host and right_host and left_host == right_host)


def extract_collection(payload: Any, preferred_keys: list[str]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_collection(value, preferred_keys)
            if nested:
                return nested

    for key in ("data", "result", "response", "body", "payload"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            nested = extract_collection(value, preferred_keys)
            if nested:
                return nested
    return []


def pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_ad_spend_from_env() -> dict[str, float]:
    spend: dict[str, float] = {}
    raw_json = os.getenv("SHOPLINE_AD_SPEND_JSON", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    source = normalize_marketing_source(key)
                    if source in TRAFFIC_SOURCE_BUCKETS:
                        spend[source] = parse_amount(value)
        except json.JSONDecodeError:
            pass

    for source in TRAFFIC_SOURCE_BUCKETS:
        env_key = f"SHOPLINE_AD_SPEND_{source.upper()}"
        value = os.getenv(env_key, "").strip()
        if value:
            spend[source] = parse_amount(value)
    return spend


def load_sku_costs_from_env() -> dict[str, float]:
    raw = os.getenv("SHOPLINE_SKU_COST_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(sku).strip(): max(0.0, parse_amount(value))
        for sku, value in parsed.items()
        if str(sku).strip()
    }


def load_shipping_costs_from_env() -> dict[str, float]:
    raw = os.getenv("SHOPLINE_SHIPPING_COST_BY_MARKET_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(market).strip().upper(): max(0.0, parse_amount(value))
        for market, value in parsed.items()
        if str(market).strip()
    }


def load_traffic_from_env() -> dict[str, dict[str, int]]:
    traffic: dict[str, dict[str, int]] = {}
    for env_name in (
        "SHOPLINE_TRAFFIC_JSON",
        "SHOPLINE_DAILY_VISITORS_JSON",
        "SHOPLINE_VISITORS_JSON",
    ):
        raw_json = os.getenv(env_name, "").strip()
        if raw_json:
            merge_traffic_json(traffic, raw_json)

    for env_name in ("SHOPLINE_DAILY_SESSIONS_JSON", "SHOPLINE_SESSIONS_JSON"):
        raw_json = os.getenv(env_name, "").strip()
        if raw_json:
            merge_traffic_json(traffic, raw_json, default_field="sessions")

    return traffic


def merge_traffic_json(
    target: dict[str, dict[str, int]],
    raw_json: str,
    default_field: str = "visitors",
) -> None:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return
    if not isinstance(parsed, dict):
        return

    for raw_date, raw_value in parsed.items():
        day = parse_date(raw_date)
        if not day:
            continue
        key = day.isoformat()
        row = target.setdefault(key, {})
        if isinstance(raw_value, dict):
            if "visitors" in raw_value:
                row["visitors"] = max(0, parse_int(raw_value.get("visitors")))
            if "sessions" in raw_value:
                row["sessions"] = max(0, parse_int(raw_value.get("sessions")))
        else:
            row[default_field] = max(0, parse_int(raw_value))


def normalize_traffic_field(value: Any) -> str:
    field = str(value or "").strip().lower()
    return "sessions" if field == "sessions" else "visitors"


def normalize_ga4_metric(value: Any) -> str:
    raw = str(value or "").strip()
    compact = raw.replace("_", "").replace("-", "").lower()
    if compact in {"user", "userkeyeventrate", "userconversionrate"}:
        return "userKeyEventRate"
    if compact in {"session", "sessionkeyeventrate", "sessionconversionrate", "conversionrate"}:
        return "sessionKeyEventRate"
    if raw in {"sessionKeyEventRate", "userKeyEventRate"}:
        return raw
    return DEFAULT_GA4_CONVERSION_METRIC


def normalize_ga4_conversion_mode(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"key_event_rate", "ga4_key_event_rate"}:
        return "key_event_rate"
    if raw in {"shopline_orders_over_sessions", "orders_over_sessions", "shopline_orders"}:
        return "shopline_orders_over_sessions"
    if raw in {"shopline_orders_over_active_users", "orders_over_active_users"}:
        return "shopline_orders_over_active_users"
    if raw in {"shopline_orders_over_total_users", "orders_over_total_users"}:
        return "shopline_orders_over_total_users"
    return DEFAULT_GA4_CONVERSION_MODE


def load_ga4_traffic_for_window(start: date, end: date) -> Ga4TrafficResult:
    config = Ga4Config.from_env()
    if not config.configured:
        return Ga4TrafficResult(rows=[])
    cache_key = f"ga4:traffic:{config.property_id}:{config.metric_name}:{start.isoformat()}:{end.isoformat()}"
    cached = get_data_cache(cache_key)
    if isinstance(cached, dict):
        return Ga4TrafficResult(rows=list(cached.get("rows") or []), error=cached.get("error"))
    try:
        result = Ga4TrafficResult(rows=fetch_ga4_traffic_series(config, start, end))
        set_data_cache(cache_key, {"rows": result.rows, "error": result.error})
        return result
    except Exception as exc:  # pragma: no cover - network and credential specific branch
        return Ga4TrafficResult(rows=[], error=f"GA4: {exc.__class__.__name__}: {exc}")


def load_ga4_channels_for_window(start: date, end: date) -> Ga4ChannelResult:
    config = Ga4Config.from_env()
    if not config.configured:
        return Ga4ChannelResult(rows=[])
    cache_key = f"ga4:channels:{config.property_id}:{config.key_event_name}:{start.isoformat()}:{end.isoformat()}"
    cached = get_data_cache(cache_key)
    if isinstance(cached, dict):
        return Ga4ChannelResult(rows=list(cached.get("rows") or []), error=cached.get("error"))
    try:
        result = Ga4ChannelResult(rows=fetch_ga4_channel_rows(config, start, end))
        set_data_cache(cache_key, {"rows": result.rows, "error": result.error})
        return result
    except Exception as exc:  # pragma: no cover - network and credential specific branch
        return Ga4ChannelResult(rows=[], error=f"GA4 Channels: {exc.__class__.__name__}: {exc}")


def probe_ga4_connection(today: date | None = None) -> dict[str, Any]:
    """Run a small live GA4 report and return actionable connector diagnostics."""
    config = Ga4Config.from_env()
    checked_at = now_iso()
    if not config.configured:
        return {
            "ok": False,
            "configured": False,
            "status": "unconfigured",
            "message": "GA4 配置未完成",
            "rows": 0,
            "sessions": None,
            "purchases": None,
            "conversion": None,
            "checkedAt": checked_at,
        }

    target_date = today or current_dashboard_date()
    try:
        rows = fetch_ga4_traffic_series(config, target_date, target_date)
        sessions = sum(parse_int(row.get("sessions")) for row in rows)
        purchases = sum(parse_optional_float(row.get("keyEvents")) or 0 for row in rows)
        conversion = ga4_conversion_rate(rows, config.conversion_mode)
        return {
            "ok": True,
            "configured": True,
            "status": "ready" if rows else "empty",
            "message": (
                f"GA4 已返回 {round_number(purchases)} 次 Purchase"
                if rows
                else "GA4 已连接，所选日期暂无数据"
            ),
            "rows": len(rows),
            "sessions": sessions if rows else None,
            "purchases": round_number(purchases) if rows else None,
            "conversion": conversion,
            "date": target_date.isoformat(),
            "checkedAt": checked_at,
        }
    except Exception as exc:  # pragma: no cover - network and credential specific branch
        return {
            "ok": False,
            "configured": True,
            "status": "error",
            "message": f"{exc.__class__.__name__}: {exc}",
            "rows": 0,
            "sessions": None,
            "purchases": None,
            "conversion": None,
            "date": target_date.isoformat(),
            "checkedAt": checked_at,
        }


def probe_integrations(today: date | None = None) -> dict[str, Any]:
    """Check Shopline and GA4 in parallel for the dashboard's interface test."""
    client = ShoplineClient()
    target_date = today or current_dashboard_date(client.config.timezone_name)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="connector-probe") as pool:
        shopline_future = pool.submit(client.test_connection)
        ga4_future = pool.submit(probe_ga4_connection, target_date)
        shopline = shopline_future.result()
        ga4 = ga4_future.result()
    ok = bool(shopline.get("ok")) and bool(ga4.get("ok"))
    return {
        "ok": ok,
        "message": "Shopline 与 GA4 均已连接" if ok else "接口检测发现异常",
        "shopline": shopline,
        "ga4": ga4,
        "checkedAt": now_iso(timezone_name=client.config.timezone_name),
    }


def fetch_ga4_traffic_series(
    config: Ga4Config,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - depends on optional package availability
        raise RuntimeError("google-analytics-data is not installed") from exc

    credentials = None
    if config.service_account_json:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(config.service_account_json),
            scopes=[GA4_READONLY_SCOPE],
        )
    elif config.service_account_file:
        credentials = service_account.Credentials.from_service_account_file(
            config.service_account_file,
            scopes=[GA4_READONLY_SCOPE],
        )

    client = BetaAnalyticsDataClient(credentials=credentials)
    request = RunReportRequest(
        property=ga4_property_name(config.property_id),
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="totalUsers"),
            Metric(name=f"keyEvents:{config.key_event_name}"),
            Metric(name=config.metric_name),
        ],
    )
    response = client.run_report(request=request, timeout=config.timeout_seconds)
    return normalize_ga4_rows(response, config.metric_name)


def fetch_ga4_channel_rows(
    config: Ga4Config,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Filter,
            FilterExpression,
            Metric,
            RunReportRequest,
        )
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - depends on optional package availability
        raise RuntimeError("google-analytics-data is not installed") from exc

    credentials = None
    if config.service_account_json:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(config.service_account_json),
            scopes=[GA4_READONLY_SCOPE],
        )
    elif config.service_account_file:
        credentials = service_account.Credentials.from_service_account_file(
            config.service_account_file,
            scopes=[GA4_READONLY_SCOPE],
        )

    client = BetaAnalyticsDataClient(credentials=credentials)
    request = RunReportRequest(
        property=ga4_property_name(config.property_id),
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionDefaultChannelGroup"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name=f"keyEvents:{config.key_event_name}"),
            Metric(name="advertiserAdCost"),
        ],
        limit=250,
    )
    response = client.run_report(request=request, timeout=config.timeout_seconds)

    # GA4's `sessions` metric is non-additive for this property's source
    # dimensions. Count session_start events for the channel table instead so
    # each session is represented once and channel totals remain reconcilable.
    session_request = RunReportRequest(
        property=ga4_property_name(config.property_id),
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionDefaultChannelGroup"),
        ],
        metrics=[Metric(name="eventCount"), Metric(name="activeUsers")],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="eventName",
                string_filter=Filter.StringFilter(
                    value="session_start",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            )
        ),
        limit=250,
    )
    session_response = client.run_report(
        request=session_request,
        timeout=config.timeout_seconds,
    )
    return merge_ga4_channel_session_starts(
        normalize_ga4_channel_rows(response),
        normalize_ga4_session_start_rows(session_response),
    )


def normalize_ga4_channel_rows(response: Any) -> list[dict[str, Any]]:
    dimensions = [header.name for header in getattr(response, "dimension_headers", [])]
    metrics = [header.name for header in getattr(response, "metric_headers", [])]
    rows = []
    for row in getattr(response, "rows", []):
        dimension_map = {
            dimensions[index]: value.value
            for index, value in enumerate(getattr(row, "dimension_values", []))
            if index < len(dimensions)
        }
        metric_map = {
            metrics[index]: value.value
            for index, value in enumerate(getattr(row, "metric_values", []))
            if index < len(metrics)
        }
        key_events = metric_map.get("keyEvents")
        if key_events is None:
            key_events = next(
                (value for key, value in metric_map.items() if key.startswith("keyEvents")),
                None,
            )
        source = str(dimension_map.get("sessionSource") or "").strip()
        medium = str(dimension_map.get("sessionMedium") or "").strip()
        group = str(dimension_map.get("sessionDefaultChannelGroup") or "").strip()
        rows.append(
            {
                "channel": normalize_ga4_channel(source, medium, group),
                "source": source or "(not set)",
                "medium": medium or "(not set)",
                "group": group or "Unassigned",
                "sessions": max(0, parse_int(metric_map.get("sessions"))),
                "activeUsers": max(0, parse_int(metric_map.get("activeUsers"))),
                "keyEvents": max(0, parse_optional_float(key_events) or 0),
                "adCost": max(0, parse_optional_float(metric_map.get("advertiserAdCost")) or 0),
            }
        )
    return rows


def normalize_ga4_session_start_rows(response: Any) -> list[dict[str, Any]]:
    dimensions = [header.name for header in getattr(response, "dimension_headers", [])]
    metrics = [header.name for header in getattr(response, "metric_headers", [])]
    rows: list[dict[str, Any]] = []
    for row in getattr(response, "rows", []):
        dimension_map = {
            dimensions[index]: value.value
            for index, value in enumerate(getattr(row, "dimension_values", []))
            if index < len(dimensions)
        }
        metric_map = {
            metrics[index]: value.value
            for index, value in enumerate(getattr(row, "metric_values", []))
            if index < len(metrics)
        }
        rows.append(
            {
                "source": str(dimension_map.get("sessionSource") or "(not set)").strip(),
                "medium": str(dimension_map.get("sessionMedium") or "(not set)").strip(),
                "group": str(
                    dimension_map.get("sessionDefaultChannelGroup") or "Unassigned"
                ).strip(),
                "sessions": max(0, parse_int(metric_map.get("eventCount"))),
                "activeUsers": max(0, parse_int(metric_map.get("activeUsers"))),
            }
        )
    return rows


def merge_ga4_channel_session_starts(
    channel_rows: list[dict[str, Any]],
    session_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return tuple(
            str(row.get(field) or "").strip().lower()
            for field in ("source", "medium", "group")
        )

    session_lookup = {row_key(row): row for row in session_rows}
    merged: list[dict[str, Any]] = []
    channel_keys: set[tuple[str, str, str]] = set()
    for row in channel_rows:
        key = row_key(row)
        channel_keys.add(key)
        session_row = session_lookup.get(key, {})
        merged.append(
            {
                **row,
                "reportedSessions": parse_int(row.get("sessions")),
                "reportedActiveUsers": parse_int(row.get("activeUsers")),
                "sessions": parse_int(session_row.get("sessions")),
                "activeUsers": parse_int(session_row.get("activeUsers")),
                "sessionMetric": "session_start",
            }
        )

    for key, session_row in session_lookup.items():
        if key in channel_keys:
            continue
        source = str(session_row.get("source") or "(not set)")
        medium = str(session_row.get("medium") or "(not set)")
        group = str(session_row.get("group") or "Unassigned")
        merged.append(
            {
                "channel": normalize_ga4_channel(source, medium, group),
                "source": source,
                "medium": medium,
                "group": group,
                "sessions": parse_int(session_row.get("sessions")),
                "activeUsers": parse_int(session_row.get("activeUsers")),
                "keyEvents": 0.0,
                "adCost": 0.0,
                "reportedSessions": 0,
                "reportedActiveUsers": 0,
                "sessionMetric": "session_start",
            }
        )
    return merged


def normalize_ga4_channel(source: Any, medium: Any, group: Any) -> str:
    source_text = str(source or "").strip()
    medium_text = str(medium or "").strip()
    group_text = str(group or "").strip().lower()
    combined = f"{source_text} {medium_text}".strip()

    if is_internal_store_host(source_text):
        return "Direct"
    source_key = re.sub(r"[\s._/-]+", "", source_text.lower())
    if source_key in {"smartpush", "wangao"}:
        return "SmartPush"
    if source_text.lower().startswith("igshopping"):
        return "Instagram"

    normalized = normalize_marketing_source(combined)
    if normalized in TRAFFIC_SOURCE_BUCKETS and normalized not in {"Direct", "Organic", "Ad", "Other"}:
        return normalized
    if "direct" in group_text or source_text.lower() in {"(direct)", "direct"}:
        return "Direct"
    if "email" in group_text or medium_text.lower() in {"email", "e-mail", "edm"}:
        return "Email"
    if "affiliate" in group_text:
        return "Affiliate"
    if "referral" in group_text:
        return "Referral"
    if "organic" in group_text:
        return normalized if normalized in TRAFFIC_SOURCE_BUCKETS and normalized != "Direct" else "Organic"
    if any(label in group_text for label in ("paid", "display", "cross-network", "shopping")):
        return normalized if normalized in TRAFFIC_SOURCE_BUCKETS and normalized != "Direct" else "Ad"
    if normalized in TRAFFIC_SOURCE_BUCKETS:
        return normalized
    if not source_text or source_text.lower() in {"(not set)", "not set", "unassigned"}:
        return "Other"
    return "Referral"


def ga4_property_name(property_id: str) -> str:
    clean = str(property_id or "").strip()
    if clean.startswith("properties/"):
        return clean
    return f"properties/{clean}"


def normalize_ga4_rows(response: Any, metric_name: str) -> list[dict[str, Any]]:
    dimensions = [header.name for header in getattr(response, "dimension_headers", [])]
    metrics = [header.name for header in getattr(response, "metric_headers", [])]
    rows = []
    for row in getattr(response, "rows", []):
        dimension_values = getattr(row, "dimension_values", [])
        metric_values = getattr(row, "metric_values", [])
        dimension_map = {
            dimensions[index]: value.value
            for index, value in enumerate(dimension_values)
            if index < len(dimensions)
        }
        metric_map = {
            metrics[index]: value.value
            for index, value in enumerate(metric_values)
            if index < len(metrics)
        }
        day = normalize_ga4_date(dimension_map.get("date"))
        if not day:
            continue
        key_events = metric_map.get("keyEvents")
        if key_events is None:
            key_events = next(
                (value for key, value in metric_map.items() if key.startswith("keyEvents")),
                None,
            )
        rows.append(
            {
                "date": day,
                "visitors": None,
                "sessions": max(0, parse_int(metric_map.get("sessions"))),
                "activeUsers": max(0, parse_int(metric_map.get("activeUsers"))),
                "totalUsers": max(0, parse_int(metric_map.get("totalUsers"))),
                "keyEvents": max(0, parse_optional_float(key_events) or 0),
                "conversion": normalize_ga4_rate(metric_map.get(metric_name)),
                "source": "ga4",
            }
        )
    return sorted(rows, key=lambda row: row["date"])


def normalize_ga4_date(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    parsed = parse_date(text)
    return parsed.isoformat() if parsed else ""


def filter_ga4_rows_by_window(
    rows: list[dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (day := parse_date(row.get("date"))) is not None and start <= day <= end
    ]


def normalize_ga4_rate(value: Any) -> float | None:
    rate = parse_optional_float(value)
    if rate is None:
        return None
    if abs(rate) <= 1:
        rate *= 100
    return round_number(rate)


def apply_ga4_conversion_mode(
    rows: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    normalized_mode = normalize_ga4_conversion_mode(mode)
    if normalized_mode == "key_event_rate":
        return [dict(row, conversionMode=normalized_mode) for row in rows]

    order_counts: dict[str, int] = {}
    for order in orders:
        key = str(order.get("createdAt", ""))[:10]
        if key:
            order_counts[key] = order_counts.get(key, 0) + 1

    denominator_field = ga4_denominator_field(normalized_mode)
    converted = []
    for row in rows:
        next_row = dict(row)
        order_count = order_counts.get(str(row.get("date")), 0)
        denominator = parse_int(row.get(denominator_field))
        next_row["shoplineOrders"] = order_count
        next_row["conversionMode"] = normalized_mode
        next_row["conversionDenominatorField"] = denominator_field
        next_row["conversion"] = (
            round_number(order_count / denominator * 100)
            if denominator > 0
            else None
        )
        converted.append(next_row)
    return converted


def ga4_denominator_field(mode: str) -> str:
    normalized_mode = normalize_ga4_conversion_mode(mode)
    if normalized_mode == "shopline_orders_over_active_users":
        return "activeUsers"
    if normalized_mode == "shopline_orders_over_total_users":
        return "totalUsers"
    return "sessions"


def ga4_conversion_rate(
    rows: list[dict[str, Any]],
    mode: str = "key_event_rate",
) -> float | None:
    denominator_field = ga4_denominator_field(mode)
    weighted_total = 0.0
    denominator_total = 0
    fallback_values = []
    for row in rows:
        conversion = parse_optional_float(row.get("conversion"))
        if conversion is None:
            continue
        fallback_values.append(conversion)
        denominator = parse_int(row.get(denominator_field))
        if denominator > 0:
            weighted_total += conversion * denominator
            denominator_total += denominator
    if denominator_total > 0:
        return round_number(weighted_total / denominator_total)
    if fallback_values:
        return round_number(sum(fallback_values) / len(fallback_values))
    return None


def merge_traffic_series(
    base_rows: list[dict[str, Any]],
    override_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overrides = {str(row.get("date")): row for row in override_rows}
    merged = []
    for row in base_rows:
        next_row = dict(row)
        override = overrides.get(str(row.get("date")))
        if override:
            for field in (
                "visitors",
                "sessions",
                "activeUsers",
                "totalUsers",
                "keyEvents",
                "shoplineOrders",
                "conversion",
                "conversionMode",
                "conversionDenominatorField",
            ):
                if override.get(field) is not None:
                    next_row[field] = override[field]
            next_row["source"] = str(override.get("source") or "ga4")
        merged.append(next_row)
    return merged


def conversion_note(value: float | None) -> str:
    return "" if value is not None else "未配置真实访客数"


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def parse_amount(value: Any) -> float:
    if isinstance(value, dict):
        if "amount" in value:
            return parse_amount(value["amount"])
        if "value" in value:
            return parse_amount(value["value"])
        if "cent_amount" in value:
            return parse_amount(value["cent_amount"]) / 100
        if "cents" in value:
            return parse_amount(value["cents"]) / 100
        return 0.0
    if isinstance(value, (int, float)):
        if math.isnan(value) if isinstance(value, float) else False:
            return 0.0
        return float(value)
    if value is None:
        return 0.0

    text = str(value).strip()
    if not text:
        return 0.0
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return 0.0
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return 0.0


def parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (OSError, ValueError):
            return None
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def filter_orders_by_window(
    orders: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    filtered = []
    for order in orders:
        order_date = parse_date(order.get("createdAt"))
        if order_date and start <= order_date <= end:
            filtered.append(order)
    return filtered


def apply_dashboard_filters(
    orders: list[dict[str, Any]],
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    filters = filters or {}
    channel = str(filters.get("channel") or "").strip().lower()
    status = str(filters.get("status") or "").strip().lower()
    market = str(filters.get("market") or "").strip().lower()
    product = str(filters.get("product") or "").strip().lower()
    result = []
    for order in orders:
        if channel and str(order.get("source") or "").strip().lower() != channel:
            continue
        if market and str(order.get("market") or "").strip().lower() != market:
            continue
        if status and not order_matches_dashboard_status(order, status):
            continue
        if product:
            item_values = [
                str(value or "").strip().lower()
                for item in order.get("items", [])
                if isinstance(item, dict)
                for value in (item.get("sku"), item.get("title"))
            ]
            if product not in item_values:
                continue
        result.append(order)
    return result


def order_matches_dashboard_status(order: dict[str, Any], status: str) -> bool:
    financial = str(order.get("status") or "").lower()
    fulfillment = str(order.get("fulfillmentStatus") or "").lower()
    if status == "paid":
        return "paid" in financial and "unpaid" not in financial
    if status == "unpaid":
        return "unpaid" in financial
    if status == "fulfilled":
        return "fulfill" in fulfillment and "unfulfill" not in fulfillment
    if status == "unfulfilled":
        return any(token in fulfillment for token in ("unful", "pending", "open"))
    if status == "refunded":
        return "refund" in financial or "refund" in fulfillment
    if status == "cancelled":
        return "cancel" in financial or "cancel" in fulfillment
    return True


def build_filter_options(orders: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    channels = sorted({str(order.get("source") or "").strip() for order in orders if order.get("source")})
    markets = sorted({str(order.get("market") or "").strip() for order in orders if order.get("market")})
    products: dict[str, str] = {}
    for order in orders:
        for item in order.get("items", []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("sku") or item.get("title") or "").strip()
            if value:
                products[value] = str(item.get("title") or value).strip()
    return {
        "channels": [{"value": value, "label": value} for value in channels],
        "markets": [{"value": value, "label": value} for value in markets],
        "products": [
            {"value": value, "label": label}
            for value, label in sorted(products.items(), key=lambda item: item[1])[:200]
        ],
        "statuses": [
            {"value": "paid", "label": "已支付"},
            {"value": "unpaid", "label": "未支付"},
            {"value": "fulfilled", "label": "已发货"},
            {"value": "unfulfilled", "label": "待发货"},
            {"value": "refunded", "label": "退款"},
            {"value": "cancelled", "label": "取消"},
        ],
    }


def apply_ga4_channel_filter(
    rows: list[dict[str, Any]],
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    channel = str(filters.get("channel") or "").strip().lower()
    if not channel:
        return rows
    return [row for row in rows if str(row.get("channel") or "").strip().lower() == channel]


def build_traffic_series(
    start: date,
    end: date,
    orders: list[dict[str, Any]],
    traffic_overrides: dict[str, dict[str, int]] | None = None,
    sample_mode: bool = True,
) -> list[dict[str, Any]]:
    by_date: dict[str, int] = {}
    for order in orders:
        day = str(order.get("createdAt", ""))[:10]
        by_date[day] = by_date.get(day, 0) + 1

    traffic_overrides = traffic_overrides or {}
    series = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        order_count = by_date.get(cursor.isoformat(), 0)
        configured = traffic_overrides.get(key)
        if configured is not None:
            visitors = configured.get("visitors")
            sessions = configured.get("sessions")
            source = "configured"
        elif sample_mode:
            rng = random.Random(cursor.toordinal() * 17)
            baseline = 380 + rng.randint(0, 220)
            visitors = max(baseline, order_count * rng.randint(32, 58) + rng.randint(80, 180))
            sessions = int(visitors * (1.12 + rng.random() * 0.22))
            source = "sample"
        else:
            visitors = None
            sessions = None
            source = "missing"
        series.append(
            {
                "date": key,
                "visitors": visitors,
                "sessions": sessions,
                "source": source,
            }
        )
        cursor += timedelta(days=1)
    return series


def build_series(
    start: date,
    end: date,
    orders: list[dict[str, Any]],
    traffic: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    order_map: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        key = str(order.get("createdAt", ""))[:10]
        order_map.setdefault(key, []).append(order)
    traffic_map = {row["date"]: row for row in traffic}

    rows = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        day_orders = order_map.get(key, [])
        revenue = sum(float(order.get("total", 0)) for order in day_orders)
        traffic_row = traffic_map.get(key, {})
        visitor_value = traffic_row.get("visitors")
        session_value = traffic_row.get("sessions")
        visitors = parse_int(visitor_value) if visitor_value is not None else None
        sessions = parse_int(session_value) if session_value is not None else None
        active_users_value = traffic_row.get("activeUsers")
        total_users_value = traffic_row.get("totalUsers")
        key_events_value = traffic_row.get("keyEvents")
        shopline_orders_value = traffic_row.get("shoplineOrders")
        active_users = (
            parse_int(active_users_value) if active_users_value is not None else None
        )
        total_users = parse_int(total_users_value) if total_users_value is not None else None
        key_events = (
            parse_optional_float(key_events_value) if key_events_value is not None else None
        )
        shopline_orders = (
            parse_int(shopline_orders_value) if shopline_orders_value is not None else None
        )
        conversion_value = traffic_row.get("conversion")
        conversion = parse_optional_float(conversion_value)
        if conversion is None and visitors and visitors > 0:
            conversion = round_number((len(day_orders) / visitors * 100))
        rows.append(
            {
                "date": key,
                "label": cursor.strftime("%m/%d"),
                "revenue": round_number(revenue),
                "orders": len(day_orders),
                "visitors": visitors,
                "sessions": sessions,
                "activeUsers": active_users,
                "totalUsers": total_users,
                "keyEvents": key_events,
                "shoplineOrders": shopline_orders,
                "conversion": conversion,
            }
        )
        cursor += timedelta(days=1)
    return rows


def build_chart_analytics(
    current_kpis: dict[str, float | None],
    previous_kpis: dict[str, float | None],
    year_kpis: dict[str, float | None],
    lookback_series: list[dict[str, Any]],
    current_orders: list[dict[str, Any]],
    current_ga4_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "comparison": [
            comparison_item("销售额环比", current_kpis.get("revenue"), previous_kpis.get("revenue"), "currency"),
            comparison_item("订单环比", current_kpis.get("orders"), previous_kpis.get("orders"), "number"),
            comparison_item("转化率环比", current_kpis.get("conversion"), previous_kpis.get("conversion"), "percent"),
            comparison_item(
                "销售额同比",
                current_kpis.get("revenue"),
                year_kpis.get("revenue"),
                "currency",
                allow_zero_previous=False,
            ),
        ],
        "windows": [
            summarize_series_window("近 7 天", lookback_series[-7:]),
            summarize_series_window("近 30 天", lookback_series[-30:]),
        ],
        "funnel": build_conversion_funnel(current_orders, current_ga4_rows),
    }


def comparison_item(
    label: str,
    current: float | None,
    previous: float | None,
    value_type: str,
    allow_zero_previous: bool = True,
) -> dict[str, Any]:
    if previous in (None, 0) and not allow_zero_previous:
        previous = None
    delta = calculate_delta(current, previous)
    return {
        "label": label,
        "value": round_number(current) if current is not None else None,
        "previous": round_number(previous) if previous is not None else None,
        "delta": round(delta, 1) if delta is not None else None,
        "type": value_type,
        "tone": "neutral" if delta is None else "positive" if delta >= 0 else "negative",
    }


def summarize_series_window(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    revenue = sum(float(row.get("revenue") or 0) for row in rows)
    orders = sum(int(row.get("orders") or 0) for row in rows)
    sessions = sum_optional_int(row.get("sessions") for row in rows)
    key_events = sum_optional_float(row.get("keyEvents") for row in rows)
    conversion_values = [
        float(row.get("conversion"))
        for row in rows
        if row.get("conversion") is not None
    ]
    conversion = None
    if sessions is not None and sessions > 0 and key_events is not None:
        conversion = key_events / sessions * 100
    elif conversion_values:
        conversion = sum(conversion_values) / len(conversion_values)

    return {
        "label": label,
        "days": len(rows),
        "revenue": round_number(revenue),
        "orders": orders,
        "aov": round_number(revenue / orders if orders else 0),
        "sessions": sessions,
        "conversion": round_number(conversion) if conversion is not None else None,
    }


def build_conversion_funnel(
    orders: list[dict[str, Any]],
    ga4_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sessions = sum_optional_int(row.get("sessions") for row in ga4_rows)
    active_users = sum_optional_int(row.get("activeUsers") for row in ga4_rows)
    key_events = sum_optional_float(row.get("keyEvents") for row in ga4_rows)
    shopline_orders = len(orders)

    raw_steps = [
        ("访问会话", sessions),
        ("活跃用户", active_users),
        ("GA4 Purchase", key_events),
        ("Shopline 订单", shopline_orders),
    ]
    available_steps = [(label, value) for label, value in raw_steps if value is not None]
    if not available_steps:
        available_steps = [("Shopline 订单", shopline_orders)]

    base_value = float(available_steps[0][1] or 0)
    previous_value: float | None = None
    steps = []
    for label, value in available_steps:
        numeric_value = float(value or 0)
        steps.append(
            {
                "label": label,
                "value": round_number(numeric_value),
                "baseRate": round_number(numeric_value / base_value * 100) if base_value > 0 else None,
                "stepRate": (
                    round_number(numeric_value / previous_value * 100)
                    if previous_value and previous_value > 0
                    else None
                ),
            }
        )
        previous_value = numeric_value
    return steps


def sum_optional_int(values: Iterable[Any]) -> int | None:
    total = 0
    found = False
    for value in values:
        if value is None:
            continue
        total += parse_int(value)
        found = True
    return total if found else None


def sum_optional_float(values: Iterable[Any]) -> float | None:
    total = 0.0
    found = False
    for value in values:
        parsed = parse_optional_float(value)
        if parsed is None:
            continue
        total += parsed
        found = True
    return round_number(total) if found else None


def build_channels(
    orders: list[dict[str, Any]],
    ga4_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    ga4_rows = ga4_rows or []
    grouped: dict[str, dict[str, Any]] = {}
    total_revenue = sum(float(order.get("total", 0)) for order in orders) or 1
    for order in orders:
        source = str(order.get("source") or "").strip()
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = normalize_marketing_source(order.get("sourceRaw") or source)
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = "Direct"
        bucket = grouped.setdefault(source, empty_channel_bucket(source))
        bucket["orders"] += 1
        bucket["revenue"] += float(order.get("total", 0))
        bucket["units"] += int(order.get("units", 0))
        if order.get("attributionMethod") == "shopline_attribution":
            bucket["officialOrders"] += 1
        bucket["orderDetails"].append(
            {
                "id": str(order.get("id") or ""),
                "createdAt": str(order.get("createdAt") or ""),
                "total": round_number(float(order.get("total") or 0)),
                "status": str(order.get("status") or ""),
                "utmSource": str(order.get("sourceUtm") or ""),
                "utmMedium": str(order.get("sourceMedium") or ""),
                "campaign": str(order.get("sourceCampaign") or ""),
                "attributionMethod": str(order.get("attributionMethod") or ""),
                "attributionConfidence": str(order.get("attributionConfidence") or ""),
            }
        )
        utm_values = {
            "source": str(order.get("sourceUtm") or "").strip(),
            "medium": str(order.get("sourceMedium") or "").strip(),
            "campaign": str(order.get("sourceCampaign") or "").strip(),
            "content": str(order.get("sourceContent") or "").strip(),
            "term": str(order.get("sourceTerm") or "").strip(),
        }
        if any(utm_values.values()):
            utm_key = tuple(utm_values.values())
            utm_detail = bucket["utmDetails"].setdefault(
                utm_key,
                {**utm_values, "orders": 0, "revenue": 0.0},
            )
            utm_detail["orders"] += 1
            utm_detail["revenue"] += float(order.get("total", 0))

    total_sessions = sum(parse_int(row.get("sessions")) for row in ga4_rows)
    for row in ga4_rows:
        source = str(row.get("channel") or "Other").strip()
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = normalize_marketing_source(source)
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = "Other"
        bucket = grouped.setdefault(source, empty_channel_bucket(source))
        bucket["sessions"] += parse_int(row.get("sessions"))
        bucket["activeUsers"] += parse_int(row.get("activeUsers"))
        bucket["keyEvents"] += parse_optional_float(row.get("keyEvents")) or 0
        bucket["adSpend"] += parse_optional_float(row.get("adCost")) or 0
        source_medium = " / ".join(
            value
            for value in (str(row.get("source") or "").strip(), str(row.get("medium") or "").strip())
            if value and value != "(not set)"
        )
        if not source_medium:
            source_medium = str(row.get("group") or "Unassigned").strip()
        if source_medium:
            bucket["sourceDetails"][source_medium] = (
                bucket["sourceDetails"].get(source_medium, 0) + parse_int(row.get("sessions"))
            )

    channels = []
    for bucket in grouped.values():
        orders_count = int(bucket["orders"])
        revenue = float(bucket["revenue"])
        sessions = int(bucket["sessions"])
        key_events = float(bucket["keyEvents"])
        details = sorted(
            bucket["sourceDetails"].items(),
            key=lambda item: item[1],
            reverse=True,
        )
        utm_details = sorted(
            bucket["utmDetails"].values(),
            key=lambda item: (item["orders"], item["revenue"]),
            reverse=True,
        )
        utm_orders = sum(int(item["orders"]) for item in utm_details)
        ga4_conversion = round_number(key_events / sessions * 100) if sessions else None
        shopline_conversion = round_number(orders_count / sessions * 100) if sessions else None
        order_details = sorted(
            bucket["orderDetails"],
            key=lambda item: (item["createdAt"], item["id"]),
            reverse=True,
        )
        channels.append(
            {
                "channel": bucket["channel"],
                "orders": orders_count,
                "officialOrders": int(bucket["officialOrders"]),
                "inferredOrders": max(0, orders_count - int(bucket["officialOrders"])),
                "revenue": round_number(revenue),
                "aov": round_number(revenue / orders_count if orders_count else 0),
                "share": round_number(sessions / total_sessions * 100) if total_sessions else round_number(revenue / total_revenue * 100),
                "revenueShare": round_number(revenue / total_revenue * 100),
                "sessions": sessions,
                "activeUsers": int(bucket["activeUsers"]),
                "keyEvents": round_number(key_events),
                "adSpend": round_number(float(bucket["adSpend"])),
                "conversion": ga4_conversion,
                "ga4Conversion": ga4_conversion,
                "shoplineConversion": shopline_conversion,
                "conversionDifference": (
                    round_number(shopline_conversion - ga4_conversion)
                    if shopline_conversion is not None and ga4_conversion is not None
                    else None
                ),
                "sourceDetails": [
                    {"label": label, "sessions": count}
                    for label, count in details[:3]
                ],
                "utmDetails": [
                    {
                        **{key: item[key] for key in ("source", "medium", "campaign", "content", "term")},
                        "orders": int(item["orders"]),
                        "revenue": round_number(float(item["revenue"])),
                    }
                    for item in utm_details[:20]
                ],
                "utmOrders": utm_orders,
                "utmCombinationCount": len(utm_details),
                "utmCoverage": round_number(utm_orders / orders_count * 100) if orders_count else None,
                "orderDetailCount": len(order_details),
                "orderDetails": order_details[:20],
                "trafficSource": "ga4" if sessions or ga4_rows else "shopline",
            }
        )
    shared_meta_sessions = any(
        row["channel"] == "Facebook"
        and any(
            detail["label"].lower().startswith(("ad / facebook", "sl_smartads / facebook"))
            for detail in row["sourceDetails"]
        )
        for row in channels
    )
    for row in channels:
        shared_meta = shared_meta_sessions and row["channel"] in {"Facebook", "Instagram"}
        row["conversionComparable"] = not shared_meta
        row["conversionNote"] = (
            "Meta 流量共用 facebook UTM，Facebook 与 Instagram 会话无法完全拆分"
            if shared_meta
            else "SHOPLINE 订单与 GA4 会话的跨系统估算"
        )
    sort_field = "sessions" if total_sessions else "revenue"
    return sorted(channels, key=lambda row: (row[sort_field], row["revenue"]), reverse=True)


def empty_channel_bucket(source: str) -> dict[str, Any]:
    return {
        "channel": source,
        "orders": 0,
        "officialOrders": 0,
        "revenue": 0.0,
        "units": 0,
        "sessions": 0,
        "activeUsers": 0,
        "keyEvents": 0.0,
        "adSpend": 0.0,
        "sourceDetails": {},
        "utmDetails": {},
        "orderDetails": [],
    }


def build_channel_analytics(
    orders: list[dict[str, Any]],
    channels: list[dict[str, Any]],
    ga4_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ga4_rows = ga4_rows or []
    total_orders = len(orders)
    official_orders = sum(
        1 for order in orders if order.get("attributionMethod") == "shopline_attribution"
    )
    smartpush_orders = [order for order in orders if order.get("source") == "SmartPush"]
    attributed_orders = sum(
        1 for order in orders if str(order.get("source") or "") not in {"", "Direct", "Other"}
    )
    total_sessions = sum(parse_int(row.get("sessions")) for row in channels)
    attributed_sessions = sum(
        parse_int(row.get("sessions"))
        for row in channels
        if row.get("channel") not in {"Direct", "Other"}
    )
    ga4_configured = Ga4Config.from_env().configured
    return {
        "mode": "ga4_shopline" if ga4_rows else ("ga4_empty" if ga4_configured else "shopline"),
        "label": (
            "GA4 渠道会话 + SHOPLINE 官方订单归因"
            if ga4_rows
            else (
                "SHOPLINE 官方订单归因 · GA4 暂无会话"
                if ga4_configured
                else "SHOPLINE 官方订单归因"
            )
        ),
        "ga4Configured": ga4_configured,
        "sessionMetric": "session_start" if ga4_rows else None,
        "sessions": total_sessions,
        "activeUsers": sum(parse_int(row.get("activeUsers")) for row in channels),
        "attributedSessions": attributed_sessions,
        "attributionRate": round_number(attributed_sessions / total_sessions * 100) if total_sessions else None,
        "orders": total_orders,
        "attributedOrders": attributed_orders,
        "orderAttributionRate": round_number(attributed_orders / total_orders * 100) if total_orders else None,
        "officialOrders": official_orders,
        "officialAttributionRate": round_number(official_orders / total_orders * 100) if total_orders else None,
        "smartPushOrders": len(smartpush_orders),
        "smartPushRevenue": round_number(
            sum(float(order.get("total") or 0) for order in smartpush_orders)
        ),
        "channelCount": len(channels),
    }


def build_campaign_breakdown(orders: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    marked_orders = 0
    for order in orders:
        channel = str(order.get("source") or "Direct").strip() or "Direct"
        campaign = str(order.get("sourceCampaign") or "").strip()
        adset = str(order.get("sourceAdset") or "").strip()
        ad = str(order.get("sourceAd") or "").strip()
        content = str(order.get("sourceContent") or "").strip()
        if campaign or adset or ad or content:
            marked_orders += 1
        key = (channel, campaign or "未标记", adset or "--", ad or content or "--")
        row = grouped.setdefault(
            key,
            {
                "channel": channel,
                "campaign": campaign or "未标记",
                "adset": adset or "--",
                "ad": ad or content or "--",
                "orders": 0,
                "revenue": 0.0,
                "customers": set(),
            },
        )
        row["orders"] += 1
        row["revenue"] += float(order.get("total") or 0)
        customer_key = str(order.get("customerKey") or "").strip()
        if customer_key:
            row["customers"].add(customer_key)

    rows = []
    for row in grouped.values():
        orders_count = int(row["orders"])
        revenue = float(row["revenue"])
        rows.append(
            {
                "channel": row["channel"],
                "campaign": row["campaign"],
                "adset": row["adset"],
                "ad": row["ad"],
                "orders": orders_count,
                "revenue": round_number(revenue),
                "aov": round_number(revenue / orders_count if orders_count else 0),
                "customers": len(row["customers"]),
            }
        )
    rows.sort(key=lambda row: (row["revenue"], row["orders"]), reverse=True)
    total_orders = len(orders)
    return {
        "rows": rows[:100],
        "markedOrders": marked_orders,
        "totalOrders": total_orders,
        "coverage": round_number(marked_orders / total_orders * 100) if total_orders else None,
    }


def suggest_utm_value(field: str, value: Any) -> str:
    """Return a compact, deterministic UTM naming suggestion."""
    text = urllib.parse.unquote_plus(str(value or "")).strip().lower()
    if field == "utm_source":
        text = UTM_SOURCE_ALIASES.get(text, text)
    elif field == "utm_medium":
        text = UTM_MEDIUM_ALIASES.get(text, text)
    text = re.sub(r"[\s/\\]+", "-", text)
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"[-_.]{2,}", "-", text).strip("-_.")
    if text:
        return text
    return {
        "utm_source": "source-name",
        "utm_medium": "paid_social",
        "utm_campaign": "campaign-name",
    }.get(field, "tracking-value")


def build_utm_issue(
    order: dict[str, Any],
    field: str,
    value: str,
    code: str,
    message: str,
    suggestion: str,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "id": hashlib.sha1(
            f"{order.get('id')}|{field}|{code}|{value}".encode("utf-8")
        ).hexdigest()[:12],
        "orderId": str(order.get("id") or "--"),
        "createdAt": str(order.get("createdAt") or ""),
        "customer": str(order.get("customer") or "Guest"),
        "source": str(order.get("source") or "Direct"),
        "campaign": str(order.get("sourceCampaign") or ""),
        "field": field,
        "value": value,
        "code": code,
        "message": message,
        "suggestion": suggestion,
        "severity": severity,
    }


def diagnose_utm_order(order: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        field_name: str(order.get(order_key) or "").strip()
        for order_key, field_name in UTM_REQUIRED_FIELDS
    }
    issues: list[dict[str, Any]] = []
    for field, value in values.items():
        if not value:
            continue
        lowered = value.lower().strip()
        suggestion = suggest_utm_value(field, value)
        if lowered in UTM_PLACEHOLDERS:
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "placeholder",
                    "使用了占位值，归因平台会把它当作无效 UTM。",
                    suggestion,
                    "critical",
                )
            )
            continue
        if field == "utm_source" and lowered in UTM_SOURCE_ALIASES:
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "source_alias",
                    "来源使用了非标准别名，容易拆成多个渠道。",
                    suggestion,
                )
            )
        if field == "utm_medium" and lowered in UTM_MEDIUM_ALIASES:
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "medium_alias",
                    "媒介名称与统一命名规范不一致。",
                    suggestion,
                )
            )
        if field == "utm_source" and lowered in UTM_MEDIUM_VALUES:
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "field_swapped",
                    "utm_source 疑似填入了媒介类型。",
                    "facebook",
                    "critical",
                )
            )
        if value != lowered:
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "uppercase",
                    "包含大写字符，同一名称可能被拆分统计。",
                    suggestion,
                )
            )
        if not UTM_NAMING_PATTERN.fullmatch(value):
            issues.append(
                build_utm_issue(
                    order,
                    field,
                    value,
                    "invalid_format",
                    "仅建议使用小写字母、数字、点、下划线和连字符。",
                    suggestion,
                )
            )

    source_value = values["utm_source"].lower()
    medium_value = values["utm_medium"].lower()
    if source_value and medium_value and source_value == medium_value:
        issues.append(
            build_utm_issue(
                order,
                "utm_source / utm_medium",
                source_value,
                "duplicated_fields",
                "来源和媒介填写成了相同值，无法区分平台与投放类型。",
                f"{suggest_utm_value('utm_source', source_value)} / paid_social",
                "critical",
            )
        )

    order_source = str(order.get("source") or "")
    for click_type in (order.get("clickIds") or {}):
        expected_sources = CLICK_SOURCE_EXPECTATIONS.get(str(click_type).lower(), set())
        if expected_sources and order_source not in expected_sources:
            issues.append(
                build_utm_issue(
                    order,
                    "click_id / utm_source",
                    f"{click_type} → {order_source or '未识别'}",
                    "click_source_conflict",
                    f"{click_type} 与当前渠道不一致，可能存在错误覆盖或跨域丢参。",
                    "核对落地页重定向与最终 URL 参数",
                    "critical",
                )
            )
    return issues


def build_attribution_diagnostics(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Build actionable UTM and click-to-campaign diagnostics for the UI."""
    missing_orders: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    click_rows: list[dict[str, Any]] = []
    valid_utm_orders = 0
    complete_utm_orders = 0
    campaign_orders = 0
    invalid_order_ids: set[str] = set()
    mapped_click_orders: set[str] = set()
    unmapped_click_orders: set[str] = set()
    configured_mappings = load_click_campaign_map_from_env()

    for order in sorted(orders, key=lambda row: str(row.get("createdAt") or ""), reverse=True):
        order_id = str(order.get("id") or "--")
        missing_fields = [
            field_name
            for order_key, field_name in UTM_REQUIRED_FIELDS
            if not str(order.get(order_key) or "").strip()
            or str(order.get(order_key) or "").strip().lower() in UTM_PLACEHOLDERS
        ]
        click_ids = order.get("clickIds") if isinstance(order.get("clickIds"), dict) else {}
        if missing_fields:
            likely_direct = str(order.get("source") or "") in {"", "Direct", "Organic"} and not click_ids
            missing_orders.append(
                {
                    "orderId": order_id,
                    "createdAt": str(order.get("createdAt") or ""),
                    "customer": str(order.get("customer") or "Guest"),
                    "source": str(order.get("source") or "Direct"),
                    "campaign": str(order.get("sourceCampaign") or ""),
                    "total": round_number(float(order.get("total") or 0)),
                    "missingFields": missing_fields,
                    "clickTypes": list(click_ids),
                    "severity": "info" if likely_direct else "critical" if click_ids else "warning",
                    "reason": (
                        "直接或自然访问可按例外处理"
                        if likely_direct
                        else "存在 Click ID 但 UTM 不完整"
                        if click_ids
                        else "订单来源已识别，但标准 UTM 不完整"
                    ),
                }
            )
        else:
            complete_utm_orders += 1

        issues = diagnose_utm_order(order)
        if issues:
            invalid_order_ids.add(order_id)
            issue_rows.extend(issues)
        elif not missing_fields:
            valid_utm_orders += 1
        if str(order.get("sourceCampaign") or "").strip():
            campaign_orders += 1

        for click_type, click_id_value in click_ids.items():
            click_id = str(click_id_value or "").strip()
            if not click_id:
                continue
            configured = resolve_click_campaign_mapping({str(click_type): click_id}, configured_mappings)
            campaign_id = str(configured.get("campaignId") or order.get("sourceCampaignId") or "").strip()
            campaign_name = str(configured.get("campaignName") or order.get("sourceCampaign") or campaign_id).strip()
            mapped = bool(campaign_id or campaign_name)
            if mapped:
                mapped_click_orders.add(order_id)
            else:
                unmapped_click_orders.add(order_id)
            click_rows.append(
                {
                    "orderId": order_id,
                    "createdAt": str(order.get("createdAt") or ""),
                    "source": str(order.get("source") or "Direct"),
                    "clickType": str(click_type).lower(),
                    "clickId": click_id,
                    "clickIdPreview": mask_tracking_id(click_id),
                    "campaignId": campaign_id,
                    "campaignName": campaign_name,
                    "mapped": mapped,
                    "mappingMethod": "configured" if configured else "order_payload" if mapped else "unmapped",
                }
            )

    total_orders = len(orders)
    click_rows.sort(key=lambda row: (not row["mapped"], row["createdAt"]), reverse=True)
    mapping_template = {
        f"{row['clickType']}:{row['clickId']}": {
            "campaignId": "CAMPAIGN_ID",
            "campaignName": "campaign-name",
        }
        for row in click_rows
        if not row["mapped"]
    }
    return {
        "summary": {
            "totalOrders": total_orders,
            "completeUtmOrders": complete_utm_orders,
            "validUtmOrders": valid_utm_orders,
            "missingOrders": len(missing_orders),
            "invalidOrders": len(invalid_order_ids),
            "campaignOrders": campaign_orders,
            "clickOrders": len(mapped_click_orders | unmapped_click_orders),
            "mappedClickOrders": len(mapped_click_orders),
            "unmappedClickOrders": len(unmapped_click_orders),
            "utmCoverage": round_number(complete_utm_orders / total_orders * 100) if total_orders else None,
            "validUtmCoverage": round_number(valid_utm_orders / total_orders * 100) if total_orders else None,
            "campaignCoverage": round_number(campaign_orders / total_orders * 100) if total_orders else None,
            "clickMappingRate": round_number(
                len(mapped_click_orders) / len(mapped_click_orders | unmapped_click_orders) * 100
            ) if mapped_click_orders or unmapped_click_orders else None,
        },
        "missingOrders": missing_orders[:500],
        "issues": issue_rows[:800],
        "clickMappings": click_rows[:500],
        "mappingTemplate": dict(list(mapping_template.items())[:100]),
        "rules": [
            "utm_source、utm_medium、utm_campaign 为标准必填字段",
            "统一使用小写字母、数字、点、下划线或连字符",
            "utm_source 写平台，utm_medium 写流量类型，utm_campaign 写活动名称",
            "广告最终 URL 保留 gclid、fbclid、ttclid 等 Click ID",
        ],
    }


def mask_tracking_id(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 12:
        return text
    return f"{text[:6]}…{text[-4:]}"


def build_ga4_status(
    config: Ga4Config,
    traffic_rows: list[dict[str, Any]],
    channel_rows: list[dict[str, Any]],
    errors: Iterable[str | None] = (),
    start: date | None = None,
    end: date | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    active_errors = [str(error) for error in errors if error]
    sessions = sum_optional_int(row.get("sessions") for row in traffic_rows)
    active_users = sum_optional_int(row.get("activeUsers") for row in traffic_rows)
    purchases = sum_optional_float(row.get("keyEvents") for row in traffic_rows)
    conversion = ga4_conversion_rate(traffic_rows, config.conversion_mode)

    if not config.configured:
        status = "unconfigured"
        label = "GA4 未配置"
    elif active_errors:
        status = "error"
        label = "GA4 接口异常"
    elif traffic_rows:
        status = "ready"
        label = "GA4 数据已抓取"
    else:
        status = "empty"
        label = "GA4 已连接 · 所选日期暂无数据"

    return {
        "configured": config.configured,
        "status": status,
        "label": label,
        "propertyId": config.property_id,
        "keyEventName": config.key_event_name,
        "metricName": config.metric_name,
        "conversionMode": config.conversion_mode,
        "trafficRows": len(traffic_rows),
        "channelRows": len(channel_rows),
        "sessions": sessions,
        "activeUsers": active_users,
        "purchases": purchases,
        "conversion": conversion,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "errors": active_errors,
        "syncedAt": now_iso(timezone_name=timezone_name),
    }


def build_data_reconciliation(
    orders: list[dict[str, Any]],
    ga4_rows: list[dict[str, Any]],
    ga4_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shopline_orders = len(orders)
    ga4_purchases = sum_optional_float(row.get("keyEvents") for row in ga4_rows)
    if ga4_purchases is None:
        status = str((ga4_status or {}).get("status") or "missing")
        label = str((ga4_status or {}).get("label") or "GA4 Purchase 暂无数据")
        return {
            "shoplineOrders": shopline_orders,
            "ga4Purchases": None,
            "difference": None,
            "differenceRate": None,
            "status": status,
            "label": label,
        }
    difference = shopline_orders - ga4_purchases
    difference_rate = abs(difference) / shopline_orders * 100 if shopline_orders else 0
    if difference_rate <= 5:
        status = "aligned"
        label = "数据基本一致"
    elif difference_rate <= 20:
        status = "delayed"
        label = "存在正常延迟"
    else:
        status = "divergent"
        label = "差异需要排查"
    return {
        "shoplineOrders": shopline_orders,
        "ga4Purchases": round_number(ga4_purchases),
        "difference": round_number(difference),
        "differenceRate": round_number(difference_rate),
        "status": status,
        "label": label,
    }


def build_sync_quality(
    order_result: dict[str, Any],
    orders: list[dict[str, Any]],
    product_result: dict[str, Any],
    channels: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    total_orders = len(orders)
    attributed_orders = sum(
        1 for order in orders if str(order.get("source") or "") not in {"", "Direct", "Other"}
    )
    official_attribution_orders = sum(
        1 for order in orders if order.get("attributionMethod") == "shopline_attribution"
    )
    identified_customers = sum(
        1 for order in orders if str(order.get("customerKey") or "").strip()
    )
    campaign_orders = sum(
        1 for order in orders if str(order.get("sourceCampaign") or "").strip()
    )
    attribution_rate = attributed_orders / total_orders * 100 if total_orders else 0
    official_attribution_rate = (
        official_attribution_orders / total_orders * 100 if total_orders else 0
    )
    customer_rate = identified_customers / total_orders * 100 if total_orders else 0
    campaign_rate = campaign_orders / total_orders * 100 if total_orders else 0
    score = 100.0
    score -= min(30, len(errors) * 15)
    score -= max(0, 80 - attribution_rate) * 0.25
    score -= max(0, 80 - customer_rate) * 0.15
    score -= min(15, int(order_result.get("duplicateCount") or 0) * 0.5)
    if order_result.get("pageLimitReached"):
        score -= 20
    return {
        "score": round_number(max(0, score)),
        "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D",
        "orderPages": int(order_result.get("pages") or 0),
        "orderChunks": int(order_result.get("chunks") or 1),
        "orderWindowCacheHits": int(order_result.get("windowCacheHits") or 0),
        "pageLimitReached": bool(order_result.get("pageLimitReached")),
        "rawOrders": int(order_result.get("rawCount") or total_orders),
        "normalizedOrders": int(order_result.get("normalizedCount") or total_orders),
        "duplicateOrders": int(order_result.get("duplicateCount") or 0),
        "productCount": int(product_result.get("rawCount") or 0),
        "attributionRate": round_number(attribution_rate),
        "officialAttributionOrders": official_attribution_orders,
        "officialAttributionRate": round_number(official_attribution_rate),
        "customerIdentificationRate": round_number(customer_rate),
        "campaignCoverage": round_number(campaign_rate),
        "channelCount": len(channels),
        "errorCount": len(errors),
    }


def build_product_rows(
    orders: list[dict[str, Any]], products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    product_lookup = {
        (product.get("sku") or product.get("title")): product
        for product in products
        if product.get("sku") or product.get("title")
    }
    rows: dict[str, dict[str, Any]] = {}
    for order in orders:
        for item in order.get("items", []):
            key = item.get("sku") or item.get("title")
            if not key:
                continue
            product = product_lookup.get(key) or product_lookup.get(item.get("title"))
            row = rows.setdefault(
                key,
                {
                    "title": item.get("title", "Unknown item"),
                    "sku": item.get("sku", ""),
                    "units": 0,
                    "revenue": 0.0,
                    "inventory": int(product.get("inventory", 0)) if product else 0,
                    "status": product.get("status", "active") if product else "active",
                },
            )
            row["units"] += int(item.get("quantity", 0))
            row["revenue"] += float(item.get("revenue", 0))

    if not rows:
        for product in products:
            key = product.get("sku") or product.get("title")
            rows[key] = {
                "title": product.get("title", "Product"),
                "sku": product.get("sku", ""),
                "units": 0,
                "revenue": 0.0,
                "inventory": int(product.get("inventory", 0)),
                "status": product.get("status", "active"),
            }

    result = []
    for row in rows.values():
        result.append(
            {
                "title": row["title"],
                "sku": row["sku"],
                "units": int(row["units"]),
                "revenue": round_number(row["revenue"]),
                "inventory": int(row["inventory"]),
                "status": row["status"],
            }
        )
    return sorted(result, key=lambda item: (item["revenue"], item["units"]), reverse=True)[:8]


def build_recent_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_orders = sorted(orders, key=lambda row: row.get("createdAt", ""), reverse=True)
    return [
        {
            "id": order["id"],
            "createdAt": order["createdAt"],
            "customer": order["customer"],
            "source": order["source"],
            "sourceRaw": order.get("sourceRaw", order["source"]),
            "market": order["market"],
            "total": order["total"],
            "status": order["status"],
            "fulfillmentStatus": order["fulfillmentStatus"],
        }
        for order in sorted_orders
    ]


def build_profit_summary(
    orders: list[dict[str, Any]],
    channels: list[dict[str, Any]],
    costs: CostConfig,
    ad_spend: dict[str, float],
) -> dict[str, Any]:
    gross_revenue = sum(float(order.get("total", 0)) for order in orders)
    refunds = sum(float(order.get("refundTotal", 0)) for order in orders)
    discounts = sum(float(order.get("discounts", 0)) for order in orders)
    tax_total = sum(float(order.get("taxTotal", 0)) for order in orders)
    net_revenue = max(0.0, gross_revenue - refunds)
    sku_costs = load_sku_costs_from_env()
    shipping_costs = load_shipping_costs_from_env()
    product_cost = 0.0
    configured_units = 0
    total_units = 0
    for order in orders:
        items = [item for item in order.get("items", []) if isinstance(item, dict)]
        if not items:
            product_cost += float(order.get("total") or 0) * max(0.0, min(1.0, costs.product_cost_rate))
            continue
        for item in items:
            quantity = max(0, parse_int(item.get("quantity")))
            total_units += quantity
            sku = str(item.get("sku") or "").strip()
            if sku and sku in sku_costs:
                product_cost += sku_costs[sku] * quantity
                configured_units += quantity
            else:
                product_cost += float(item.get("revenue") or 0) * max(0.0, min(1.0, costs.product_cost_rate))
    payment_fee = net_revenue * max(0.0, costs.payment_fee_rate)
    shipping_cost = sum(
        shipping_costs.get(str(order.get("market") or "").upper(), max(0.0, costs.shipping_cost_per_order))
        for order in orders
    )
    manual_ad_cost = sum(float(value) for value in ad_spend.values())
    ga4_ad_cost = sum(float(channel.get("adSpend") or 0) for channel in channels)
    ad_cost = manual_ad_cost if manual_ad_cost > 0 else ga4_ad_cost
    ad_cost_source = "configured" if manual_ad_cost > 0 else ("ga4" if ga4_ad_cost > 0 else "missing")
    platform_cost = payment_fee + shipping_cost
    estimated_profit = net_revenue - product_cost - platform_cost - ad_cost
    margin = (estimated_profit / net_revenue * 100) if net_revenue else 0.0
    return {
        "revenue": round_number(gross_revenue),
        "grossRevenue": round_number(gross_revenue),
        "netRevenue": round_number(net_revenue),
        "refunds": round_number(refunds),
        "discounts": round_number(discounts),
        "taxTotal": round_number(tax_total),
        "productCost": round_number(product_cost),
        "paymentFee": round_number(payment_fee),
        "shippingCost": round_number(shipping_cost),
        "adCost": round_number(ad_cost),
        "adCostSource": ad_cost_source,
        "platformCost": round_number(platform_cost),
        "estimatedProfit": round_number(estimated_profit),
        "margin": round_number(margin),
        "productCostRate": round_number(costs.product_cost_rate * 100),
        "paymentFeeRate": round_number(costs.payment_fee_rate * 100),
        "shippingCostPerOrder": round_number(costs.shipping_cost_per_order),
        "skuCostCount": len(sku_costs),
        "costCoverage": round_number(configured_units / total_units * 100) if total_units else 0,
        "shippingMarketCount": len(shipping_costs),
        "channelCount": len(channels),
        "notes": [
            "已优先采用 SKU 成本，缺失 SKU 按默认成本率估算。",
            "净销售额已扣除接口返回的退款金额。",
        ],
    }


def aggregate_orders_by_channel(orders: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for order in orders:
        channel = str(order.get("source") or "Direct").strip() or "Direct"
        bucket = result.setdefault(channel, {"orders": 0.0, "revenue": 0.0})
        bucket["orders"] += 1
        bucket["revenue"] += float(order.get("total") or 0)
    return result


def build_channel_movement(
    channel: str,
    current: dict[str, float],
    previous: dict[str, float],
) -> dict[str, Any]:
    current_revenue = float(current.get("revenue") or 0)
    previous_revenue = float(previous.get("revenue") or 0)
    current_orders = int(current.get("orders") or 0)
    previous_orders = int(previous.get("orders") or 0)
    return {
        "channel": channel,
        "currentRevenue": round_number(current_revenue),
        "previousRevenue": round_number(previous_revenue),
        "revenueDelta": round_number(current_revenue - previous_revenue),
        "revenueDeltaRate": round_number(calculate_delta(current_revenue, previous_revenue) or 0),
        "currentOrders": current_orders,
        "previousOrders": previous_orders,
        "orderDelta": current_orders - previous_orders,
    }


def build_product_anomalies(
    today: date,
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    baseline_days: int = 7,
) -> list[dict[str, Any]]:
    current_rows: dict[str, dict[str, Any]] = {}
    baseline_rows: dict[str, dict[str, Any]] = {}
    baseline_start = today - timedelta(days=baseline_days)
    for order in orders:
        order_day = parse_date(order.get("createdAt"))
        if order_day is None or order_day > today or order_day < baseline_start:
            continue
        target = current_rows if order_day == today else baseline_rows
        for item in order.get("items", []):
            if not isinstance(item, dict):
                continue
            key = str(item.get("sku") or item.get("title") or "").strip()
            if not key:
                continue
            bucket = target.setdefault(
                key,
                {
                    "sku": str(item.get("sku") or ""),
                    "title": str(item.get("title") or key),
                    "units": 0,
                    "revenue": 0.0,
                },
            )
            bucket["units"] += max(0, parse_int(item.get("quantity")))
            bucket["revenue"] += float(item.get("revenue") or 0)

    inventory_lookup: dict[str, int] = {}
    for product in products:
        inventory = parse_int(product.get("inventory"))
        for key in (product.get("sku"), product.get("title")):
            if str(key or "").strip():
                inventory_lookup[str(key).strip()] = inventory

    anomalies: list[dict[str, Any]] = []
    for key in set(current_rows) | set(baseline_rows):
        current = current_rows.get(key, {})
        baseline = baseline_rows.get(key, {})
        title = str(current.get("title") or baseline.get("title") or key)
        sku = str(current.get("sku") or baseline.get("sku") or "")
        units = int(current.get("units") or 0)
        average_units = float(baseline.get("units") or 0) / max(1, baseline_days)
        inventory = inventory_lookup.get(sku, inventory_lookup.get(title))
        anomaly: dict[str, Any] | None = None
        if units >= 3 and units >= max(3, average_units * 1.8):
            anomaly = {
                "type": "spike",
                "label": "销量突增",
                "tone": "positive",
                "message": f"今日 {units} 件，近 7 日均值 {round_number(average_units)} 件。",
                "score": 300 + units,
            }
        elif average_units >= 1 and units <= average_units * 0.4:
            anomaly = {
                "type": "drop",
                "label": "销量下滑",
                "tone": "negative",
                "message": f"今日 {units} 件，近 7 日均值 {round_number(average_units)} 件。",
                "score": 200 + average_units,
            }
        if inventory is not None and inventory <= 10 and units > 0:
            stock_anomaly = {
                "type": "stock",
                "label": "热卖低库存",
                "tone": "critical",
                "message": f"今日售出 {units} 件，当前库存 {inventory} 件。",
                "score": 400 + units,
            }
            if anomaly is None or stock_anomaly["score"] > anomaly["score"]:
                anomaly = stock_anomaly
        if anomaly:
            anomalies.append(
                {
                    **anomaly,
                    "title": title,
                    "sku": sku,
                    "todayUnits": units,
                    "baselineUnits": round_number(average_units),
                    "inventory": inventory,
                }
            )
    anomalies.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    for row in anomalies:
        row.pop("score", None)
    return anomalies[:5]


def build_daily_operating_summary(
    today: date,
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    costs: CostConfig,
    ad_spend: dict[str, float],
    spend_window_days: int = 1,
) -> dict[str, Any]:
    today_orders = filter_orders_by_window(orders, today, today)
    yesterday = today - timedelta(days=1)
    previous_orders = filter_orders_by_window(orders, yesterday, yesterday)
    today_channels = aggregate_orders_by_channel(today_orders)
    previous_channels = aggregate_orders_by_channel(previous_orders)
    movements = [
        build_channel_movement(
            channel,
            today_channels.get(channel, {}),
            previous_channels.get(channel, {}),
        )
        for channel in set(today_channels) | set(previous_channels)
    ]
    growth_channels = sorted(
        (row for row in movements if float(row["revenueDelta"]) > 0),
        key=lambda row: (float(row["revenueDelta"]), int(row["orderDelta"])),
        reverse=True,
    )[:3]
    decline_channels = sorted(
        (row for row in movements if float(row["revenueDelta"]) < 0),
        key=lambda row: (float(row["revenueDelta"]), int(row["orderDelta"])),
    )[:3]

    daily_ad_spend = {
        channel: float(value) / max(1, int(spend_window_days or 1))
        for channel, value in ad_spend.items()
    }
    today_channel_rows = build_channels(today_orders)
    profit = build_profit_summary(today_orders, today_channel_rows, costs, daily_ad_spend)
    today_revenue = sum(float(order.get("total") or 0) for order in today_orders)
    previous_revenue = sum(float(order.get("total") or 0) for order in previous_orders)
    revenue_delta_rate = calculate_delta(today_revenue, previous_revenue)
    order_delta_rate = calculate_delta(float(len(today_orders)), float(len(previous_orders)))
    if not today_orders:
        headline = "当日暂未产生订单，建议优先检查流量、广告投放与支付链路。"
    else:
        direction = "增长" if (revenue_delta_rate or 0) >= 0 else "下降"
        headline = (
            f"当日 {len(today_orders)} 单，销售额 {round_number(today_revenue)}，"
            f"较昨日{direction} {abs(round_number(revenue_delta_rate or 0))}%。"
        )
    return {
        "date": today.isoformat(),
        "comparisonDate": yesterday.isoformat(),
        "headline": headline,
        "metrics": {
            "orders": len(today_orders),
            "revenue": round_number(today_revenue),
            "ordersDeltaRate": round_number(order_delta_rate) if order_delta_rate is not None else None,
            "revenueDeltaRate": round_number(revenue_delta_rate) if revenue_delta_rate is not None else None,
            "estimatedProfit": profit["estimatedProfit"],
            "profitMargin": profit["margin"],
        },
        "growthChannels": growth_channels,
        "declineChannels": decline_channels,
        "abnormalProducts": build_product_anomalies(today, orders, products),
        "profit": {
            "estimatedProfit": profit["estimatedProfit"],
            "margin": profit["margin"],
            "adCost": profit["adCost"],
            "productCost": profit["productCost"],
        },
    }


def build_ad_performance(channels: list[dict[str, Any]], ad_spend: dict[str, float]) -> list[dict[str, Any]]:
    rows = []
    channel_lookup = {
        str(channel.get("channel", "")).strip(): channel
        for channel in channels
        if str(channel.get("channel", "")).strip()
    }
    channel_names = list(channel_lookup)
    for name in ad_spend:
        if name not in channel_lookup and float(ad_spend.get(name, 0.0)) > 0:
            channel_names.append(name)

    for name in channel_names:
        channel = channel_lookup.get(name, {"channel": name, "orders": 0, "revenue": 0.0})
        configured_spend = float(ad_spend.get(name, 0.0))
        ga4_spend = float(channel.get("adSpend", 0.0))
        spend = configured_spend if configured_spend > 0 else ga4_spend
        revenue = float(channel.get("revenue", 0.0))
        orders_count = int(channel.get("orders", 0))
        rows.append(
            {
                "channel": name,
                "orders": orders_count,
                "spend": round_number(spend),
                "spendSource": "configured" if configured_spend > 0 else ("ga4" if ga4_spend > 0 else "missing"),
                "revenue": round_number(revenue),
                "roas": round_number(revenue / spend if spend else 0.0),
                "cpa": round_number(spend / orders_count if orders_count else 0.0),
            }
        )
    return rows


def build_customer_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    order_count = len(orders)
    customer_rows: dict[str, dict[str, Any]] = {}
    identified_orders = 0
    unidentified_orders = 0
    has_customer_id = False
    has_email = False
    has_phone = False
    has_name = False

    for order in orders:
        customer_id = str(order.get("customerId") or "").strip()
        email = str(order.get("customerEmail") or "").strip()
        phone = str(order.get("customerPhone") or "").strip()
        name = str(order.get("customer") or "").strip()
        key = str(order.get("customerKey") or "").strip()
        identified = bool(key and (customer_id or email or phone or name.lower() != "guest"))

        has_customer_id = has_customer_id or bool(customer_id)
        has_email = has_email or bool(email)
        has_phone = has_phone or bool(phone)
        has_name = has_name or bool(name and name.lower() != "guest")

        if identified:
            identified_orders += 1
        else:
            unidentified_orders += 1
            key = f"unknown:{order.get('id') or unidentified_orders}"
            name = "未识别客户"

        row = customer_rows.setdefault(
            key,
            {
                "name": name or email or phone or "未识别客户",
                "contact": email or phone or customer_id or "--",
                "orders": 0,
                "revenue": 0.0,
                "latestOrder": "",
                "firstOrder": "",
                "identified": identified,
            },
        )
        row["orders"] += 1
        row["revenue"] += float(order.get("total", 0))
        created_at = str(order.get("createdAt") or "")
        if created_at > str(row.get("latestOrder") or ""):
            row["latestOrder"] = created_at
        if created_at and (not row.get("firstOrder") or created_at < str(row.get("firstOrder"))):
            row["firstOrder"] = created_at

    repeat_customers = sum(
        1 for row in customer_rows.values() if int(row["orders"]) > 1 and row["identified"]
    )
    new_customers = sum(
        1 for row in customer_rows.values() if int(row["orders"]) == 1 and row["identified"]
    )
    unique_customers = sum(1 for row in customer_rows.values() if row["identified"])
    repeat_orders = sum(
        int(row["orders"]) - 1 for row in customer_rows.values() if int(row["orders"]) > 1 and row["identified"]
    )
    repeat_rate = (repeat_orders / order_count * 100) if order_count else 0.0
    order_dates = [day for order in orders if (day := parse_date(order.get("createdAt"))) is not None]
    latest_date = max(order_dates, default=None)
    segment_rows: dict[str, dict[str, Any]] = {}
    segment_labels = {
        "champion": "高价值客户",
        "loyal": "复购客户",
        "recent": "近期新客",
        "at_risk": "待召回客户",
        "one_time": "一次购买",
    }
    for row in customer_rows.values():
        if not row.get("identified"):
            continue
        latest_order = parse_date(row.get("latestOrder"))
        recency = (latest_date - latest_order).days if latest_date and latest_order else 0
        frequency = int(row.get("orders") or 0)
        monetary = float(row.get("revenue") or 0)
        if frequency >= 3 and recency <= 30:
            segment = "champion"
        elif recency > 60 and frequency >= 2:
            segment = "at_risk"
        elif frequency >= 2:
            segment = "loyal"
        elif recency <= 14:
            segment = "recent"
        else:
            segment = "one_time"
        bucket = segment_rows.setdefault(
            segment,
            {"key": segment, "label": segment_labels[segment], "customers": 0, "revenue": 0.0},
        )
        bucket["customers"] += 1
        bucket["revenue"] += monetary
    top_customers = sorted(
        customer_rows.values(),
        key=lambda row: (float(row["revenue"]), int(row["orders"])),
        reverse=True,
    )[:5]
    hints = []
    if not order_count:
        hints.append("当前日期范围没有订单，客户分析暂无可统计数据。")
    if unidentified_orders:
        hints.append(
            f"{unidentified_orders} 个订单没有可识别客户字段，请确认 Shopline 订单接口返回 customer / buyer / user 信息。"
        )
    if order_count and not (has_customer_id or has_email or has_phone):
        hints.append("当前只能按客户姓名估算复购；建议开放 customer.id、email 或 phone 字段。")
    missing_fields = []
    if not has_customer_id:
        missing_fields.append("customer.id / customer_id")
    if not has_email:
        missing_fields.append("customer.email / email")
    if not has_phone:
        missing_fields.append("customer.phone / phone")
    if not has_name:
        missing_fields.append("customer.name / customer_name")

    return {
        "uniqueCustomers": unique_customers,
        "newCustomers": new_customers,
        "repeatCustomers": repeat_customers,
        "repeatRate": round_number(repeat_rate),
        "averageLtv": round_number(
            sum(float(row["revenue"]) for row in customer_rows.values() if row["identified"]) / unique_customers
            if unique_customers
            else 0
        ),
        "averageOrdersPerCustomer": round_number(identified_orders / unique_customers if unique_customers else 0),
        "segments": [
            {
                "key": row["key"],
                "label": row["label"],
                "customers": int(row["customers"]),
                "revenue": round_number(float(row["revenue"])),
            }
            for row in sorted(segment_rows.values(), key=lambda item: item["revenue"], reverse=True)
        ],
        "identifiedOrders": identified_orders,
        "unidentifiedOrders": unidentified_orders,
        "identifiedRate": round_number(identified_orders / order_count * 100 if order_count else 0),
        "missingFields": missing_fields,
        "hints": hints,
        "requiredFields": [
            "订单日期：created_at / order_at",
            "订单金额：total_price / total_amount",
            "客户标识：customer.id、email、phone 至少一个",
            "客户名称：customer.name / customer_name",
        ],
        "topCustomers": [
            {
                "name": str(row["name"]),
                "contact": str(row["contact"]),
                "orders": int(row["orders"]),
                "revenue": round_number(float(row["revenue"])),
                "latestOrder": str(row.get("latestOrder") or ""),
                "identified": bool(row["identified"]),
            }
            for row in top_customers
        ],
    }


def build_order_status_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "paid": 0,
        "unpaid": 0,
        "fulfilled": 0,
        "unfulfilled": 0,
        "cancelled": 0,
        "refunded": 0,
        "other": 0,
    }
    total = len(orders) or 1
    for order in orders:
        financial = str(order.get("status") or "").lower()
        fulfillment = str(order.get("fulfillmentStatus") or "").lower()
        if "refund" in financial or "refund" in fulfillment:
            counts["refunded"] += 1
        elif "cancel" in financial or "cancel" in fulfillment:
            counts["cancelled"] += 1
        elif "unpaid" in financial:
            counts["unpaid"] += 1
        elif "paid" in financial:
            counts["paid"] += 1
        else:
            counts["other"] += 1

        if "fulfill" in fulfillment and "unfulfill" not in fulfillment:
            counts["fulfilled"] += 1
        elif any(token in fulfillment for token in {"unful", "pending", "open"}):
            counts["unfulfilled"] += 1

    return {
        "counts": counts,
        "rates": {key: round_number(value / total * 100) for key, value in counts.items()},
        "total": len(orders),
    }


def build_alerts(
    kpis: dict[str, float],
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    source_mode: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    alerts = []
    low_stock_products = [p for p in products if int(p.get("inventory", 0)) <= 5]
    pending_orders = [
        order
        for order in orders
        if str(order.get("fulfillmentStatus", "")).lower() in {"unfulfilled", "pending", "open"}
    ]

    if source_mode != "live":
        alerts.append(
            {
                "level": "info",
                "title": "数据源",
                "message": "当前使用示例数据，Shopline 环境变量配置完成后会切到实时数据。",
            }
        )
    if errors:
        alerts.append(
            {
                "level": "critical",
                "title": "接口回退",
                "message": "Shopline 请求失败，页面已回退到示例数据。",
            }
        )
    if low_stock_products:
        alerts.append(
            {
                "level": "warning",
                "title": "库存预警",
                "message": f"{len(low_stock_products)} 个 SKU 库存低于 5 件。",
            }
        )
    if pending_orders:
        alerts.append(
            {
                "level": "warning",
                "title": "履约队列",
                "message": f"{len(pending_orders)} 个订单仍在待发货状态。",
            }
        )
    if kpis["conversion"] is not None and kpis["conversion"] < 1:
        alerts.append(
            {
                "level": "warning",
                "title": "转化率",
                "message": "转化率低于 1%，建议检查落地页与支付链路。",
            }
        )
    return alerts[:5]


def load_alert_thresholds() -> dict[str, float]:
    defaults: dict[str, float] = {
        "orderDropPercent": 30,
        "lowStock": 5,
        "hotStock": 10,
        "hotSalesUnits": 5,
        "unpaidCount": 5,
        "unpaidRate": 20,
        "refundCount": 2,
        "refundRate": 3,
        "minimumRoas": 1.5,
        "minimumConversion": 1,
    }
    raw = os.getenv("DASHBOARD_ALERT_THRESHOLDS_JSON", "").strip()
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return defaults
    if not isinstance(parsed, dict):
        return defaults
    for key in defaults:
        if key in parsed:
            value = parse_optional_float(parsed.get(key))
            if value is not None and value >= 0:
                defaults[key] = value
    return defaults


def deliver_alert_webhook(
    alerts: list[dict[str, Any]],
    force_refresh: bool = False,
    manual: bool = False,
) -> dict[str, Any]:
    webhook_url = os.getenv("DASHBOARD_ALERT_WEBHOOK_URL", "").strip()
    send_on_sync = os.getenv("DASHBOARD_ALERT_WEBHOOK_ON_SYNC", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }
    status = {
        "configured": bool(webhook_url),
        "attempted": False,
        "delivered": False,
        "error": None,
    }
    if not webhook_url or not alerts:
        return status
    if not manual and (not force_refresh or not send_on_sync):
        return status
    status["attempted"] = True
    body = json.dumps(
        {
            "title": "SOSOVE 数据面板预警",
            "generatedAt": now_iso(),
            "alerts": alerts,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status["delivered"] = 200 <= int(response.status) < 300
    except Exception as exc:  # pragma: no cover - external webhook specific
        status["error"] = f"{exc.__class__.__name__}: {exc}"
    return status


def decorate_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_status_by_title = {
        "履约队列": "unfulfilled",
        "未支付偏高": "unpaid",
        "退款异常": "refunded",
    }
    channel_titles = {"广告花费异常", "转化率偏低", "订单下滑", "GA4 转化延迟"}
    product_titles = {"库存预警", "爆品库存不足"}
    result = []
    for alert in alerts:
        title = str(alert.get("title") or "预警")
        message = str(alert.get("message") or "")
        alert_id = hashlib.sha1(f"{title}|{message}".encode("utf-8")).hexdigest()[:12]
        result.append(
            {
                **alert,
                "id": alert_id,
                "orderStatus": order_status_by_title.get(title, ""),
                "focus": (
                    "channels"
                    if title in channel_titles
                    else "products"
                    if title in product_titles
                    else "orders"
                ),
                "actions": ["orders", "channels", "dismiss", "notify"],
            }
        )
    return result


def build_alerts_v2(
    kpis: dict[str, float],
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    source_mode: str,
    errors: list[str],
    previous_kpis: dict[str, float] | None = None,
    product_rows: list[dict[str, Any]] | None = None,
    ad_performance: list[dict[str, Any]] | None = None,
    order_status: dict[str, Any] | None = None,
    series: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    previous_kpis = previous_kpis or {}
    product_rows = product_rows or []
    ad_performance = ad_performance or []
    order_status = order_status or {}
    series = series or []
    thresholds = load_alert_thresholds()
    alerts: list[dict[str, Any]] = []

    if source_mode != "live":
        alerts.append(
            {
                "level": "info",
                "title": "数据源",
                "message": "当前不是完整实时数据，请先检查 Shopline / GA4 接口配置。",
            }
        )
    if errors:
        alerts.append(
            {
                "level": "critical",
                "title": "接口异常",
                "message": "数据接口返回异常，部分指标可能不完整。",
            }
        )

    previous_orders = float(previous_kpis.get("orders") or 0)
    current_orders = float(kpis.get("orders") or 0)
    order_delta = calculate_delta(current_orders, previous_orders)
    if len(series) > 1 and previous_orders >= 5 and order_delta is not None and order_delta <= -thresholds["orderDropPercent"]:
        alerts.append(
            {
                "level": "warning",
                "title": "订单下滑",
                "message": f"当前订单 {int(current_orders)} 单，比上期下降 {abs(round_number(order_delta))}%，建议检查广告投放、流量入口和支付链路。",
            }
        )

    low_stock_products = [
        product for product in products
        if int(product.get("inventory", 0)) <= thresholds["lowStock"]
    ]
    if low_stock_products:
        alerts.append(
            {
                "level": "warning",
                "title": "库存预警",
                "message": f"{len(low_stock_products)} 个 SKU 库存低于 {thresholds['lowStock']} 件。",
            }
        )

    hot_low_stock = [
        product
        for product in product_rows
        if int(product.get("units", 0)) >= thresholds["hotSalesUnits"]
        and int(product.get("inventory", 0)) <= thresholds["hotStock"]
    ]
    if hot_low_stock:
        top_hot = sorted(
            hot_low_stock,
            key=lambda product: (int(product.get("units", 0)), float(product.get("revenue", 0))),
            reverse=True,
        )[:2]
        names = "、".join(
            str(product.get("title") or product.get("sku") or "SKU")[:18]
            for product in top_hot
        )
        alerts.append(
            {
                "level": "critical",
                "title": "爆品库存不足",
                "message": f"{len(hot_low_stock)} 个热卖 SKU 库存低于 10，优先处理：{names}。",
            }
        )

    pending_orders = [
        order
        for order in orders
        if str(order.get("fulfillmentStatus", "")).lower() in {"unfulfilled", "pending", "open"}
    ]
    if pending_orders:
        alerts.append(
            {
                "level": "warning",
                "title": "履约队列",
                "message": f"{len(pending_orders)} 个订单仍处于待发货状态。",
            }
        )

    status_counts = order_status.get("counts", {}) if isinstance(order_status, dict) else {}
    status_rates = order_status.get("rates", {}) if isinstance(order_status, dict) else {}
    unpaid_count = int(status_counts.get("unpaid") or 0)
    unpaid_rate = float(status_rates.get("unpaid") or 0)
    refunded_count = int(status_counts.get("refunded") or 0)
    refunded_rate = float(status_rates.get("refunded") or 0)
    if unpaid_count >= thresholds["unpaidCount"] and unpaid_rate >= thresholds["unpaidRate"]:
        alerts.append(
            {
                "level": "warning",
                "title": "未支付偏高",
                "message": f"未支付订单 {unpaid_count} 单，占比 {round_number(unpaid_rate)}%，建议检查支付失败、弃单或客服跟进。",
            }
        )
    if refunded_count >= thresholds["refundCount"] and refunded_rate >= thresholds["refundRate"]:
        alerts.append(
            {
                "level": "critical",
                "title": "退款异常",
                "message": f"退款订单 {refunded_count} 单，占比 {round_number(refunded_rate)}%，建议排查商品质量、物流时效和客服记录。",
            }
        )

    ad_issues = []
    for row in ad_performance:
        channel = str(row.get("channel") or "").strip()
        spend = float(row.get("spend") or 0)
        revenue = float(row.get("revenue") or 0)
        order_count = int(row.get("orders") or 0)
        roas = float(row.get("roas") or 0)
        if spend <= 0:
            continue
        if order_count == 0:
            ad_issues.append(f"{channel} 有花费但暂无订单")
        elif roas < thresholds["minimumRoas"]:
            ad_issues.append(f"{channel} ROAS {round_number(roas)}")
        elif revenue < spend:
            ad_issues.append(f"{channel} 销售额低于广告费")
    if ad_issues:
        alerts.append(
            {
                "level": "warning",
                "title": "广告花费异常",
                "message": "；".join(ad_issues[:2]) + "，建议检查预算、素材和落地页。",
            }
        )

    if kpis.get("conversion") is not None and float(kpis.get("conversion") or 0) < thresholds["minimumConversion"]:
        alerts.append(
            {
                "level": "warning",
                "title": "转化率偏低",
                "message": f"转化率低于 {thresholds['minimumConversion']}%，建议检查落地页、支付链路和流量质量。",
            }
        )

    latest_series = series[-1] if series else {}
    ga4_key_events = parse_optional_float(latest_series.get("keyEvents"))
    latest_order_count = parse_int(latest_series.get("orders"))
    if (
        ga4_key_events is not None
        and latest_order_count >= 5
        and ga4_key_events + 1 < latest_order_count
        and ga4_key_events < latest_order_count * 0.85
    ):
        alerts.append(
            {
                "level": "info",
                "title": "GA4 转化延迟",
                "message": f"GA4 purchase 记录 {round_number(ga4_key_events)} 次，Shopline 今日订单 {latest_order_count} 单，GA4 转化率可能存在延迟。",
            }
        )

    return decorate_alerts(alerts[:8])


def build_events(
    source_mode: str,
    kpis: dict[str, float],
    errors: list[str],
    timezone_name: str | None = None,
) -> list[dict[str, Any]]:
    events = [
        {
            "time": now_iso(timezone_name=timezone_name),
            "kind": "sync",
            "title": "数据同步完成",
            "detail": f"{int(kpis['orders'])} 个订单已进入当前看板。",
        },
        {
            "time": now_iso(minutes=-18, timezone_name=timezone_name),
            "kind": "inventory",
            "title": "库存扫描",
            "detail": f"{int(kpis['lowStock'])} 个 SKU 需要补货关注。",
        },
        {
            "time": now_iso(minutes=-45, timezone_name=timezone_name),
            "kind": "source",
            "title": "数据模式",
            "detail": source_label(source_mode),
        },
    ]
    if errors:
        events.insert(
            0,
            {
                "time": now_iso(minutes=-2, timezone_name=timezone_name),
                "kind": "error",
                "title": "接口错误",
                "detail": errors[0],
            },
        )
    return events


def detect_currency(
    orders: list[dict[str, Any]], products: list[dict[str, Any]], fallback: str
) -> str:
    for collection in (orders, products):
        for item in collection:
            currency = item.get("currency")
            if currency:
                return str(currency).upper()
    return fallback or DEFAULT_CURRENCY


def sample_products(today: date | None = None, currency: str = DEFAULT_CURRENCY) -> list[dict[str, Any]]:
    today = today or current_dashboard_date()
    names = [
        ("Linen Market Dress", "LM-DRESS", "Dresses", 89, 14),
        ("Satin Work Blouse", "SW-BLOUSE", "Tops", 54, 7),
        ("Wide Leg Travel Pants", "WL-PANTS", "Bottoms", 72, 3),
        ("Soft Rib Cardigan", "SR-CARD", "Knitwear", 68, 22),
        ("Summer Utility Skirt", "SU-SKIRT", "Bottoms", 63, 5),
        ("Clean Layer Tank", "CL-TANK", "Tops", 32, 41),
        ("Structured Commuter Bag", "SC-BAG", "Accessories", 96, 8),
        ("Air Mesh Jacket", "AM-JACKET", "Outerwear", 118, 2),
        ("Pleated Ease Dress", "PE-DRESS", "Dresses", 104, 11),
        ("Minimal Cotton Tee", "MC-TEE", "Tops", 28, 36),
    ]
    return [
        {
            "id": f"prod-{index:03d}",
            "title": name,
            "sku": sku,
            "category": category,
            "price": float(price),
            "currency": currency,
            "inventory": inventory,
            "status": "active",
            "updatedAt": (today - timedelta(days=index % 5)).isoformat(),
        }
        for index, (name, sku, category, price, inventory) in enumerate(names, start=1)
    ]


def sample_orders(
    days: int,
    today: date | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> list[dict[str, Any]]:
    today = today or current_dashboard_date()
    products = sample_products(today=today, currency=currency)
    rng = random.Random(today.toordinal() + days * 113)
    sources = ["Meta", "fb", "ins", "Instagram", "Google", "TikTok", "Email", "Direct", "Organic", "ad"]
    markets = ["US", "JP", "HK", "SG", "AU"]
    customers = ["Aki Tanaka", "Mia Chen", "Sara Miller", "Hana Ito", "Lena Wong", "Nora Kim"]
    statuses = ["paid", "paid", "paid", "partially_refunded"]
    fulfillment = ["fulfilled", "unfulfilled", "fulfilled", "pending"]
    orders = []
    order_total = max(24, days * 4)

    for index in range(order_total):
        created = today - timedelta(days=rng.randint(0, max(days - 1, 0)))
        item_count = rng.randint(1, 3)
        chosen_products = rng.sample(products, k=item_count)
        items = []
        for product in chosen_products:
            quantity = rng.randint(1, 2)
            price = float(product["price"])
            discount = 0.92 if rng.random() < 0.22 else 1.0
            items.append(
                {
                    "title": product["title"],
                    "sku": product["sku"],
                    "quantity": quantity,
                    "price": round_number(price * discount),
                    "revenue": round_number(price * quantity * discount),
                }
            )
        total = sum(float(item["revenue"]) for item in items)
        source_raw = rng.choice(sources)
        orders.append(
            {
                "id": f"SL-{today.strftime('%m%d')}-{index + 1001}",
                "createdAt": created.isoformat(),
                "total": round_number(total),
                "currency": currency,
                "status": rng.choice(statuses),
                "fulfillmentStatus": rng.choice(fulfillment),
                "sourceRaw": source_raw,
                "source": normalize_marketing_source(source_raw),
                "market": rng.choice(markets),
                "customer": rng.choice(customers),
                "units": sum(int(item["quantity"]) for item in items),
                "items": items,
            }
        )
    return orders


def build_url(base_url: str, path: str, params: dict[str, str] | None = None) -> str:
    if not base_url:
        raise ValueError("SHOPLINE_API_BASE_URL is not configured")
    base = base_url.rstrip("/") + "/"
    url = urllib.parse.urljoin(base, path.lstrip("/"))
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    return url


def normalize_base_url(base_url: str, api_version: str = DEFAULT_API_VERSION) -> str:
    if not base_url:
        return ""
    if not urllib.parse.urlparse(base_url).scheme:
        base_url = f"https://{base_url}"
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return base_url.rstrip("/")
    path = parsed.path.rstrip("/")
    if "/admin/openapi" in path:
        return base_url.rstrip("/")
    normalized_path = f"{path}/admin/openapi/{api_version}".replace("//", "/")
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    ).rstrip("/")


def normalize_endpoint_path(path: str, resource: str) -> str:
    clean = (path or "").strip()
    if not clean:
        return ""
    default_paths = {
        "orders": "/orders.json",
        "products": "/products/products.json",
    }
    if clean.endswith(".json"):
        return clean
    if clean.strip("/") == resource:
        return default_paths.get(resource, f"/{resource}.json")
    return clean if clean.startswith("/") else f"/{clean}"


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:1]}***{value[-1:]}"
    return f"{value[:4]}...{value[-4:]}"


def mask_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.netloc}"


def round_number(value: float, digits: int = 2) -> float:
    rounded = round(float(value), digits)
    return int(rounded) if rounded.is_integer() else rounded


def current_dashboard_date(timezone_name: str | None = None) -> date:
    return datetime.now(resolve_timezone(timezone_name)).date()


def resolve_timezone(timezone_name: str | None = None):
    name = timezone_name or os.getenv("SHOPLINE_TIMEZONE", DEFAULT_TIMEZONE)
    name = str(name).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def now_iso(minutes: int = 0, timezone_name: str | None = None) -> str:
    tz = resolve_timezone(timezone_name)
    now = datetime.now(tz) + timedelta(minutes=minutes)
    return now.isoformat(timespec="seconds")


def local_midnight(day: date, timezone_name: str | None = None) -> datetime:
    tz = resolve_timezone(timezone_name)
    return datetime.combine(day, time.min, tzinfo=tz)


def local_end_of_day(day: date, timezone_name: str | None = None) -> datetime:
    tz = resolve_timezone(timezone_name)
    return datetime.combine(day, time.max, tzinfo=tz)
