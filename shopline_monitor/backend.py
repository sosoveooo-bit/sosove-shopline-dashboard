from __future__ import annotations

import json
import math
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
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
GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
TRAFFIC_SOURCE_BUCKETS = {
    "Facebook",
    "Instagram",
    "Google",
    "TikTok",
    "Email",
    "Direct",
    "Organic",
    "Ad",
}
PAID_TRAFFIC_SOURCES = {"Facebook", "Instagram", "Google", "TikTok", "Ad"}


@dataclass(frozen=True)
class ShoplineConfig:
    base_url: str = ""
    access_token: str = ""
    store_domain: str = ""
    orders_path: str = ""
    products_path: str = ""
    token_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    timeout_seconds: float = 12.0
    default_currency: str = DEFAULT_CURRENCY
    timezone_name: str = DEFAULT_TIMEZONE
    conversion_traffic_field: str = DEFAULT_CONVERSION_TRAFFIC_FIELD
    max_order_pages: int = DEFAULT_MAX_ORDER_PAGES

    @classmethod
    def from_env(cls) -> "ShoplineConfig":
        timeout_raw = os.getenv("SHOPLINE_TIMEOUT_SECONDS", "12")
        max_order_pages_raw = os.getenv("SHOPLINE_MAX_ORDER_PAGES", str(DEFAULT_MAX_ORDER_PAGES))
        api_version = os.getenv("SHOPLINE_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
        try:
            timeout = max(1.0, float(timeout_raw))
        except ValueError:
            timeout = 12.0
        try:
            max_order_pages = max(1, min(25, int(max_order_pages_raw)))
        except ValueError:
            max_order_pages = DEFAULT_MAX_ORDER_PAGES

        raw_base_url = os.getenv("SHOPLINE_API_BASE_URL", "").strip()
        raw_orders_path = os.getenv("SHOPLINE_ORDERS_ENDPOINT", "").strip()
        raw_products_path = os.getenv("SHOPLINE_PRODUCTS_ENDPOINT", "").strip()

        return cls(
            base_url=normalize_base_url(raw_base_url, api_version),
            access_token=os.getenv("SHOPLINE_ACCESS_TOKEN", "").strip(),
            store_domain=os.getenv("SHOPLINE_STORE_DOMAIN", "").strip(),
            orders_path=normalize_endpoint_path(raw_orders_path, "orders"),
            products_path=normalize_endpoint_path(raw_products_path, "products"),
            token_header=os.getenv("SHOPLINE_TOKEN_HEADER", "Authorization").strip()
            or "Authorization",
            auth_prefix=os.getenv("SHOPLINE_AUTH_PREFIX", "Bearer").strip(),
            timeout_seconds=timeout,
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
            "productsEndpoint": self.config.products_path,
            "tokenHeader": self.config.token_header,
            "tokenPreview": mask_secret(self.config.access_token),
            "defaultCurrency": self.config.default_currency,
            "timezoneName": self.config.timezone_name,
            "trafficConfigured": bool(load_traffic_from_env()),
            "conversionTrafficField": self.config.conversion_traffic_field,
            "maxOrderPages": self.config.max_order_pages,
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
            return {
                "items": sample_orders(days, today=today, currency=self.config.default_currency),
                "source": "sample",
                "error": None,
            }

        start = today - timedelta(days=max(days - 1, 0))
        params = build_order_query_params(start, today, timezone_name=self.config.timezone_name)
        try:
            orders = []
            seen_page_info = set()
            for _ in range(self.config.max_order_pages):
                payload, headers = self.request_json_with_headers(
                    self.config.orders_path, params=params
                )
                orders.extend(normalize_shopline_orders(payload, self.config.default_currency))
                page_info = next_page_info_from_link(headers.get("Link") or headers.get("link", ""))
                if not page_info or page_info in seen_page_info:
                    break
                seen_page_info.add(page_info)
                params = {"limit": str(ORDER_PAGE_LIMIT), "page_info": page_info}
            return {"items": orders, "source": "live", "error": None}
        except Exception as exc:  # pragma: no cover - network-specific branch
            return {
                "items": sample_orders(days, today=today, currency=self.config.default_currency),
                "source": "sample",
                "error": f"{exc.__class__.__name__}: {exc}",
            }

    def load_products(self, today: date | None = None) -> dict[str, Any]:
        today = today or current_dashboard_date(self.config.timezone_name)
        if not self.config.has_credentials or not self.config.products_path:
            return {
                "items": sample_products(today=today, currency=self.config.default_currency),
                "source": "sample",
                "error": None,
            }

        try:
            payload = self.request_json(self.config.products_path, params={"limit": "50"})
            products = normalize_shopline_products(payload, self.config.default_currency)
            return {"items": products, "source": "live", "error": None}
        except Exception as exc:  # pragma: no cover - network-specific branch
            return {
                "items": sample_products(today=today, currency=self.config.default_currency),
                "source": "sample",
                "error": f"{exc.__class__.__name__}: {exc}",
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

        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw), dict(response.headers.items())

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
) -> dict[str, Any]:
    client = client or ShoplineClient()
    today = today or current_dashboard_date(client.config.timezone_name)
    days = resolve_range_days(range_key)

    current_start = today - timedelta(days=days - 1)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start - timedelta(days=1)

    current_orders_result = client.load_orders(days, today=today)
    previous_orders_result = client.load_orders(days, today=previous_end)
    products_result = client.load_products(today=today)
    products = list(products_result["items"])

    current_orders = filter_orders_by_window(
        list(current_orders_result["items"]), current_start, today
    )
    previous_orders = filter_orders_by_window(
        list(previous_orders_result["items"]), previous_start, previous_end
    )
    traffic_overrides = load_traffic_from_env()
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
    ga4_config = Ga4Config.from_env()
    ga4_current = load_ga4_traffic_for_window(current_start, today)
    ga4_previous = load_ga4_traffic_for_window(previous_start, previous_end)
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
    current_traffic = merge_traffic_series(current_traffic, current_ga4_rows)
    previous_traffic = merge_traffic_series(previous_traffic, previous_ga4_rows)

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
    series = build_series(current_start, today, current_orders, current_traffic)
    channels = build_channels(current_orders)
    cost_config = CostConfig.from_env()
    ad_spend = load_ad_spend_from_env()
    profit = build_profit_summary(current_orders, channels, cost_config, ad_spend)
    ad_performance = build_ad_performance(channels, ad_spend)
    campaigns = build_campaign_breakdown(current_orders)
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
            previous_orders_result.get("error"),
            products_result.get("error"),
            ga4_current.error,
            ga4_previous.error,
        )
        if error
    ]
    currency = detect_currency(current_orders, products, client.config.default_currency)

    return {
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
        },
        "currency": currency,
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
        "channels": channels,
        "profit": profit,
        "adPerformance": ad_performance,
        "campaigns": campaigns,
        "customers": customers,
        "orderStatus": order_status,
        "products": product_rows,
        "orders": build_recent_orders(filter_orders_by_window(current_orders, today, today)),
        "alerts": build_alerts_v2(
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
        ),
        "events": build_events(source_mode, current_kpis, errors, client.config.timezone_name),
        "connector": client.connector_status(),
    }


def resolve_range_days(range_key: str) -> int:
    return SUPPORTED_RANGES.get(range_key, SUPPORTED_RANGES["7d"])


def source_mode_from_results(*results: dict[str, Any]) -> str:
    sources = {result.get("source") for result in results}
    if sources == {"live"}:
        return "live"
    if "live" in sources:
        return "mixed"
    return "sample"


def source_label(mode: str) -> str:
    return {
        "live": "实时接口数据",
        "mixed": "混合数据",
        "sample": "示例数据",
    }.get(mode, "示例数据")


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
) -> list[dict[str, Any]]:
    default_currency = (default_currency or DEFAULT_CURRENCY).upper()
    records = extract_collection(payload, ["orders", "order_list", "items", "data", "results"])
    orders = []
    for index, record in enumerate(records, start=1):
        if isinstance(record, dict):
            orders.append(normalize_order(record, index, default_currency))
    return orders


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

    normalized_source, raw_source = extract_order_traffic_source(order)
    campaign = extract_order_marketing_params(order, raw_source, normalized_source)

    return {
        "id": str(
            pick(order, "id", "order_id", "orderNo", "order_no", "name", default=f"sample-{index}")
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
        "source": normalized_source,
        "sourceRaw": raw_source or normalized_source,
        "campaign": campaign,
        "market": str(pick(order, "country", "market", "shipping_country", default="Online")),
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
    if "email" in compact or "newsletter" in compact or "edm" in tokens:
        return "Email"
    if "organic" in compact or "natural" in compact or "seo" in tokens:
        return "Organic"
    if "direct" in compact or "(direct)" in compact or "shopline" in compact or "website" in compact:
        return "Direct"
    if any(token in tokens for token in {"ad", "ads", "paid", "cpc", "ppc", "campaign"}):
        return "Ad"

    return raw[:1].upper() + raw[1:] if raw[:1].islower() else raw


def normalize_phone_key(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    return digits[-12:] if digits else ""


def extract_order_traffic_source(order: dict[str, Any]) -> tuple[str, str]:
    candidates = [
        ("utm_source", str(pick(order, "utm_source", default="")).strip()),
        ("utm_medium", str(pick(order, "utm_medium", default="")).strip()),
        ("source_url", str(pick(order, "source_url", default="")).strip()),
        ("referring_site", str(pick(order, "referring_site", default="")).strip()),
        ("landing_site", str(pick(order, "landing_site", default="")).strip()),
        ("source_identifier", str(pick(order, "source_identifier", default="")).strip()),
        ("source", str(pick(order, "source", default="")).strip()),
        ("channel", str(pick(order, "channel", default="")).strip()),
        ("sales_channel", str(pick(order, "sales_channel", default="")).strip()),
        ("source_name", str(pick(order, "source_name", default="")).strip()),
    ]

    for field, value in candidates:
        if not value:
            continue
        source = infer_traffic_source(field, value)
        if source:
            return source, value

    fallback_raw = str(
        pick(order, "source_url", "referring_site", "landing_site", "source_identifier", default="")
    ).strip()
    if not fallback_raw:
        fallback_raw = "Direct"
    return "Direct", fallback_raw


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
    parsed = urllib.parse.urlparse(url_value)
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

    if "facebook.com" in host or host.endswith("fb.com"):
        return "Facebook"
    if "instagram.com" in host:
        return "Instagram"
    if "tiktok.com" in host:
        return "TikTok"
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

    return ""


def extract_order_marketing_params(
    order: dict[str, Any],
    raw_source: str = "",
    normalized_source: str = "",
) -> dict[str, str]:
    query = collect_marketing_query_params(order, raw_source)
    values = {
        "channel": normalized_source or normalize_marketing_source(raw_source),
        "campaignId": first_marketing_value(
            order,
            query,
            "campaign_id",
            "campaignId",
            "campaignID",
            "fb_campaign_id",
            "utm_id",
        ),
        "adsetId": first_marketing_value(
            order,
            query,
            "adset_id",
            "ad_set_id",
            "adsetId",
            "adSetId",
            "fb_adset_id",
        ),
        "adId": first_marketing_value(
            order,
            query,
            "ad_id",
            "adId",
            "adID",
            "fb_ad_id",
        ),
        "utmCampaign": first_marketing_value(order, query, "utm_campaign"),
        "utmContent": first_marketing_value(order, query, "utm_content"),
        "utmSource": first_marketing_value(order, query, "utm_source"),
        "utmMedium": first_marketing_value(order, query, "utm_medium"),
        "utmTerm": first_marketing_value(order, query, "utm_term"),
    }
    values["hasCampaignParams"] = "true" if any(
        values.get(key)
        for key in ("campaignId", "adsetId", "adId", "utmCampaign", "utmContent")
    ) else "false"
    return values


def collect_marketing_query_params(order: dict[str, Any], raw_source: str = "") -> dict[str, str]:
    query: dict[str, str] = {}
    url_candidates = [
        raw_source,
        str(pick(order, "source_url", default="") or ""),
        str(pick(order, "referring_site", default="") or ""),
        str(pick(order, "landing_site", default="") or ""),
        str(pick(order, "source_identifier", default="") or ""),
    ]
    for candidate in url_candidates:
        for key, value in parse_query_values(candidate).items():
            query.setdefault(key, value)
    return query


def parse_query_values(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}

    parsed = urllib.parse.urlparse(text)
    raw_query = parsed.query
    if not raw_query and "=" in text:
        raw_query = text.split("?", 1)[-1]
    if not raw_query:
        return {}

    result: dict[str, str] = {}
    for key, values in urllib.parse.parse_qs(raw_query, keep_blank_values=False).items():
        if values:
            result[str(key).strip()] = str(values[0]).strip()
    return result


def first_marketing_value(
    order: dict[str, Any],
    query: dict[str, str],
    *keys: str,
) -> str:
    for key in keys:
        value = pick(order, key, default="")
        if value not in (None, ""):
            return str(value).strip()
    for key in keys:
        if key in query and query[key]:
            return str(query[key]).strip()
    return ""


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
    try:
        return Ga4TrafficResult(rows=fetch_ga4_traffic_series(config, start, end))
    except Exception as exc:  # pragma: no cover - network and credential specific branch
        return Ga4TrafficResult(rows=[], error=f"GA4: {exc.__class__.__name__}: {exc}")


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


def build_channels(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    total_revenue = sum(float(order.get("total", 0)) for order in orders) or 1
    for order in orders:
        source = str(order.get("source") or "").strip()
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = normalize_marketing_source(order.get("sourceRaw") or source)
        if source not in TRAFFIC_SOURCE_BUCKETS:
            source = "Direct"
        bucket = grouped.setdefault(source, {"channel": source, "orders": 0, "revenue": 0.0, "units": 0})
        bucket["orders"] += 1
        bucket["revenue"] += float(order.get("total", 0))
        bucket["units"] += int(order.get("units", 0))

    channels = []
    for bucket in grouped.values():
        orders_count = int(bucket["orders"])
        revenue = float(bucket["revenue"])
        channels.append(
            {
                "channel": bucket["channel"],
                "orders": orders_count,
                "revenue": round_number(revenue),
                "aov": round_number(revenue / orders_count if orders_count else 0),
                "share": round_number(revenue / total_revenue * 100),
            }
        )
    return sorted(channels, key=lambda row: row["revenue"], reverse=True)


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
    revenue = sum(float(order.get("total", 0)) for order in orders)
    product_cost = revenue * max(0.0, min(1.0, costs.product_cost_rate))
    payment_fee = revenue * max(0.0, costs.payment_fee_rate)
    shipping_cost = len(orders) * max(0.0, costs.shipping_cost_per_order)
    ad_cost = sum(float(value) for value in ad_spend.values())
    platform_cost = payment_fee + shipping_cost
    estimated_profit = revenue - product_cost - platform_cost - ad_cost
    margin = (estimated_profit / revenue * 100) if revenue else 0.0
    return {
        "revenue": round_number(revenue),
        "productCost": round_number(product_cost),
        "paymentFee": round_number(payment_fee),
        "shippingCost": round_number(shipping_cost),
        "adCost": round_number(ad_cost),
        "platformCost": round_number(platform_cost),
        "estimatedProfit": round_number(estimated_profit),
        "margin": round_number(margin),
        "productCostRate": round_number(costs.product_cost_rate * 100),
        "paymentFeeRate": round_number(costs.payment_fee_rate * 100),
        "shippingCostPerOrder": round_number(costs.shipping_cost_per_order),
        "channelCount": len(channels),
        "notes": [
            "利润为估算值，基于环境变量的成本参数。",
            "未配置广告花费时，ROAS 只反映已记录支出。",
        ],
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
        spend = float(ad_spend.get(name, 0.0))
        revenue = float(channel.get("revenue", 0.0))
        orders_count = int(channel.get("orders", 0))
        rows.append(
            {
                "channel": name,
                "orders": orders_count,
                "spend": round_number(spend),
                "revenue": round_number(revenue),
                "roas": round_number(revenue / spend if spend else 0.0),
                "cpa": round_number(spend / orders_count if orders_count else 0.0),
            }
        )
    return rows


def build_campaign_breakdown(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    total_revenue = 0.0

    for order in orders:
        channel = normalize_marketing_source(order.get("source") or order.get("sourceRaw") or "")
        campaign = order.get("campaign")
        if not isinstance(campaign, dict):
            campaign = extract_order_marketing_params(
                order,
                str(order.get("sourceRaw") or ""),
                channel,
            )

        has_params = str(campaign.get("hasCampaignParams") or "").lower() == "true"
        if not has_params and channel not in PAID_TRAFFIC_SOURCES:
            continue

        campaign_id = str(campaign.get("campaignId") or "").strip()
        adset_id = str(campaign.get("adsetId") or "").strip()
        ad_id = str(campaign.get("adId") or "").strip()
        utm_campaign = str(campaign.get("utmCampaign") or "").strip()
        utm_content = str(campaign.get("utmContent") or "").strip()
        key = (
            channel,
            campaign_id,
            adset_id,
            ad_id,
            utm_campaign,
            utm_content,
        )
        bucket = grouped.setdefault(
            key,
            {
                "channel": channel,
                "campaignId": campaign_id,
                "adsetId": adset_id,
                "adId": ad_id,
                "utmCampaign": utm_campaign,
                "utmContent": utm_content,
                "utmSource": str(campaign.get("utmSource") or "").strip(),
                "utmMedium": str(campaign.get("utmMedium") or "").strip(),
                "orders": 0,
                "revenue": 0.0,
                "units": 0,
            },
        )
        revenue = float(order.get("total", 0))
        bucket["orders"] += 1
        bucket["revenue"] += revenue
        bucket["units"] += int(order.get("units", 0))
        total_revenue += revenue

    rows = []
    total_revenue = total_revenue or 1.0
    for bucket in grouped.values():
        orders_count = int(bucket["orders"])
        revenue = float(bucket["revenue"])
        rows.append(
            {
                "channel": bucket["channel"],
                "campaignId": bucket["campaignId"],
                "adsetId": bucket["adsetId"],
                "adId": bucket["adId"],
                "utmCampaign": bucket["utmCampaign"],
                "utmContent": bucket["utmContent"],
                "utmSource": bucket["utmSource"],
                "utmMedium": bucket["utmMedium"],
                "orders": orders_count,
                "revenue": round_number(revenue),
                "aov": round_number(revenue / orders_count if orders_count else 0),
                "units": int(bucket["units"]),
                "share": round_number(revenue / total_revenue * 100),
            }
        )
    return sorted(rows, key=lambda row: (row["revenue"], row["orders"]), reverse=True)[:12]


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
                "identified": identified,
            },
        )
        row["orders"] += 1
        row["revenue"] += float(order.get("total", 0))
        created_at = str(order.get("createdAt") or "")
        if created_at > str(row.get("latestOrder") or ""):
            row["latestOrder"] = created_at

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
    if len(series) > 1 and previous_orders >= 5 and order_delta is not None and order_delta <= -30:
        alerts.append(
            {
                "level": "warning",
                "title": "订单下滑",
                "message": f"当前订单 {int(current_orders)} 单，比上期下降 {abs(round_number(order_delta))}%，建议检查广告投放、流量入口和支付链路。",
            }
        )

    low_stock_products = [product for product in products if int(product.get("inventory", 0)) <= 5]
    if low_stock_products:
        alerts.append(
            {
                "level": "warning",
                "title": "库存预警",
                "message": f"{len(low_stock_products)} 个 SKU 库存低于 5 件。",
            }
        )

    hot_low_stock = [
        product
        for product in product_rows
        if int(product.get("units", 0)) >= 5 and int(product.get("inventory", 0)) <= 10
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
    if unpaid_count >= 5 and unpaid_rate >= 20:
        alerts.append(
            {
                "level": "warning",
                "title": "未支付偏高",
                "message": f"未支付订单 {unpaid_count} 单，占比 {round_number(unpaid_rate)}%，建议检查支付失败、弃单或客服跟进。",
            }
        )
    if refunded_count >= 2 and refunded_rate >= 3:
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
        elif roas < 1.5:
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

    if kpis.get("conversion") is not None and float(kpis.get("conversion") or 0) < 1:
        alerts.append(
            {
                "level": "warning",
                "title": "转化率偏低",
                "message": "转化率低于 1%，建议检查落地页、支付链路和流量质量。",
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

    return alerts[:8]


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
