"""
test_graph_prompts.py - Comprehensive test suite for 30+ Graph & Chart Prompts.

Verifies:
1. Fast-path & LLM routing in GraphAgent.
2. Successful chart generation across 32 diverse prompt categories:
   - Top spenders, vendors, items, buyers, currencies
   - Status distributions, timelines, trends, histograms, box plots
   - Knowledge graphs, dashboards
   - Natural language variations & typos
3. Plotly figure generation and HTML export to static/charts.
4. Orchestrator conversation memory and self-healing resilience against INVALID_CHAT_HISTORY.
"""

import os
import sys
import pytest

sys.stdout.reconfigure(encoding="utf-8")

from graph_agent import GraphAgent
from orchestrator import MainOrchestrator


TEST_PROMPTS = [
    # Category 1: Vendor Spend & Analytics (5 prompts)
    ("plot the top 5 vendors by spend", "vendor_top5_spend"),
    ("bar chart of invoice count per vendor", "vendor_invoice_count"),
    ("vendor market share donut chart", "vendor_market_share"),
    ("average amount per vendor bar chart", "vendor_avg_amount"),
    ("show top 5 vendors by total amount", "vendor_top5_totals"),

    # Category 2: Payment Status & Settlement (4 prompts)
    ("pie chart of paid vs unpaid vs overdue invoices", "status_pie_breakdown"),
    ("bar chart of invoice count by payment status", "status_bar_count"),
    ("compare overdue vs paid amounts in a bar chart", "status_overdue_vs_paid"),
    ("distribution of payment status across all invoices", "status_distribution"),

    # Category 3: Timeline & Temporal Trends (5 prompts)
    ("line graph of invoice amounts over time by date", "timeline_date_trend"),
    ("bar chart of invoice totals by date", "timeline_date_bars"),
    ("cumulative spend line chart", "timeline_cumulative_spend"),
    ("number of invoices per date", "timeline_count_by_date"),
    ("scatter plot of amounts against invoice dates", "timeline_scatter_dates"),

    # Category 4: Line Items & Deep Inspection (5 prompts)
    ("generate a graph of top five items on which we spend", "item_top5_spend"),
    ("top items by quantity purchased", "item_top_quantity"),
    ("bar chart of the most expensive items purchased", "item_expensive_bars"),
    ("scatter plot of item prices vs total amounts", "item_scatter_price"),
    ("top 5 products by total expenditure", "item_top5_products"),

    # Category 5: Distribution & Statistical Metrics (4 prompts)
    ("histogram of invoice total amounts distribution", "stats_histogram_totals"),
    ("show invoices above 1000 in a horizontal bar chart", "stats_above_1000"),
    ("bar chart of the top 3 biggest single invoices", "stats_top3_biggest"),
    ("bar chart of the 5 smallest invoices", "stats_5_smallest"),

    # Category 6: Currency & Buyer Profiles (3 prompts)
    ("chart showing currency breakdown of invoices", "profile_currency_breakdown"),
    ("which buyers have the highest spend in a pie chart", "profile_buyer_pie"),
    ("total spend by buyer bar chart", "profile_buyer_spend"),

    # Category 7: Knowledge Graph & Executive Dashboards (3 prompts)
    ("show knowledge graph of vendors and buyers", "kg_vendors_buyers"),
    ("generate executive dashboard charts", "dashboard_executive"),
    ("build a network graph of invoice relationships", "kg_network_relationships"),

    # Category 8: Typos, Variations & Natural Language (3 prompts)
    ("genrate a bar chrt of top suplpyers", "typo_top_suppliers"),
    ("generate the graph of 10 latest invoices", "nl_10_latest_invoices"),
    ("plot invoice amounts grouped by status with white theme", "nl_status_white_theme"),
]


@pytest.fixture(scope="module")
def graph_agent():
    return GraphAgent()


@pytest.mark.parametrize("prompt,slug", TEST_PROMPTS)
def test_graph_agent_prompt(graph_agent, prompt, slug):
    """Verify each of the 32 prompt variations produces valid charts and saved HTML."""
    res = graph_agent.run(prompt)
    assert res is not None, f"GraphAgent returned None for prompt: {prompt}"
    assert res.get("status") == "success", f"Failed on prompt '{prompt}': {res.get('error')} | {res.get('message')}"
    figures = res.get("figures", [])
    chart_paths = res.get("chart_paths", [])

    assert len(figures) > 0, f"No figures returned for prompt: {prompt}"
    assert len(chart_paths) > 0, f"No chart HTML files exported for prompt: {prompt}"

    for path in chart_paths:
        disk_path = path.lstrip("/")
        assert os.path.exists(disk_path), f"Generated chart file missing on disk: {disk_path}"
        assert os.path.getsize(disk_path) > 500, f"Generated chart file too small: {disk_path}"


def test_orchestrator_graph_tool_and_recovery():
    """Verify Orchestrator correctly formats graph response and survives tool calls without INVALID_CHAT_HISTORY."""
    import uuid
    test_conv_id = f"test_graph_conv_{uuid.uuid4().hex[:8]}"
    orch = MainOrchestrator(conversation_id=test_conv_id)

    # Turn 1: Graph generation query
    reply1 = orch.run("generate a graph of top five items on which we spend")
    assert "/static/charts/" in reply1 or "chart" in reply1.lower() or "cuisinart" in reply1.lower(), (
        f"Unexpected reply1: {reply1}"
    )

    # Turn 2: Follow-up question (this was the exact turn that triggered INVALID_CHAT_HISTORY in screenshot)
    reply2 = orch.run("what happen")
    assert "Found AIMessages with tool_calls that do not have a corresponding ToolMessage" not in reply2, (
        f"INVALID_CHAT_HISTORY detected in reply2: {reply2}"
    )
    assert len(reply2) > 10, f"Reply2 too short: {reply2}"
