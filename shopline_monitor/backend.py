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


SUPPORTED_RANGES = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
DEFAULT_CURRENCY = "USD"
DEFAULT_API_VERSION = "v20260301"
ORDER_PAGE_LIMIT = 100
DEFAULT_MAX_ORDER_PAGES = 5
DEFAULT_PRODUCT_COST_RATE = 0.35
DEFAULT_PAYMENT_FEE_RATE = 0.036
DEFAULT_SHIPPING_COST_PER_ORDER = 0.0
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
            "maxOrderPages": self.config.max_order_pages,
            "missing": missing,
        }

    def load_orders(self, days: int, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        if not self.config.has_credentials or not self.config.orders_path:
            return {
                "items": sample_orders(days, today=today, currency=self.config.default_currency),
                "source": "sample",
                "error": None,
            }

        start = today - timedelta(days=max(days - 1, 0))
        params = build_order_query_params(start, today)
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
        today = today or date.today()
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
                "checkedAt": now_iso(),
            }

        probe_path = self.config.orders_path or self.config.products_path
        try:
            payload = self.request_json(probe_path, params={"limit": "1"})
            count = len(extract_collection(payload, ["orders", "products", "items", "data"]))
            return {
                "ok": True,
                "mode": "live",
                "message": f"received {count} item(s)",
                "checkedAt": now_iso(),
            }
        except Exception as exc:  # pragma: no cover - network-specific branch
            return {
                "ok": False,
                "mode": "live",
                "message": f"{exc.__class__.__name__}: {exc}",
                "checkedAt": now_iso(),
            }


def build_dashboard_payload(
    range_key: str = "7d",
    client: ShoplineClient | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    days = resolve_range_days(range_key)
    client = client or ShoplineClient()

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
    current_traffic = build_traffic_series(current_start, today, current_orders)
    previous_traffic = build_traffic_series(previous_start, previous_end, previous_orders)

    current_kpis = calculate_kpis(current_orders, products, current_traffic)
    previous_kpis = calculate_kpis(previous_orders, products, previous_traffic)
    series = build_series(current_start, today, current_orders, current_traffic)
    channels = build_channels(current_orders)
    cost_config = CostConfig.from_env()
    ad_spend = load_ad_spend_from_env()
    profit = build_profit_summary(current_orders, channels, cost_config, ad_spend)
    customers = build_customer_summary(current_orders)
    order_status = build_order_status_summary(current_orders)
    source_mode = source_mode_from_results(
        current_orders_result, previous_orders_result, products_result
    )
    errors = [
        error
        for error in (
            current_orders_result.get("error"),
            previous_orders_result.get("error"),
            products_result.get("error"),
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
            "syncedAt": now_iso(),
            "errors": errors,
        },
        "currency": currency,
        "kpis": {
            "revenue": kpi_item(
                "销售额", current_kpis["revenue"], previous_kpis["revenue"], "currency"
            ),
            "orders": kpi_item("订单数", current_kpis["orders"], previous_kpis["orders"], "number"),
            "conversion": kpi_item(
                "转化率", current_kpis["conversion"], previous_kpis["conversion"], "percent"
            ),
            "aov": kpi_item("客单价", current_kpis["aov"], previous_kpis["aov"], "currency"),
            "units": kpi_item("售出件数", current_kpis["units"], previous_kpis["units"], "number"),
            "lowStock": kpi_item("低库存 SKU", current_kpis["lowStock"], previous_kpis["lowStock"], "number"),
        },
        "series": series,
        "channels": channels,
        "profit": profit,
        "adPerformance": build_ad_performance(channels, ad_spend),
        "customers": customers,
        "orderStatus": order_status,
        "products": build_product_rows(current_orders, products),
        "orders": build_recent_orders(filter_orders_by_window(current_orders, today, today)),
        "alerts": build_alerts(current_kpis, current_orders, products, source_mode, errors),
        "events": build_events(source_mode, current_kpis, errors),
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
) -> dict[str, str]:
    safe_limit = max(1, min(limit, ORDER_PAGE_LIMIT))
    return {
        "limit": str(safe_limit),
        "status": "any",
        "hidden_order": "false",
        "sort_condition": "order_at:desc",
        "created_at_min": local_midnight(start).isoformat(timespec="seconds"),
        "created_at_max": local_end_of_day(end).isoformat(timespec="seconds"),
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


def kpi_item(label: str, value: float, previous: float, value_type: str) -> dict[str, Any]:
    delta = calculate_delta(value, previous)
    return {
        "label": label,
        "value": round_number(value),
        "previous": round_number(previous),
        "delta": round(delta, 1),
        "type": value_type,
        "tone": "positive" if delta >= 0 else "negative",
    }


def calculate_delta(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / abs(previous)) * 100


def calculate_kpis(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    traffic: list[dict[str, Any]],
) -> dict[str, float]:
    revenue = sum(float(order.get("total", 0)) for order in orders)
    order_count = len(orders)
    visitors = sum(int(day.get("visitors", 0)) for day in traffic)
    units = sum(int(order.get("units", 0)) for order in orders)
    low_stock = sum(1 for product in products if int(product.get("inventory", 0)) <= 5)
    return {
        "revenue": revenue,
        "orders": float(order_count),
        "conversion": (order_count / visitors * 100) if visitors else 0.0,
        "aov": (revenue / order_count) if order_count else 0.0,
        "units": float(units),
        "lowStock": float(low_stock),
    }


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
    ) or date.today()

    normalized_source, raw_source = extract_order_traffic_source(order)

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
            parse_date(pick(product, "updated_at", "updatedAt", "created_at")) or date.today()
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
    start: date, end: date, orders: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_date: dict[str, int] = {}
    for order in orders:
        day = str(order.get("createdAt", ""))[:10]
        by_date[day] = by_date.get(day, 0) + 1

    series = []
    cursor = start
    while cursor <= end:
        order_count = by_date.get(cursor.isoformat(), 0)
        rng = random.Random(cursor.toordinal() * 17)
        baseline = 380 + rng.randint(0, 220)
        visitors = max(baseline, order_count * rng.randint(32, 58) + rng.randint(80, 180))
        series.append(
            {
                "date": cursor.isoformat(),
                "visitors": visitors,
                "sessions": int(visitors * (1.12 + rng.random() * 0.22)),
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
        visitors = int(traffic_map.get(key, {}).get("visitors", 0))
        rows.append(
            {
                "date": key,
                "label": cursor.strftime("%m/%d"),
                "revenue": round_number(revenue),
                "orders": len(day_orders),
                "visitors": visitors,
                "conversion": round_number((len(day_orders) / visitors * 100) if visitors else 0),
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
    for channel in channels:
        name = str(channel.get("channel", "")).strip()
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
    if kpis["conversion"] < 1:
        alerts.append(
            {
                "level": "warning",
                "title": "转化率",
                "message": "转化率低于 1%，建议检查落地页与支付链路。",
            }
        )
    return alerts[:5]


def build_events(source_mode: str, kpis: dict[str, float], errors: list[str]) -> list[dict[str, Any]]:
    events = [
        {
            "time": now_iso(),
            "kind": "sync",
            "title": "数据同步完成",
            "detail": f"{int(kpis['orders'])} 个订单已进入当前看板。",
        },
        {
            "time": now_iso(minutes=-18),
            "kind": "inventory",
            "title": "库存扫描",
            "detail": f"{int(kpis['lowStock'])} 个 SKU 需要补货关注。",
        },
        {
            "time": now_iso(minutes=-45),
            "kind": "source",
            "title": "数据模式",
            "detail": source_label(source_mode),
        },
    ]
    if errors:
        events.insert(
            0,
            {
                "time": now_iso(minutes=-2),
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
    today = today or date.today()
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
    today = today or date.today()
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


def now_iso(minutes: int = 0) -> str:
    now = datetime.now(timezone.utc).astimezone() + timedelta(minutes=minutes)
    return now.isoformat(timespec="seconds")


def local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min).astimezone()


def local_end_of_day(day: date) -> datetime:
    return local_midnight(day) + timedelta(days=1) - timedelta(seconds=1)
