import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shopline_monitor.env_loader import load_environment
from shopline_monitor.backend import (
    CostConfig,
    Ga4Config,
    Ga4TrafficResult,
    ShoplineClient,
    ShoplineConfig,
    build_customer_summary,
    build_data_reconciliation,
    build_dashboard_payload,
    build_ga4_status,
    build_campaign_breakdown,
    build_channel_analytics,
    build_channels,
    build_alerts_v2,
    build_ad_performance,
    build_attribution_diagnostics,
    build_daily_operating_summary,
    calculate_conversion_rate,
    build_order_query_params,
    build_url,
    ga4_conversion_rate,
    load_traffic_from_env,
    merge_ga4_channel_session_starts,
    next_page_info_from_link,
    normalize_ga4_rate,
    normalize_ga4_channel_rows,
    normalize_ga4_channel,
    normalize_base_url,
    normalize_endpoint_path,
    normalize_shopline_orders,
    normalize_shopline_products,
    normalize_marketing_source,
    probe_ga4_connection,
    apply_dashboard_filters,
    deduplicate_orders,
    clear_data_cache,
    clear_live_data_cache,
)
from shopline_monitor.server import dashboard_auth_enabled, parse_date_param, verify_dashboard_token


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

    def test_normalize_orders_reads_shopline_utm_json_and_landing_query(self):
        payload = {
            "orders": [
                {
                    "order_id": "nested-utm",
                    "landing_site": "https://sosove.com/products/a",
                    "utm_parameters": '{"utm_source":"ad","utm_medium":"facebook","utm_campaign":"launch-1","utm_content":"video-01","utm_term":"summer-dress"}',
                },
                {
                    "order_id": "line-order",
                    "source_url": "https://access.line.me/profile",
                    "landing_site": "https://sosove.com/products/b?utm_source=line&utm_medium=chat",
                },
                {
                    "order_id": "direct-order",
                    "landing_site": "https://sosove.com/products/c",
                },
            ]
        }

        orders = normalize_shopline_orders(payload)

        self.assertEqual([row["source"] for row in orders], ["Facebook", "LINE", "Direct"])
        self.assertEqual(orders[0]["sourceUtm"], "ad")
        self.assertEqual(orders[0]["sourceMedium"], "facebook")
        self.assertEqual(orders[0]["sourceCampaign"], "launch-1")
        self.assertEqual(orders[0]["sourceContent"], "video-01")
        self.assertEqual(orders[0]["sourceTerm"], "summer-dress")
        self.assertEqual(orders[0]["attributionMethod"], "utm")
        self.assertEqual(orders[0]["attributionConfidence"], "high")

    def test_normalize_orders_prefers_shopline_official_last_touch_attribution(self):
        payload = {
            "orders": [
                {"order_id": "smartpush-1", "total_price": "8430"},
                {"order_id": "instagram-1", "total_price": "6880"},
                {"order_id": "google-ads-1", "total_price": "6980"},
            ]
        }
        attribution = {
            "smartpush-1": {
                "order_seq": "smartpush-1",
                "last_interaction": {
                    "last_interaction_source": "Other",
                    "last_referrer_name": "smartpush",
                    "last_referrer_url": None,
                    "last_utm_parameters": {
                        "last_utm_source": "wangao",
                        "last_utm_medium": "email",
                        "last_utm_campaign": "welcome-series",
                    },
                },
            },
            "instagram-1": {
                "order_seq": "instagram-1",
                "last_interaction": {
                    "last_interaction_source": "Social",
                    "last_referrer_name": "Instagram",
                    "last_referrer_url": "https://instagram.com/",
                    "last_utm_parameters": {},
                },
            },
            "google-ads-1": {
                "order_seq": "google-ads-1",
                "last_interaction": {
                    "last_interaction_source": "Search",
                    "last_referrer_name": "GoogleAds",
                    "last_referrer_url": None,
                    "last_utm_parameters": {},
                },
            },
        }

        orders = normalize_shopline_orders(
            payload,
            default_currency="JPY",
            attribution_by_order_id=attribution,
        )

        self.assertEqual(
            [order["source"] for order in orders],
            ["SmartPush", "Instagram", "Google Ads"],
        )
        self.assertEqual(orders[0]["sourceUtm"], "wangao")
        self.assertEqual(orders[0]["sourceMedium"], "email")
        self.assertEqual(orders[0]["sourceCampaign"], "welcome-series")
        self.assertEqual(orders[0]["attributionMethod"], "shopline_attribution")
        self.assertEqual(orders[0]["attributionConfidence"], "high")

    def test_ga4_channels_merge_sessions_with_shopline_orders(self):
        response = SimpleNamespace(
            dimension_headers=[
                SimpleNamespace(name="sessionSource"),
                SimpleNamespace(name="sessionMedium"),
                SimpleNamespace(name="sessionDefaultChannelGroup"),
            ],
            metric_headers=[
                SimpleNamespace(name="sessions"),
                SimpleNamespace(name="activeUsers"),
                SimpleNamespace(name="keyEvents:purchase"),
            ],
            rows=[
                SimpleNamespace(
                    dimension_values=[
                        SimpleNamespace(value="ad"),
                        SimpleNamespace(value="facebook"),
                        SimpleNamespace(value="Unassigned"),
                    ],
                    metric_values=[
                        SimpleNamespace(value="100"),
                        SimpleNamespace(value="80"),
                        SimpleNamespace(value="4"),
                    ],
                ),
                SimpleNamespace(
                    dimension_values=[
                        SimpleNamespace(value="line"),
                        SimpleNamespace(value="chat"),
                        SimpleNamespace(value="Organic Social"),
                    ],
                    metric_values=[
                        SimpleNamespace(value="20"),
                        SimpleNamespace(value="16"),
                        SimpleNamespace(value="1"),
                    ],
                ),
            ],
        )
        ga4_rows = normalize_ga4_channel_rows(response)

        channels = build_channels(
            [{"source": "Facebook", "total": 250, "units": 2}],
            ga4_rows,
        )
        grouped = {row["channel"]: row for row in channels}

        self.assertEqual(grouped["Facebook"]["sessions"], 100)
        self.assertEqual(grouped["Facebook"]["orders"], 1)
        self.assertEqual(grouped["Facebook"]["conversion"], 4)
        self.assertEqual(grouped["Facebook"]["ga4Conversion"], 4)
        self.assertEqual(grouped["Facebook"]["shoplineConversion"], 1)
        self.assertEqual(grouped["LINE"]["sessions"], 20)
        self.assertEqual(grouped["LINE"]["orders"], 0)

    def test_ga4_channel_normalizes_smartpush_aliases_before_email_or_referral(self):
        self.assertEqual(normalize_ga4_channel("smartpush", "email", "Email"), "SmartPush")
        self.assertEqual(normalize_ga4_channel("wangao", "wangao", "Referral"), "SmartPush")
        self.assertEqual(normalize_ga4_channel("newsletter", "email", "Email"), "Email")
        self.assertEqual(
            normalize_ga4_channel("sl_smartads", "facebook", "Paid Social"),
            "Facebook",
        )

    def test_ga4_status_exposes_live_purchase_diagnostics(self):
        status = build_ga4_status(
            Ga4Config(property_id="123", service_account_json="{}"),
            [
                {
                    "date": "2026-06-17",
                    "sessions": 200,
                    "activeUsers": 150,
                    "keyEvents": 8,
                    "conversion": 4,
                }
            ],
            [{"channel": "Facebook", "sessions": 100}],
            start=date(2026, 6, 17),
            end=date(2026, 6, 17),
        )

        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["purchases"], 8)
        self.assertEqual(status["sessions"], 200)
        self.assertEqual(status["channelRows"], 1)

    def test_ga4_channel_sessions_use_additive_session_start_counts(self):
        rows = merge_ga4_channel_session_starts(
            [
                {
                    "channel": "Facebook",
                    "source": "facebook",
                    "medium": "paid_social",
                    "group": "Paid Social",
                    "sessions": 140,
                    "activeUsers": 110,
                    "keyEvents": 6,
                    "adCost": 50,
                }
            ],
            [
                {
                    "source": "facebook",
                    "medium": "paid_social",
                    "group": "Paid Social",
                    "sessions": 100,
                    "activeUsers": 80,
                }
            ],
        )

        self.assertEqual(rows[0]["sessions"], 100)
        self.assertEqual(rows[0]["activeUsers"], 80)
        self.assertEqual(rows[0]["reportedSessions"], 140)
        self.assertEqual(rows[0]["keyEvents"], 6)
        self.assertEqual(rows[0]["adCost"], 50)

    def test_reconciliation_surfaces_ga4_query_error_instead_of_generic_missing(self):
        status = {"status": "error", "label": "GA4 接口异常"}

        reconciliation = build_data_reconciliation([{"id": "order-1"}], [], status)

        self.assertEqual(reconciliation["status"], "error")
        self.assertEqual(reconciliation["label"], "GA4 接口异常")

    def test_ga4_probe_returns_purchase_and_conversion(self):
        rows = [
            {
                "date": "2026-06-17",
                "sessions": 200,
                "activeUsers": 150,
                "keyEvents": 6,
                "conversion": 3.25,
            }
        ]
        env = {
            "GA4_PROPERTY_ID": "123",
            "GA4_SERVICE_ACCOUNT_JSON": "{}",
            "GA4_CONVERSION_MODE": "key_event_rate",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("shopline_monitor.backend.fetch_ga4_traffic_series", return_value=rows):
                result = probe_ga4_connection(date(2026, 6, 17))

        self.assertTrue(result["ok"])
        self.assertEqual(result["purchases"], 6)
        self.assertEqual(result["sessions"], 200)
        self.assertEqual(result["conversion"], 3.25)

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
        self.assertEqual(normalize_marketing_source("smartpush"), "SmartPush")
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

    def test_build_channels_exposes_utm_breakdown(self):
        channels = build_channels(
            [
                {
                    "source": "Facebook",
                    "sourceUtm": "facebook",
                    "sourceMedium": "cpc",
                    "sourceCampaign": "summer-sale",
                    "sourceContent": "video-01",
                    "sourceTerm": "dress",
                    "total": 120,
                    "units": 1,
                },
                {
                    "source": "Facebook",
                    "sourceUtm": "facebook",
                    "sourceMedium": "cpc",
                    "sourceCampaign": "summer-sale",
                    "sourceContent": "video-01",
                    "sourceTerm": "dress",
                    "total": 80,
                    "units": 1,
                },
                {"source": "Facebook", "total": 50, "units": 1},
            ]
        )

        facebook = channels[0]
        self.assertEqual(facebook["utmCombinationCount"], 1)
        self.assertEqual(facebook["utmOrders"], 2)
        self.assertEqual(facebook["utmCoverage"], 66.67)
        self.assertEqual(facebook["utmDetails"][0]["campaign"], "summer-sale")
        self.assertEqual(facebook["utmDetails"][0]["orders"], 2)
        self.assertEqual(facebook["utmDetails"][0]["revenue"], 200)

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

    def test_load_environment_strips_utf8_bom_from_first_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "SHOPLINE_API_BASE_URL=https://example.com\nSHOPLINE_ACCESS_TOKEN=test-token\n",
                encoding="utf-8-sig",
            )

            with patch.dict(os.environ, {}, clear=True):
                loaded = load_environment([env_path])

                self.assertEqual(os.environ["SHOPLINE_API_BASE_URL"], "https://example.com")
                self.assertEqual(os.environ["SHOPLINE_ACCESS_TOKEN"], "test-token")
                self.assertIn("SHOPLINE_API_BASE_URL", loaded)

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
        self.assertEqual(config.attribution_path, "/orders/order_attribution_info.json")
        self.assertEqual(config.products_path, "/products/products.json")
        self.assertEqual(config.default_currency, "JPY")
        self.assertEqual(config.timezone_name, "Asia/Tokyo")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.retry_attempts, 3)

    def test_order_query_params_include_any_status_and_recent_sort(self):
        params = build_order_query_params(date(2026, 6, 11), date(2026, 6, 17), limit=500)

        self.assertEqual(params["limit"], "100")
        self.assertEqual(params["status"], "any")
        self.assertEqual(params["hidden_order"], "false")
        self.assertEqual(params["sort_condition"], "order_at:desc")
        self.assertTrue(params["created_at_min"].startswith("2026-06-11T00:00:00"))
        self.assertTrue(params["created_at_max"].startswith("2026-06-17T23:59:59"))

    def test_shopline_request_retries_transient_timeout(self):
        class FakeResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"orders":[]}'

        client = ShoplineClient(
            ShoplineConfig(
                base_url="https://store.example/admin/openapi/v20260301",
                access_token="token-value",
                orders_path="/orders.json",
                retry_attempts=2,
            )
        )
        with patch(
            "shopline_monitor.backend.urllib.request.urlopen",
            side_effect=[TimeoutError("read timed out"), FakeResponse()],
        ) as urlopen, patch("shopline_monitor.backend.time_module.sleep") as sleep:
            payload, headers = client.request_json_with_headers("/orders.json")

        self.assertEqual(payload, {"orders": []})
        self.assertEqual(headers, {})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

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

    def test_live_order_failure_never_injects_sample_orders(self):
        class FailingClient(ShoplineClient):
            def __init__(self):
                super().__init__(
                    ShoplineConfig(
                        base_url="https://store.example/admin/openapi/v20260301",
                        access_token="token-value",
                        orders_path="/orders.json",
                    )
                )

            def request_json_with_headers(self, path, params=None):
                raise TimeoutError("read timed out")

        result = FailingClient().load_orders(1, today=date(2026, 6, 17))

        self.assertEqual(result["source"], "error")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["normalizedCount"], 0)
        self.assertIn("实时订单拉取失败", result["error"])

    def test_live_order_failure_keeps_last_successful_real_data(self):
        clear_data_cache()
        client = ShoplineClient(
            ShoplineConfig(
                base_url="https://store.example/admin/openapi/v20260301",
                access_token="token-value",
                orders_path="/orders.json",
            )
        )
        payload = {
            "orders": [
                {
                    "order_id": "smartpush-live-1",
                    "created_at": "2026-06-17T09:20:36+08:00",
                    "total_price": "8430",
                    "source_name": "smartpush",
                }
            ]
        }
        with patch.object(client, "request_json_with_headers", return_value=(payload, {})):
            first = client.load_orders(1, today=date(2026, 6, 17))

        clear_live_data_cache(date(2026, 6, 17))
        with patch.object(
            client,
            "request_json_with_headers",
            side_effect=TimeoutError("read timed out"),
        ):
            fallback = client.load_orders(1, today=date(2026, 6, 17))

        self.assertEqual(first["source"], "live")
        self.assertEqual(fallback["source"], "stale")
        self.assertEqual(fallback["items"], first["items"])
        self.assertTrue(fallback["stale"])
        self.assertIn("上次成功数据", fallback["error"])
        clear_data_cache()

    def test_live_refresh_reuses_historical_order_windows(self):
        clear_data_cache()
        client = ShoplineClient(
            ShoplineConfig(
                base_url="https://store.example/admin/openapi/v20260301",
                access_token="token-value",
                orders_path="/orders.json",
            )
        )
        with patch.dict(os.environ, {"SHOPLINE_ORDER_CHUNK_DAYS": "7"}), patch.object(
            client,
            "request_json_with_headers",
            return_value=({"orders": []}, {}),
        ) as request:
            first = client.load_orders(30, today=date(2026, 6, 30))
            clear_live_data_cache(date(2026, 6, 30))
            second = client.load_orders(30, today=date(2026, 6, 30))

        self.assertEqual(first["chunks"], 5)
        self.assertEqual(first["windowCacheHits"], 0)
        self.assertEqual(second["windowCacheHits"], 4)
        self.assertEqual(request.call_count, 6)
        clear_data_cache()

    def test_channel_analytics_exposes_official_smartpush_reconciliation(self):
        orders = [
            {
                "source": "SmartPush",
                "total": 8430,
                "units": 1,
                "attributionMethod": "shopline_attribution",
            },
            {
                "source": "Facebook",
                "total": 6980,
                "units": 1,
                "attributionMethod": "utm",
            },
        ]

        channels = build_channels(orders)
        analytics = build_channel_analytics(orders, channels)
        grouped = {row["channel"]: row for row in channels}

        self.assertEqual(grouped["SmartPush"]["officialOrders"], 1)
        self.assertEqual(grouped["SmartPush"]["inferredOrders"], 0)
        self.assertEqual(grouped["SmartPush"]["orderDetailCount"], 1)
        self.assertEqual(grouped["SmartPush"]["orderDetails"][0]["total"], 8430)
        self.assertEqual(analytics["officialOrders"], 1)
        self.assertEqual(analytics["officialAttributionRate"], 50)
        self.assertEqual(analytics["smartPushOrders"], 1)
        self.assertEqual(analytics["smartPushRevenue"], 8430)

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

    def test_load_orders_enriches_rows_with_bulk_order_attribution(self):
        class AttributionClient(ShoplineClient):
            def __init__(self):
                super().__init__(
                    ShoplineConfig(
                        base_url="https://store.example/admin/openapi/v20260301",
                        access_token="token-value",
                        orders_path="/orders.json",
                        attribution_path="/orders/order_attribution_info.json",
                    )
                )
                self.attribution_payload = None

            def request_json_with_headers(self, path, params=None):
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
                    {},
                )

            def post_json_with_headers(self, path, payload):
                self.attribution_payload = payload
                return (
                    {
                        "data": [
                            {
                                "order_seq": "1001",
                                "last_interaction": {
                                    "last_interaction_source": "Other",
                                    "last_referrer_name": "smartpush",
                                    "last_utm_parameters": {},
                                },
                            }
                        ]
                    },
                    {},
                )

        client = AttributionClient()
        result = client.load_orders(7, today=date(2026, 6, 17))

        self.assertEqual(client.attribution_payload, {"orders": ["1001"]})
        self.assertEqual(result["items"][0]["source"], "SmartPush")
        self.assertEqual(result["attributionRequested"], 1)
        self.assertEqual(result["attributionCount"], 1)
        self.assertIsNone(result["attributionError"])

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
        with patch.dict(os.environ, {"GA4_PROPERTY_ID": ""}):
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
                side_effect=[ga4_current, ga4_previous, ga4_empty],
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
                side_effect=[ga4_current, ga4_previous, ga4_empty],
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
        with patch.dict(os.environ, {"GA4_PROPERTY_ID": ""}):
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

        with patch.dict(os.environ, {"GA4_PROPERTY_ID": ""}):
            payload = build_dashboard_payload("7d", client=FakeClient(), today=date(2026, 6, 17))

        self.assertEqual(payload["kpis"]["orders"]["value"], 14)
        self.assertEqual(len(payload["orders"]), 12)
        self.assertTrue(all(order["createdAt"] == "2026-06-17" for order in payload["orders"]))

    def test_global_filters_apply_channel_market_status_and_product(self):
        orders = [
            {
                "source": "Facebook",
                "market": "JP",
                "status": "paid",
                "fulfillmentStatus": "unfulfilled",
                "items": [{"sku": "SKU-1", "title": "Dress"}],
            },
            {
                "source": "Google",
                "market": "US",
                "status": "unpaid",
                "fulfillmentStatus": "pending",
                "items": [{"sku": "SKU-2", "title": "Coat"}],
            },
        ]

        filtered = apply_dashboard_filters(
            orders,
            {"channel": "Facebook", "market": "JP", "status": "paid", "product": "SKU-1"},
        )

        self.assertEqual(filtered, [orders[0]])

    def test_campaign_breakdown_tracks_campaign_adset_and_ad(self):
        rows = build_campaign_breakdown(
            [
                {
                    "source": "Facebook",
                    "sourceCampaign": "C-1",
                    "sourceAdset": "AS-1",
                    "sourceAd": "AD-1",
                    "customerKey": "customer-1",
                    "total": 120,
                },
                {
                    "source": "Facebook",
                    "sourceCampaign": "C-1",
                    "sourceAdset": "AS-1",
                    "sourceAd": "AD-1",
                    "customerKey": "customer-2",
                    "total": 80,
                },
            ]
        )

        self.assertEqual(rows["coverage"], 100)
        self.assertEqual(rows["rows"][0]["orders"], 2)
        self.assertEqual(rows["rows"][0]["customers"], 2)
        self.assertEqual(rows["rows"][0]["revenue"], 200)

    def test_attribution_diagnostics_finds_missing_invalid_and_click_mapping(self):
        orders = [
            {
                "id": "bad-1",
                "createdAt": "2026-06-17",
                "customer": "Mia",
                "source": "Facebook",
                "sourceUtm": "FB",
                "sourceMedium": "Paid Social",
                "sourceCampaign": "",
                "sourceCampaignId": "",
                "clickIds": {"fbclid": "click-123456789"},
                "total": 120,
            },
            {
                "id": "good-1",
                "createdAt": "2026-06-17",
                "customer": "Yuki",
                "source": "Google",
                "sourceUtm": "google",
                "sourceMedium": "cpc",
                "sourceCampaign": "summer-sale",
                "sourceCampaignId": "g-campaign",
                "clickIds": {},
                "total": 90,
            },
            {
                "id": "direct-1",
                "createdAt": "2026-06-17",
                "customer": "Guest",
                "source": "Direct",
                "sourceUtm": "",
                "sourceMedium": "",
                "sourceCampaign": "",
                "sourceCampaignId": "",
                "clickIds": {},
                "total": 50,
            },
        ]
        mapping = '{"fbclid:click-123456789":{"campaignId":"meta-42","campaignName":"summer-meta"}}'

        with patch.dict(os.environ, {"SHOPLINE_CLICK_CAMPAIGN_MAP_JSON": mapping}):
            result = build_attribution_diagnostics(orders)

        self.assertEqual(result["summary"]["totalOrders"], 3)
        self.assertEqual(result["summary"]["completeUtmOrders"], 1)
        self.assertEqual(result["summary"]["missingOrders"], 2)
        self.assertEqual(result["summary"]["mappedClickOrders"], 1)
        self.assertGreaterEqual(result["summary"]["invalidOrders"], 1)
        self.assertTrue(any(row["code"] == "source_alias" for row in result["issues"]))
        self.assertEqual(result["clickMappings"][0]["campaignId"], "meta-42")

    def test_daily_operating_summary_exposes_channel_movement_profit_and_product_anomaly(self):
        orders = [
            {
                "id": "today-1",
                "createdAt": "2026-06-17",
                "source": "Facebook",
                "total": 300,
                "refundTotal": 0,
                "discounts": 0,
                "taxTotal": 0,
                "market": "JP",
                "items": [{"title": "Dress", "sku": "D-1", "quantity": 4, "revenue": 300}],
            },
            {
                "id": "yesterday-1",
                "createdAt": "2026-06-16",
                "source": "Google",
                "total": 120,
                "refundTotal": 0,
                "discounts": 0,
                "taxTotal": 0,
                "market": "JP",
                "items": [{"title": "Coat", "sku": "C-1", "quantity": 1, "revenue": 120}],
            },
        ]
        products = [{"title": "Dress", "sku": "D-1", "inventory": 5}]

        summary = build_daily_operating_summary(
            date(2026, 6, 17),
            orders,
            products,
            CostConfig(product_cost_rate=0.35, payment_fee_rate=0.036, shipping_cost_per_order=10),
            {},
        )

        self.assertEqual(summary["metrics"]["orders"], 1)
        self.assertEqual(summary["growthChannels"][0]["channel"], "Facebook")
        self.assertEqual(summary["declineChannels"][0]["channel"], "Google")
        self.assertEqual(summary["abnormalProducts"][0]["type"], "stock")
        self.assertGreater(summary["metrics"]["estimatedProfit"], 0)

    def test_alerts_are_decorated_with_action_metadata(self):
        alerts = build_alerts_v2(
            {"orders": 0, "conversion": 0.2},
            [],
            [],
            "sample",
            [],
        )

        self.assertTrue(alerts)
        self.assertTrue(alerts[0]["id"])
        self.assertEqual(alerts[0]["actions"], ["orders", "channels", "dismiss", "notify"])

    def test_order_deduplication_keeps_first_order(self):
        unique, duplicates = deduplicate_orders(
            [{"id": "1", "total": 100}, {"id": "1", "total": 120}, {"id": "2", "total": 90}]
        )

        self.assertEqual([row["id"] for row in unique], ["1", "2"])
        self.assertEqual(duplicates, 1)

    def test_optional_dashboard_authentication(self):
        with patch.dict(os.environ, {"DASHBOARD_ACCESS_TOKEN": "secret-token"}):
            self.assertTrue(dashboard_auth_enabled())
            self.assertTrue(verify_dashboard_token("secret-token"))
            self.assertFalse(verify_dashboard_token("wrong"))
        with patch.dict(os.environ, {"DASHBOARD_ACCESS_TOKEN": ""}):
            self.assertFalse(dashboard_auth_enabled())
            self.assertTrue(verify_dashboard_token(""))

    def test_parse_date_param_accepts_iso_date(self):
        self.assertEqual(parse_date_param("2026-06-17"), date(2026, 6, 17))
        self.assertEqual(parse_date_param("2026-06-17T08:30:00+08:00"), date(2026, 6, 17))
        self.assertIsNone(parse_date_param("not-a-date"))


if __name__ == "__main__":
    unittest.main()
