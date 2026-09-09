import os
import tempfile
import unittest
import urllib.error
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
    build_profit_summary,
    build_sync_quality,
    build_campaign_breakdown,
    build_channel_analytics,
    build_channels,
    build_focus_channels,
    build_alerts_v2,
    build_ad_performance,
    build_attribution_diagnostics,
    build_daily_operating_summary,
    calculate_conversion_rate,
    build_order_query_params,
    build_url,
    ga4_conversion_rate,
    ga4_purchase_count,
    load_traffic_from_env,
    merge_ga4_channel_session_starts,
    merge_ga4_transaction_diagnostics,
    next_page_info_from_link,
    normalize_ga4_rate,
    normalize_ga4_channel_rows,
    normalize_ga4_channel,
    normalize_ga4_transaction_rows,
    normalize_base_url,
    normalize_endpoint_path,
    normalize_shopline_orders,
    normalize_shopline_products,
    normalize_marketing_source,
    probe_ga4_connection,
    reconcile_ga4_channel_sessions,
    reconcile_ga4_transaction_aliases,
    apply_dashboard_filters,
    deduplicate_orders,
    clear_data_cache,
    clear_live_data_cache,
    summarize_series_window,
)
from shopline_monitor.server import dashboard_auth_enabled, parse_date_param, verify_dashboard_token


class BackendTests(unittest.TestCase):
    ENV_PREFIXES = ("SHOPLINE_", "GA4_", "DASHBOARD_")
    ENV_KEYS = {"GOOGLE_APPLICATION_CREDENTIALS"}

    def setUp(self):
        self._project_environment = {
            key: value
            for key, value in os.environ.items()
            if key.startswith(self.ENV_PREFIXES) or key in self.ENV_KEYS
        }
        for key in self._project_environment:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith(self.ENV_PREFIXES) or key in self.ENV_KEYS:
                os.environ.pop(key, None)
        os.environ.update(self._project_environment)

    def test_normalize_orders_extracts_nested_payload(self):
        payload = {
            "data": {
                "orders": [
                    {
                        "order_id": "1001",
                        "name": "JCJP1001",
                        "checkout_id": "checkout-1001",
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
        self.assertEqual(orders[0]["orderNumber"], "JCJP1001")
        self.assertEqual(orders[0]["checkoutId"], "checkout-1001")
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

    def test_shopline_official_attribution_prefers_yahoo_referrer_url_over_google_label(self):
        orders = normalize_shopline_orders(
            {"orders": [{"order_id": "yahoo-search", "total_price": "11789"}]},
            default_currency="JPY",
            attribution_by_order_id={
                "yahoo-search": {
                    "order_seq": "yahoo-search",
                    "last_interaction": {
                        "last_interaction_source": "Search",
                        "last_referrer_name": "Google",
                        "last_referrer_url": "https://search.yahoo.co.jp/",
                        "last_utm_parameters": None,
                    },
                }
            },
        )

        self.assertEqual(orders[0]["source"], "Yahoo")
        self.assertEqual(orders[0]["sourceRaw"], "https://search.yahoo.co.jp/")

    def test_shopline_official_attribution_recognizes_smartpush_in_utm_medium(self):
        orders = normalize_shopline_orders(
            {
                "orders": [
                    {"order_id": "smartpush-medium", "total_price": "11477"},
                    {"order_id": "regular-email", "total_price": "6980"},
                ]
            },
            default_currency="JPY",
            attribution_by_order_id={
                "smartpush-medium": {
                    "order_seq": "smartpush-medium",
                    "last_interaction": {
                        "last_interaction_source": "Other",
                        "last_referrer_name": "Email",
                        "last_referrer_url": None,
                        "last_utm_parameters": {
                            "last_utm_source": "email",
                            "last_utm_medium": "smartpush",
                            "last_utm_campaign": "636727",
                        },
                    },
                },
                "regular-email": {
                    "order_seq": "regular-email",
                    "last_interaction": {
                        "last_interaction_source": "Other",
                        "last_referrer_name": "Email",
                        "last_referrer_url": None,
                        "last_utm_parameters": {
                            "last_utm_source": "newsletter",
                            "last_utm_medium": "email",
                            "last_utm_campaign": "weekly-news",
                        },
                    },
                },
            },
        )

        self.assertEqual([order["source"] for order in orders], ["SmartPush", "Email"])
        self.assertEqual(orders[0]["sourceUtm"], "email")
        self.assertEqual(orders[0]["sourceMedium"], "smartpush")
        self.assertEqual(orders[0]["sourceCampaign"], "636727")
        self.assertEqual(orders[0]["attributionMethod"], "shopline_attribution")

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

    def test_ga4_transactions_are_deduplicated_by_transaction_id(self):
        response = SimpleNamespace(
            dimension_headers=[
                SimpleNamespace(name="date"),
                SimpleNamespace(name="transactionId"),
            ],
            metric_headers=[SimpleNamespace(name="eventCount")],
            rows=[
                SimpleNamespace(
                    dimension_values=[
                        SimpleNamespace(value="20260617"),
                        SimpleNamespace(value="order-1"),
                    ],
                    metric_values=[SimpleNamespace(value="2")],
                ),
                SimpleNamespace(
                    dimension_values=[
                        SimpleNamespace(value="20260617"),
                        SimpleNamespace(value="order-2"),
                    ],
                    metric_values=[SimpleNamespace(value="1")],
                ),
                SimpleNamespace(
                    dimension_values=[
                        SimpleNamespace(value="20260617"),
                        SimpleNamespace(value="(not set)"),
                    ],
                    metric_values=[SimpleNamespace(value="3")],
                ),
            ],
        )

        diagnostics = normalize_ga4_transaction_rows(response)
        rows = merge_ga4_transaction_diagnostics(
            [{"date": "2026-06-17", "sessions": 200, "keyEvents": 6}],
            diagnostics,
        )

        self.assertEqual(rows[0]["transactions"], 2)
        self.assertEqual(rows[0]["rawPurchaseEvents"], 6)
        self.assertEqual(rows[0]["duplicatePurchaseEvents"], 4)
        self.assertEqual(rows[0]["duplicateTransactionIds"], 1)
        self.assertEqual(rows[0]["missingTransactionIdEvents"], 3)
        self.assertEqual(ga4_purchase_count(rows), 2)
        reconciliation = build_data_reconciliation([{"id": "1"}, {"id": "2"}], rows)
        self.assertEqual(reconciliation["status"], "aligned")

    def test_ga4_transaction_aliases_collapse_shopline_id_and_order_number(self):
        rows = [
            {
                "date": "2026-06-17",
                "transactions": 5,
                "transactionRecords": [
                    {"id": "internal-1", "eventCount": 2},
                    {"id": "JCJP1001", "eventCount": 2},
                    {"id": "internal-2", "eventCount": 1},
                    {"id": "JCJP1002", "eventCount": 1},
                    {"id": "external-3", "eventCount": 1},
                ],
            }
        ]
        orders = [
            {"id": "internal-1", "orderNumber": "JCJP1001"},
            {"id": "internal-2", "orderNumber": "JCJP1002"},
        ]

        reconciled = reconcile_ga4_transaction_aliases(rows, orders)

        self.assertEqual(reconciled[0]["transactions"], 3)
        self.assertEqual(reconciled[0]["rawTransactionIds"], 5)
        self.assertEqual(reconciled[0]["aliasDuplicateTransactionIds"], 2)
        self.assertEqual(reconciled[0]["matchedShoplineTransactions"], 2)
        self.assertEqual(reconciled[0]["unmatchedTransactionIds"], 1)
        self.assertEqual(
            reconciled[0]["purchaseMetric"],
            "shopline_alias_unique_transaction_id",
        )
        self.assertNotIn("transactionRecords", reconciled[0])

    def test_ga4_channel_session_gap_is_reconciled_to_total_sessions(self):
        rows = reconcile_ga4_channel_sessions(
            [
                {
                    "channel": "Facebook",
                    "source": "facebook",
                    "medium": "paid_social",
                    "group": "Paid Social",
                    "sessions": 80,
                    "activeUsers": 60,
                    "keyEvents": 4,
                    "adCost": 0,
                }
            ],
            100,
        )

        self.assertEqual(sum(row["sessions"] for row in rows), 100)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sessions"], 100)
        self.assertEqual(rows[0]["reportedSessions"], 80)
        self.assertEqual(rows[0]["sessionMetric"], "sessions_normalized")

    def test_reconciliation_surfaces_ga4_query_error_instead_of_generic_missing(self):
        status = {"status": "error", "label": "GA4 接口异常"}

        reconciliation = build_data_reconciliation([{"id": "order-1"}], [], status)

        self.assertEqual(reconciliation["status"], "error")
        self.assertEqual(reconciliation["label"], "GA4 接口异常")

    def test_reconciliation_marks_missing_shopline_orders_as_divergent(self):
        reconciliation = build_data_reconciliation(
            [],
            [{"date": "2026-06-17", "transactions": 8, "keyEvents": 12}],
        )

        self.assertEqual(reconciliation["differenceRate"], 100)
        self.assertEqual(reconciliation["status"], "divergent")

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

    def test_shopline_default_timezone_matches_api_order_timestamps(self):
        with patch.dict(os.environ, {}, clear=True):
            config = ShoplineConfig.from_env()

        self.assertEqual(config.timezone_name, "Asia/Shanghai")
        params = build_order_query_params(
            date(2026, 8, 11),
            date(2026, 8, 11),
            timezone_name=config.timezone_name,
        )
        self.assertTrue(params["created_at_min"].endswith("+08:00"))
        self.assertTrue(params["created_at_max"].endswith("+08:00"))

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

    def test_shopline_429_retry_honors_retry_after_header(self):
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
        rate_limit = urllib.error.HTTPError(
            "https://store.example/orders.json",
            429,
            "Too Many Requests",
            {"Retry-After": "3"},
            None,
        )
        with patch(
            "shopline_monitor.backend.urllib.request.urlopen",
            side_effect=[rate_limit, FakeResponse()],
        ), patch("shopline_monitor.backend.time_module.sleep") as sleep:
            payload, _ = client.request_json_with_headers("/orders.json")

        self.assertEqual(payload, {"orders": []})
        sleep.assert_called_once_with(3.0)

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

    def test_channel_order_details_are_not_truncated(self):
        orders = [
            {
                "id": f"instagram-{index}",
                "createdAt": "2026-08-12",
                "source": "Instagram",
                "total": 100,
                "units": 1,
                "status": "paid",
                "attributionMethod": "shopline_attribution",
                "attributionConfidence": "high",
            }
            for index in range(24)
        ]

        instagram = next(
            row for row in build_channels(orders) if row["channel"] == "Instagram"
        )

        self.assertEqual(instagram["orders"], 24)
        self.assertEqual(instagram["orderDetailCount"], 24)
        self.assertEqual(len(instagram["orderDetails"]), 24)

    def test_focus_channels_merges_untracked_google_orders_into_yahoo(self):
        orders = [
            {
                "id": "yahoo-order",
                "createdAt": "2026-08-13",
                "source": "Yahoo",
                "total": 3000,
                "units": 1,
                "status": "paid",
                "attributionMethod": "shopline_attribution",
                "attributionConfidence": "high",
            },
            {
                "id": "google-organic",
                "createdAt": "2026-08-13",
                "source": "Google",
                "total": 9800,
                "units": 1,
                "status": "paid",
                "sourceUtm": "",
                "sourceMedium": "",
                "sourceCampaign": "",
                "clickIds": {},
                "attributionMethod": "shopline_attribution",
                "attributionConfidence": "high",
            },
            {
                "id": "google-ads-click",
                "createdAt": "2026-08-13",
                "source": "Google",
                "total": 7600,
                "units": 1,
                "sourceUtm": "",
                "sourceMedium": "",
                "sourceCampaign": "",
                "clickIds": {"gclid": "paid-click"},
            },
            {
                "id": "google-utm",
                "createdAt": "2026-08-13",
                "source": "Google",
                "total": 6400,
                "units": 1,
                "sourceUtm": "google",
                "sourceMedium": "organic",
                "sourceCampaign": "seo",
                "clickIds": {},
            },
        ]
        ga4_rows = [
            {
                "channel": "Yahoo",
                "source": "yahoo",
                "medium": "organic",
                "group": "Organic Search",
                "sessions": 10,
                "activeUsers": 8,
                "keyEvents": 1,
            },
            {
                "channel": "Google",
                "source": "google",
                "medium": "organic",
                "group": "Organic Search",
                "sessions": 42,
                "activeUsers": 35,
                "keyEvents": 3,
            },
            {
                "channel": "Google",
                "source": "google",
                "medium": "cpc",
                "group": "Paid Search",
                "sessions": 200,
                "activeUsers": 160,
                "keyEvents": 8,
            },
        ]

        focus_rows = build_focus_channels(orders, build_channels(orders, ga4_rows), ga4_rows)
        yahoo = next(row for row in focus_rows if row["channel"] == "Yahoo")

        self.assertFalse(any(row["channel"] == "Google Organic" for row in focus_rows))
        self.assertEqual(yahoo["orders"], 2)
        self.assertEqual(yahoo["officialOrders"], 2)
        self.assertEqual(yahoo["revenue"], 12800)
        self.assertEqual(yahoo["sessions"], 52)
        self.assertEqual(
            {row["id"] for row in yahoo["orderDetails"]},
            {"yahoo-order", "google-organic"},
        )
        self.assertEqual(yahoo["focusSummary"], "Yahoo 1 单 · Google自然 1 单")

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

    def test_load_products_follows_shopline_next_page_link(self):
        class PagingProductClient(ShoplineClient):
            def __init__(self):
                super().__init__(
                    ShoplineConfig(
                        base_url="https://store.example/admin/openapi/v20260301",
                        access_token="token-value",
                        products_path="/products/products.json",
                        max_product_pages=3,
                    )
                )
                self.calls = []

            def request_json_with_headers(self, path, params=None):
                self.calls.append((path, dict(params or {})))
                if params and params.get("page_info") == "page-2":
                    return {
                        "products": [{"product_id": "p-2", "title": "Coat"}]
                    }, {}
                return {
                    "products": [{"product_id": "p-1", "title": "Dress"}]
                }, {
                    "Link": '<https://store.example/products/products.json?limit=50&page_info=page-2>; rel="next"'
                }

        result = PagingProductClient().load_products(today=date(2026, 6, 17))

        self.assertEqual(result["source"], "live")
        self.assertEqual(result["rawCount"], 2)
        self.assertEqual(result["pages"], 2)
        self.assertFalse(result["pageLimitReached"])
        self.assertEqual(len(result["items"]), 2)

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

    def test_sync_quality_penalizes_ga4_divergence_and_duplicate_events(self):
        quality = build_sync_quality(
            {"pages": 1, "chunks": 1, "rawCount": 10},
            [
                {"source": "Facebook", "customerKey": f"c-{index}"}
                for index in range(10)
            ],
            {"rawCount": 80, "pages": 2, "pageLimitReached": False},
            [{"channel": "Facebook"}],
            [],
            reconciliation={"differenceRate": 35},
            ga4_status={
                "rawPurchaseEvents": 14,
                "duplicatePurchaseEvents": 4,
                "duplicateTransactionIds": 2,
            },
        )

        self.assertLess(quality["score"], 90)
        self.assertNotEqual(quality["grade"], "A")
        self.assertEqual(quality["productPages"], 2)
        self.assertEqual(quality["ga4DuplicatePurchaseEvents"], 4)

    def test_profit_summary_marks_missing_exact_costs_as_low_confidence(self):
        profit = build_profit_summary(
            [
                {
                    "total": 100,
                    "refundTotal": 0,
                    "discounts": 0,
                    "taxTotal": 0,
                    "market": "JP",
                    "items": [
                        {"sku": "SKU-1", "quantity": 1, "revenue": 100}
                    ],
                }
            ],
            [],
            CostConfig(
                product_cost_rate=0.35,
                payment_fee_rate=0.036,
                shipping_cost_per_order=0,
            ),
            {},
        )

        self.assertEqual(profit["confidence"], "low")
        self.assertEqual(profit["costCoverage"], 0)
        self.assertFalse(profit["shippingConfigured"])
        self.assertGreaterEqual(len(profit["warnings"]), 2)

    def test_frontend_sync_contract_keeps_filters_usable_and_sets_timeout(self):
        app_js = (
            Path(__file__).resolve().parents[1] / "static" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn('const REQUEST_TIMEOUT_MS = 60000;', app_js)
        self.assertIn('["sync-btn", "test-connector-btn"].forEach', app_js)
        self.assertNotIn('document.querySelectorAll("button, input, select")', app_js)
        self.assertIn('if (!state.autoRefreshMs || document.hidden', app_js)

    def test_frontend_exposes_focus_channels_and_paginated_channel_orders(self):
        package_root = Path(__file__).resolve().parents[1]
        app_js = (package_root / "static" / "app.js").read_text(encoding="utf-8")
        index_html = (package_root / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('const CHANNEL_DIALOG_PAGE_SIZE = 10;', app_js)
        self.assertIn('function renderFocusChannels(', app_js)
        self.assertIn('function changeChannelDialogPage(', app_js)
        self.assertIn('data-channel-drill="LINE"', index_html)
        self.assertIn('data-channel-drill="Yahoo"', index_html)
        self.assertIn('data-channel-drill="Organic"', index_html)
        self.assertNotIn('data-channel-drill="Google Organic"', index_html)
        self.assertIn('data-channel-drill="Direct"', index_html)

    def test_smartpush_summary_is_an_accessible_order_dialog_button(self):
        from html.parser import HTMLParser

        class InsightParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.insights = []

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if "smartpush-insight" in values.get("class", "").split():
                    self.insights.append((tag, values))

        parser = InsightParser()
        parser.feed((Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.insights), 1)
        tag, attrs = parser.insights[0]
        self.assertEqual(tag, "button")
        self.assertEqual(attrs["type"], "button")
        self.assertEqual(attrs["data-channel-drill"], "SmartPush")
        self.assertEqual(attrs["aria-haspopup"], "dialog")
        self.assertEqual(attrs["aria-controls"], "channel-order-dialog")

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

    def test_window_conversion_never_substitutes_transactions_over_sessions(self):
        rows = [{"sessions": 100, "transactions": 50, "conversion": 1.29}]
        self.assertEqual(summarize_series_window("7d", rows)["conversion"], 1.29)
        self.assertEqual(summarize_series_window("7d", rows, conversion_override=1.59)["conversion"], 1.59)

    def test_window_missing_reported_conversion_stays_empty(self):
        rows = [{"sessions": 100, "transactions": 50, "conversion": 1.29}]
        self.assertIsNone(summarize_series_window("7d", rows, conversion_override=None)["conversion"])

    def test_ga4_window_rates_fetch_complete_reports_without_date_dimensions(self):
        from shopline_monitor.backend import fetch_ga4_window_rates
        config = Ga4Config(property_id="123", service_account_json="{}")
        reports = SimpleNamespace(reports=[SimpleNamespace(rows=[SimpleNamespace(
            metric_values=[SimpleNamespace(value="0.0159")]
        )])])
        with patch("google.oauth2.service_account.Credentials.from_service_account_info"), patch(
            "google.analytics.data_v1beta.BetaAnalyticsDataClient"
        ) as client_type:
            client_type.return_value.batch_run_reports.return_value = reports
            rates = fetch_ga4_window_rates(config, [(date(2026, 9, 1), date(2026, 9, 7))])
            request = client_type.return_value.batch_run_reports.call_args.kwargs["request"]
        self.assertEqual(rates, [1.59])
        self.assertFalse(request.requests[0].dimensions)
        self.assertEqual(request.requests[0].metrics[0].name, "userKeyEventRate:purchase")
        self.assertEqual(request.requests[0].date_ranges[0].start_date, "2026-09-01")


if __name__ == "__main__":
    unittest.main()
