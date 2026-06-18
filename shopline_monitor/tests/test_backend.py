import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from shopline_monitor.env_loader import load_environment
from shopline_monitor.backend import (
    Ga4TrafficResult,
    ShoplineClient,
    ShoplineConfig,
    build_customer_summary,
    build_dashboard_payload,
    build_channels,
    build_alerts_v2,
    build_ad_performance,
    calculate_conversion_rate,
    build_order_query_params,
    build_url,
    ga4_conversion_rate,
    load_traffic_from_env,
    next_page_info_from_link,
    normalize_ga4_rate,
    normalize_base_url,
    normalize_endpoint_path,
    normalize_shopline_orders,
    normalize_shopline_products,
    normalize_marketing_source,
)
from shopline_monitor.server import parse_date_param


class BackendTests(unittest.TestCase):
    def test_normalize_orders_extracts_nested_payload(self):
        payload = {
            "data": {
                "orders": [
                    {
                        "order_id": "1001",
                        "created_at": "2026-06-17T08:30:00Z",
                        "total_price": "129.50",
                        "currency": "USD",
                        "customer": {"name": "Mia Chen"},
                        "source_name": "TikTok",
                        "line_items": [
                            {"title": "Dress", "sku": "D-1", "quantity": 2, "price": "54.00"}
                        ],
                    }
                ]
            }
        }

        orders = normalize_shopline_orders(payload)

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["id"], "1001")
        self.assertEqual(orders[0]["customer"], "Mia Chen")
        self.assertEqual(orders[0]["source"], "TikTok")
        self.assertEqual(orders[0]["sourceRaw"], "TikTok")
        self.assertEqual(orders[0]["units"], 2)
        self.assertEqual(orders[0]["total"], 129.5)

    def test_normalize_orders_prefers_traffic_attribution_over_shopline_source(self):
        payload = {
            "orders": [
                {
                    "order_id": "fb-1",
                    "total_price": "100",
                    "source_name": "Shopline",
                    "source_url": "https://jp-sosove.myshopline.com/products/a?utm_source=fb&utm_medium=cpc",
                },
                {
                    "order_id": "ig-1",
                    "total_price": "80",
                    "source_name": "Shopline",
                    "referring_site": "https://l.instagram.com/",
                },
                {
                    "order_id": "direct-1",
                    "total_price": "60",
                    "source_name": "Shopline",
                },
            ]
        }

        orders = normalize_shopline_orders(payload)

        self.assertEqual([order["source"] for order in orders], ["Facebook", "Instagram", "Direct"])
        self.assertIn("utm_source=fb", orders[0]["sourceRaw"])
        self.assertEqual(orders[1]["sourceRaw"], "https://l.instagram.com/")
        self.assertEqual(orders[2]["sourceRaw"], "Shopline")

    def test_normalize_orders_prefers_platform_over_generic_ad_tag(self):
        payload = {
            "orders": [
                {
                    "order_id": "fb-ads-1",
                    "total_price": "100",
                    "source_name": "Shopline",
                    "source_url": "https://example.com/products/a?utm_source=ad&utm_medium=facebook&fbclid=test-click",
                }
            ]
        }

        orders = normalize_shopline_orders(payload)

        self.assertEqual(orders[0]["source"], "Facebook")
        self.assertIn("fbclid=test-click", orders[0]["sourceRaw"])

    def test_normalize_orders_extracts_customer_profile_fields(self):
        payload = {
            "orders": [
                {
                    "order_id": "cust-1",
                    "created_at": "2026-06-17T08:30:00Z",
                    "total_price": "120",
                    "customer": {
                        "id": "c-100",
                        "name": "Mia Chen",
                        "email": "mia@example.com",
                        "phone": "+81 90 1234 5678",
                    },
                }
            ]
        }

        orders = normalize_shopline_orders(payload)

        self.assertEqual(orders[0]["customer"], "Mia Chen")
        self.assertEqual(orders[0]["customerId"], "c-100")
        self.assertEqual(orders[0]["customerEmail"], "mia@example.com")
        self.assertEqual(orders[0]["customerPhone"], "+81 90 1234 5678")
        self.assertEqual(orders[0]["customerKey"], "c-100")

    def test_customer_summary_reports_loaded_and_missing_customer_fields(self):
        orders = [
            {
                "id": "1001",
                "createdAt": "2026-06-17",
                "customer": "Mia Chen",
                "customerId": "c-100",
                "customerEmail": "mia@example.com",
                "customerPhone": "",
                "customerKey": "c-100",
                "total": 120,
            },
            {
                "id": "1002",
                "createdAt": "2026-06-17",
                "customer": "Mia Chen",
                "customerId": "c-100",
                "customerEmail": "mia@example.com",
                "customerPhone": "",
                "customerKey": "c-100",
                "total": 80,
            },
            {
                "id": "1003",
                "createdAt": "2026-06-17",
                "customer": "Guest",
                "customerId": "",
                "customerEmail": "",
                "customerPhone": "",
                "customerKey": "",
                "total": 40,
            },
        ]

        summary = build_customer_summary(orders)

        self.assertEqual(summary["uniqueCustomers"], 1)
        self.assertEqual(summary["repeatCustomers"], 1)
        self.assertEqual(summary["identifiedOrders"], 2)
        self.assertEqual(summary["unidentifiedOrders"], 1)
        self.assertIn("customer.phone / phone", summary["missingFields"])
        self.assertEqual(summary["topCustomers"][0]["revenue"], 200)

    def test_normalize_marketing_source_groups_common_variants(self):
        self.assertEqual(normalize_marketing_source("fb"), "Facebook")
        self.assertEqual(normalize_marketing_source("Meta Ads"), "Facebook")
        self.assertEqual(normalize_marketing_source("ins"), "Instagram")
        self.assertEqual(normalize_marketing_source("Google Ads"), "Google")
        self.assertEqual(normalize_marketing_source("tt"), "TikTok")
        self.assertEqual(normalize_marketing_source("newsletter"), "Email")
        self.assertEqual(normalize_marketing_source("organic"), "Organic")
        self.assertEqual(normalize_marketing_source("ad"), "Ad")
        self.assertEqual(normalize_marketing_source("Shopline"), "Direct")

    def test_build_channels_groups_last_touch_sources(self):
        orders = [
            {"source": "fb", "total": 100, "units": 1},
            {"source": "Meta", "total": 50, "units": 1},
            {"source": "ins", "total": 75, "units": 1},
            {"source": "Email", "total": 25, "units": 1},
            {"source": "organic", "total": 60, "units": 1},
            {"source": "direct", "total": 30, "units": 1},
            {"source": "ad", "total": 40, "units": 1},
        ]

        channels = build_channels(orders)
        grouped = {row["channel"]: row for row in channels}

        self.assertEqual(grouped["Facebook"]["orders"], 2)
        self.assertEqual(grouped["Instagram"]["orders"], 1)
        self.assertEqual(grouped["Email"]["orders"], 1)
        self.assertEqual(grouped["Organic"]["orders"], 1)
        self.assertEqual(grouped["Direct"]["orders"], 1)
        self.assertEqual(grouped["Ad"]["orders"], 1)

    def test_normalize_products_reads_variant_fallbacks(self):
        payload = {
            "products": [
                {
                    "product_id": "p-1",
                    "name": "Cardigan",
                    "variants": [{"sku": "CARD-1", "price": "68", "inventory": 4}],
                    "status": "active",
                }
            ]
        }

        products = normalize_shopline_products(payload, default_currency="JPY")

        self.assertEqual(products[0]["title"], "Cardigan")
        self.assertEqual(products[0]["sku"], "CARD-1")
        self.assertEqual(products[0]["inventory"], 4)
        self.assertEqual(products[0]["price"], 68)
        self.assertEqual(products[0]["currency"], "JPY")

    def test_dashboard_uses_sample_data_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = build_dashboard_payload("7d", today=date(2026, 6, 17))

        self.assertEqual(payload["source"]["mode"], "sample")
        self.assertEqual(payload["range"]["days"], 7)
        self.assertEqual(len(payload["series"]), 7)
        self.assertGreater(payload["kpis"]["orders"]["value"], 0)
        self.assertIn("SHOPLINE_API_BASE_URL", payload["connector"]["missing"])

    def test_load_environment_reads_dotenv_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "SHOPLINE_API_BASE_URL=https://example.com",
                        'SHOPLINE_TIMEZONE="Asia/Tokyo"',
                        'SHOPLINE_AD_SPEND_JSON={"Facebook":100}',
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"SHOPLINE_API_BASE_URL": "https://existing.example"}, clear=True):
                loaded = load_environment([env_path])

                self.assertEqual(os.environ["SHOPLINE_API_BASE_URL"], "https://existing.example")
                self.assertEqual(os.environ["SHOPLINE_TIMEZONE"], "Asia/Tokyo")
                self.assertEqual(os.environ["SHOPLINE_AD_SPEND_JSON"], '{"Facebook":100}')
                self.assertNotIn("SHOPLINE_API_BASE_URL", loaded)

    def test_traffic_env_drives_conversion_rate(self):
        env = {
            "SHOPLINE_TRAFFIC_JSON": '{"2026-06-17":{"visitors":200,"sessions":250}}',
        }
        with patch.dict(os.environ, env, clear=True):
            traffic = load_traffic_from_env()

        orders = [
            {"createdAt": "2026-06-17", "total": 100},
            {"createdAt": "2026-06-17", "total": 120},
            {"createdAt": "2026-06-16", "total": 80},
        ]

        self.assertEqual(traffic["2026-06-17"]["visitors"], 200)
        self.assertEqual(calculate_conversion_rate(orders, [{"date": "2026-06-17", "visitors": 200}]), 1.0)
        self.assertEqual(
            calculate_conversion_rate(orders, [{"date": "2026-06-17", "sessions": 250}], "sessions"),
            0.8,
        )

    def test_conversion_rate_is_empty_without_real_traffic(self):
        orders = [{"createdAt": "2026-06-17", "total": 100}]

        self.assertIsNone(calculate_conversion_rate(orders, [{"date": "2026-06-17", "visitors": None}]))
        self.assertIsNone(calculate_conversion_rate(orders, []))

    def test_ga4_rate_normalization_supports_fraction_and_percent(self):
        self.assertEqual(normalize_ga4_rate("0.125"), 12.5)
        self.assertEqual(normalize_ga4_rate("12.5"), 12.5)
        self.assertIsNone(normalize_ga4_rate(""))

    def test_ga4_conversion_rate_uses_weighted_sessions(self):
        rows = [
            {"date": "2026-06-17", "sessions": 100, "conversion": 10},
            {"date": "2026-06-18", "sessions": 300, "conversion": 20},
        ]

        self.assertEqual(ga4_conversion_rate(rows), 17.5)

    def test_alerts_include_operational_risks(self):
        orders = [
            {
                "id": "order-1",
                "createdAt": "2026-06-18",
                "status": "unpaid",
                "fulfillmentStatus": "pending",
            }
            for _ in range(8)
        ]
        kpis = {"orders": 8, "conversion": 0.8}
        previous_kpis = {"orders": 20}
        product_rows = [
            {"title": "Hot SKU", "sku": "hot-1", "units": 12, "revenue": 1200, "inventory": 3}
        ]
        ad_performance = [
            {"channel": "Facebook", "orders": 0, "spend": 500, "revenue": 0, "roas": 0}
        ]
        order_status = {
            "counts": {"unpaid": 8, "refunded": 0},
            "rates": {"unpaid": 100, "refunded": 0},
        }
        series = [
            {"date": "2026-06-17", "orders": 20, "keyEvents": 18},
            {"date": "2026-06-18", "orders": 8, "keyEvents": 5},
        ]

        alerts = build_alerts_v2(
            kpis,
            orders,
            [],
            "live",
            [],
            previous_kpis=previous_kpis,
            product_rows=product_rows,
            ad_performance=ad_performance,
            order_status=order_status,
            series=series,
        )
        titles = {alert["title"] for alert in alerts}

        self.assertIn("订单下滑", titles)
        self.assertIn("爆品库存不足", titles)
        self.assertIn("未支付偏高", titles)
        self.assertIn("广告花费异常", titles)
        self.assertIn("转化率偏低", titles)
        self.assertIn("GA4 转化延迟", titles)

    def test_ad_performance_includes_spend_only_channels(self):
        rows = build_ad_performance(
            [{"channel": "Facebook", "orders": 2, "revenue": 300}],
            {"Facebook": 100, "TikTok": 50},
        )
        by_channel = {row["channel"]: row for row in rows}

        self.assertEqual(by_channel["Facebook"]["roas"], 3)
        self.assertEqual(by_channel["TikTok"]["orders"], 0)
        self.assertEqual(by_channel["TikTok"]["spend"], 50)

    def test_build_url_adds_query_params(self):
        url = build_url("https://example.com/api", "/orders", {"limit": "1"})

        self.assertEqual(url, "https://example.com/api/orders?limit=1")

    def test_normalize_shopline_short_config(self):
        bare_base_url = normalize_base_url("jp-sosove.myshopline.com")
        base_url = normalize_base_url("https://jp-sosove.myshopline.com")
        orders_path = normalize_endpoint_path("/orders", "orders")
        products_path = normalize_endpoint_path("/products", "products")

        self.assertEqual(
            bare_base_url,
            "https://jp-sosove.myshopline.com/admin/openapi/v20260301",
        )
        self.assertEqual(
            base_url,
            "https://jp-sosove.myshopline.com/admin/openapi/v20260301",
        )
        self.assertEqual(orders_path, "/orders.json")
        self.assertEqual(products_path, "/products/products.json")

    def test_shopline_config_from_env_normalizes_short_values(self):
        env = {
            "SHOPLINE_API_BASE_URL": "jp-sosove.myshopline.com",
            "SHOPLINE_ACCESS_TOKEN": "token-value",
            "SHOPLINE_ORDERS_ENDPOINT": "/orders",
            "SHOPLINE_PRODUCTS_ENDPOINT": "/products",
            "SHOPLINE_DEFAULT_CURRENCY": "jpy",
            "SHOPLINE_TIMEZONE": "Asia/Tokyo",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ShoplineConfig.from_env()

        self.assertTrue(config.live_ready)
        self.assertEqual(
            config.base_url,
            "https://jp-sosove.myshopline.com/admin/openapi/v20260301",
        )
        self.assertEqual(config.orders_path, "/orders.json")
        self.assertEqual(config.products_path, "/products/products.json")
        self.assertEqual(config.default_currency, "JPY")
        self.assertEqual(config.timezone_name, "Asia/Tokyo")

    def test_order_query_params_include_any_status_and_recent_sort(self):
        params = build_order_query_params(date(2026, 6, 11), date(2026, 6, 17), limit=500)

        self.assertEqual(params["limit"], "100")
        self.assertEqual(params["status"], "any")
        self.assertEqual(params["hidden_order"], "false")
        self.assertEqual(params["sort_condition"], "order_at:desc")
        self.assertTrue(params["created_at_min"].startswith("2026-06-11T00:00:00"))
        self.assertTrue(params["created_at_max"].startswith("2026-06-17T23:59:59"))

    def test_load_orders_uses_shopline_recent_order_query(self):
        class RecordingClient(ShoplineClient):
            def __init__(self):
                super().__init__(
                    ShoplineConfig(
                        base_url="https://store.example/admin/openapi/v20260301",
                        access_token="token-value",
                        orders_path="/orders.json",
                    )
                )
                self.seen_path = None
                self.seen_params = None

            def request_json_with_headers(self, path, params=None):
                self.seen_path = path
                self.seen_params = params
                return {"orders": []}, {}

        client = RecordingClient()
        result = client.load_orders(7, today=date(2026, 6, 17))

        self.assertEqual(result["source"], "live")
        self.assertEqual(client.seen_path, "/orders.json")
        self.assertEqual(client.seen_params["status"], "any")
        self.assertEqual(client.seen_params["sort_condition"], "order_at:desc")
        self.assertTrue(client.seen_params["created_at_min"].startswith("2026-06-11"))
        self.assertTrue(client.seen_params["created_at_max"].startswith("2026-06-17"))

    def test_load_orders_follows_shopline_next_page_link(self):
        class PagingClient(ShoplineClient):
            def __init__(self):
                super().__init__(
                    ShoplineConfig(
                        base_url="https://store.example/admin/openapi/v20260301",
                        access_token="token-value",
                        orders_path="/orders.json",
                        max_order_pages=2,
                    )
                )
                self.seen_params = []

            def request_json_with_headers(self, path, params=None):
                self.seen_params.append(params)
                if len(self.seen_params) == 1:
                    return (
                        {
                            "orders": [
                                {
                                    "order_id": "1001",
                                    "created_at": "2026-06-17T09:20:36+08:00",
                                    "total_price": "100",
                                }
                            ]
                        },
                        {"Link": '<https://store.example/orders.json?limit=100&page_info=next-1>; rel="next"'},
                    )
                return (
                    {
                        "orders": [
                            {
                                "order_id": "1002",
                                "created_at": "2026-06-17T09:19:51+08:00",
                                "total_price": "200",
                            }
                        ]
                    },
                    {},
                )

        client = PagingClient()
        result = client.load_orders(7, today=date(2026, 6, 17))

        self.assertEqual([order["id"] for order in result["items"]], ["1001", "1002"])
        self.assertEqual(client.seen_params[1], {"limit": "100", "page_info": "next-1"})

    def test_next_page_info_from_link_header(self):
        link = (
            '<https://store.example/orders.json?limit=100&page_info=abc%2B123>; rel="next", '
            '<https://store.example/orders.json?limit=100&page_info=old>; rel="previous"'
        )

        self.assertEqual(next_page_info_from_link(link), "abc+123")

    def test_dashboard_fetches_current_and_previous_order_windows_separately(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def __init__(self):
                self.order_calls = []

            def load_orders(self, days, today=None):
                self.order_calls.append((days, today))
                order_date = today.isoformat()
                return {
                    "items": [
                        {
                            "id": f"order-{order_date}",
                            "createdAt": order_date,
                            "customer": "Guest",
                            "source": "Shopline",
                            "market": "JP",
                            "total": 100,
                            "currency": "JPY",
                            "status": "paid",
                            "fulfillmentStatus": "unfulfilled",
                            "units": 1,
                            "items": [],
                        }
                    ],
                    "source": "live",
                    "error": None,
                }

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        client = FakeClient()
        payload = build_dashboard_payload("7d", client=client, today=date(2026, 6, 17))

        self.assertEqual(
            client.order_calls,
            [
                (30, date(2026, 6, 17)),
                (7, date(2026, 6, 10)),
                (7, date(2025, 6, 17)),
            ],
        )
        self.assertEqual(payload["kpis"]["orders"]["value"], 1)

    def test_live_dashboard_does_not_fake_conversion_without_traffic(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def load_orders(self, days, today=None):
                return {
                    "items": [
                        {
                            "id": "order-1",
                            "createdAt": "2026-06-17",
                            "customer": "Guest",
                            "source": "Direct",
                            "market": "JP",
                            "total": 100,
                            "currency": "JPY",
                            "status": "paid",
                            "fulfillmentStatus": "unfulfilled",
                            "units": 1,
                            "items": [],
                        }
                    ],
                    "source": "live",
                    "error": None,
                }

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        with patch.dict(os.environ, {}, clear=True):
            payload = build_dashboard_payload("1d", client=FakeClient(), today=date(2026, 6, 17))

        self.assertIsNone(payload["kpis"]["conversion"]["value"])
        self.assertEqual(payload["kpis"]["conversion"]["note"], "未配置真实访客数")

    def test_live_dashboard_prefers_ga4_conversion_rate(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def load_orders(self, days, today=None):
                return {
                    "items": [
                        {
                            "id": "order-1",
                            "createdAt": "2026-06-17",
                            "customer": "Guest",
                            "source": "Direct",
                            "market": "JP",
                            "total": 100,
                            "currency": "JPY",
                            "status": "paid",
                            "fulfillmentStatus": "unfulfilled",
                            "units": 1,
                            "items": [],
                        }
                    ],
                    "source": "live",
                    "error": None,
                }

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        ga4_current = Ga4TrafficResult(
            rows=[
                {
                    "date": "2026-06-17",
                    "visitors": None,
                    "sessions": 200,
                    "conversion": 12.5,
                    "source": "ga4",
                }
            ],
            error=None,
        )
        ga4_previous = Ga4TrafficResult(
            rows=[
                {
                    "date": "2026-06-16",
                    "visitors": None,
                    "sessions": 100,
                    "conversion": 8.0,
                    "source": "ga4",
                }
            ],
            error=None,
        )
        ga4_empty = Ga4TrafficResult(rows=[], error=None)

        with patch.dict(os.environ, {"GA4_CONVERSION_MODE": "key_event_rate"}, clear=True):
            with patch(
                "shopline_monitor.backend.load_ga4_traffic_for_window",
                side_effect=[ga4_current, ga4_current, ga4_previous, ga4_empty],
            ):
                payload = build_dashboard_payload("1d", client=FakeClient(), today=date(2026, 6, 17))

        self.assertEqual(payload["kpis"]["conversion"]["value"], 12.5)
        self.assertEqual(payload["series"][0]["conversion"], 12.5)
        self.assertEqual(payload["series"][0]["sessions"], 200)
        self.assertIn("analytics", payload)
        self.assertEqual(payload["analytics"]["comparison"][0]["label"], "销售额环比")
        self.assertEqual([row["label"] for row in payload["analytics"]["windows"]], ["近 7 天", "近 30 天"])

    def test_live_dashboard_defaults_to_ga4_key_event_rate(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def load_orders(self, days, today=None):
                return {
                    "items": [
                        {
                            "id": "order-1",
                            "createdAt": today.isoformat(),
                            "customer": "Guest",
                            "source": "Direct",
                            "market": "JP",
                            "total": 100,
                            "currency": "JPY",
                            "status": "paid",
                            "fulfillmentStatus": "unfulfilled",
                            "units": 1,
                            "items": [],
                        }
                    ],
                    "source": "live",
                    "error": None,
                }

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        ga4_current = Ga4TrafficResult(
            rows=[{"date": "2026-06-17", "sessions": 200, "conversion": 12.5, "source": "ga4"}],
            error=None,
        )
        ga4_previous = Ga4TrafficResult(
            rows=[{"date": "2026-06-16", "sessions": 100, "conversion": 8.0, "source": "ga4"}],
            error=None,
        )
        ga4_empty = Ga4TrafficResult(rows=[], error=None)

        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "shopline_monitor.backend.load_ga4_traffic_for_window",
                side_effect=[ga4_current, ga4_current, ga4_previous, ga4_empty],
            ):
                payload = build_dashboard_payload("1d", client=FakeClient(), today=date(2026, 6, 17))

        self.assertEqual(payload["kpis"]["conversion"]["value"], 12.5)
        self.assertEqual(payload["series"][0]["conversion"], 12.5)

    def test_dashboard_supports_single_day_query(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def __init__(self):
                self.order_calls = []

            def load_orders(self, days, today=None):
                self.order_calls.append((days, today))
                order_date = today.isoformat()
                return {
                    "items": [
                        {
                            "id": f"order-{order_date}",
                            "createdAt": order_date,
                            "customer": "Guest",
                            "source": "Shopline",
                            "market": "JP",
                            "total": 100,
                            "currency": "JPY",
                            "status": "paid",
                            "fulfillmentStatus": "unfulfilled",
                            "units": 1,
                            "items": [],
                        }
                    ],
                    "source": "live",
                    "error": None,
                }

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        client = FakeClient()
        payload = build_dashboard_payload("1d", client=client, today=date(2026, 6, 17))

        self.assertEqual(payload["range"]["days"], 1)
        self.assertEqual(len(payload["series"]), 1)
        self.assertEqual(payload["series"][0]["date"], "2026-06-17")
        self.assertEqual(payload["series"][0]["orders"], 1)
        self.assertEqual(payload["series"][0]["revenue"], 100)
        self.assertEqual(
            client.order_calls,
            [
                (30, date(2026, 6, 17)),
                (1, date(2026, 6, 16)),
                (1, date(2025, 6, 17)),
            ],
        )

    def test_dashboard_recent_orders_show_full_end_day_only(self):
        class FakeClient:
            config = ShoplineConfig(default_currency="JPY")

            def load_orders(self, days, today=None):
                if today != date(2026, 6, 17):
                    return {"items": [], "source": "live", "error": None}
                today_orders = [
                    {
                        "id": f"today-{index}",
                        "createdAt": "2026-06-17",
                        "customer": "Guest",
                        "source": "Direct",
                        "sourceRaw": "Direct",
                        "market": "JP",
                        "total": 100,
                        "currency": "JPY",
                        "status": "paid",
                        "fulfillmentStatus": "unfulfilled",
                        "units": 1,
                        "items": [],
                    }
                    for index in range(12)
                ]
                older_orders = [
                    {
                        "id": f"older-{index}",
                        "createdAt": "2026-06-16",
                        "customer": "Guest",
                        "source": "Direct",
                        "sourceRaw": "Direct",
                        "market": "JP",
                        "total": 100,
                        "currency": "JPY",
                        "status": "paid",
                        "fulfillmentStatus": "unfulfilled",
                        "units": 1,
                        "items": [],
                    }
                    for index in range(2)
                ]
                return {"items": today_orders + older_orders, "source": "live", "error": None}

            def load_products(self, today=None):
                return {"items": [], "source": "live", "error": None}

            def connector_status(self):
                return {"configured": True, "missing": []}

        payload = build_dashboard_payload("7d", client=FakeClient(), today=date(2026, 6, 17))

        self.assertEqual(payload["kpis"]["orders"]["value"], 14)
        self.assertEqual(len(payload["orders"]), 12)
        self.assertTrue(all(order["createdAt"] == "2026-06-17" for order in payload["orders"]))

    def test_parse_date_param_accepts_iso_date(self):
        self.assertEqual(parse_date_param("2026-06-17"), date(2026, 6, 17))
        self.assertEqual(parse_date_param("2026-06-17T08:30:00+08:00"), date(2026, 6, 17))
        self.assertIsNone(parse_date_param("not-a-date"))


if __name__ == "__main__":
    unittest.main()
