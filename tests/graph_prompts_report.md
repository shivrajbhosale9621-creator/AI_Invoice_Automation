# 32 Graph Prompts Comprehensive Test Report

- **Total Prompts Tested**: 32
- **Passed**: 31
- **Failed**: 1

| # | Prompt | Status | Tool | Figures | Duration | Chart File |
|---|---|---|---|---|---|---|
| 1 | plot the top 5 vendors by spend | **PASSED** | `generate_custom_graph` | 1 | 5.13s | `/static/charts/chart_20261017_223655_fb3cdd9b_0.html` |
| 2 | bar chart of invoice count per vendor | **PASSED** | `generate_custom_graph` | 1 | 4.87s | `/static/charts/chart_20261017_223700_38ce98f7_0.html` |
| 3 | vendor market share donut chart | **PASSED** | `generate_custom_graph` | 1 | 1.41s | `/static/charts/chart_20261017_223701_d7826f73_0.html` |
| 4 | average amount per vendor bar chart | **PASSED** | `generate_custom_graph` | 1 | 1.25s | `/static/charts/chart_20261017_223703_36e41eff_0.html` |
| 5 | show top 5 vendors by total amount | **PASSED** | `generate_custom_graph` | 1 | 1.95s | `/static/charts/chart_20261017_223704_242e1712_0.html` |
| 6 | pie chart of paid vs unpaid vs overdue invoices | **PASSED** | `generate_custom_graph` | 1 | 9.41s | `/static/charts/chart_20261017_223714_dc9c0a0a_0.html` |
| 7 | bar chart of invoice count by payment status | **PASSED** | `generate_custom_graph` | 1 | 2.45s | `/static/charts/chart_20261017_223716_5515e0e1_0.html` |
| 8 | compare overdue vs paid amounts in a bar chart | **PASSED** | `generate_custom_graph` | 1 | 12.62s | `/static/charts/chart_20261017_223729_62f21bfa_0.html` |
| 9 | distribution of payment status across all invoices | **FAILED** | `generate_custom_graph` | 0 | 13.73s | None |
| 10 | line graph of invoice amounts over time by date | **PASSED** | `generate_custom_graph` | 1 | 1.5s | `/static/charts/chart_20261017_223744_e1d6ebac_0.html` |
| 11 | bar chart of invoice totals by date | **PASSED** | `generate_custom_graph` | 1 | 14.02s | `/static/charts/chart_20261017_223758_9b0fd932_0.html` |
| 12 | cumulative spend line chart | **PASSED** | `generate_custom_graph` | 1 | 18.48s | `/static/charts/chart_20261017_223817_8de1dbb0_0.html` |
| 13 | number of invoices per date | **PASSED** | `generate_custom_graph` | 1 | 12.9s | `/static/charts/chart_20261017_223830_f23b1fbc_0.html` |
| 14 | scatter plot of amounts against invoice dates | **PASSED** | `generate_custom_graph` | 1 | 9.34s | `/static/charts/chart_20261017_223839_64e80b32_0.html` |
| 15 | generate a graph of top five items on which we spend | **PASSED** | `generate_custom_graph` | 1 | 29.4s | `/static/charts/chart_20261017_223908_47f770c2_0.html` |
| 16 | top items by quantity purchased | **PASSED** | `generate_custom_graph` | 1 | 39.97s | `/static/charts/chart_20261017_223948_4a2aadef_0.html` |
| 17 | bar chart of the most expensive items purchased | **PASSED** | `generate_custom_graph` | 1 | 42.65s | `/static/charts/chart_20261017_224031_1b7cfe60_0.html` |
| 18 | scatter plot of item prices vs total amounts | **PASSED** | `generate_custom_graph` | 1 | 30.52s | `/static/charts/chart_20261017_224101_9fb0eefc_0.html` |
| 19 | top 5 products by total expenditure | **PASSED** | `generate_custom_graph` | 1 | 41.72s | `/static/charts/chart_20261017_224143_021e5283_0.html` |
| 20 | histogram of invoice total amounts distribution | **PASSED** | `generate_custom_graph` | 1 | 14.73s | `/static/charts/chart_20261017_224158_dd483089_0.html` |
| 21 | show invoices above 1000 in a horizontal bar chart | **PASSED** | `generate_custom_graph` | 1 | 7.35s | `/static/charts/chart_20261017_224205_c92d1c93_0.html` |
| 22 | bar chart of the top 3 biggest single invoices | **PASSED** | `generate_custom_graph` | 1 | 11.71s | `/static/charts/chart_20261017_224217_32287fd1_0.html` |
| 23 | bar chart of the 5 smallest invoices | **PASSED** | `generate_custom_graph` | 1 | 9.82s | `/static/charts/chart_20261017_224227_6ab3739a_0.html` |
| 24 | chart showing currency breakdown of invoices | **PASSED** | `generate_custom_graph` | 1 | 9.54s | `/static/charts/chart_20261017_224236_02b02859_0.html` |
| 25 | which buyers have the highest spend in a pie chart | **PASSED** | `generate_custom_graph` | 1 | 8.09s | `/static/charts/chart_20261017_224244_97fc2444_0.html` |
| 26 | total spend by buyer bar chart | **PASSED** | `generate_custom_graph` | 1 | 7.7s | `/static/charts/chart_20261017_224252_c13a85bd_0.html` |
| 27 | show knowledge graph of vendors and buyers | **PASSED** | `build_knowledge_graph` | 1 | 0.27s | `/static/charts/chart_20261017_224252_916b9d49_0.html` |
| 28 | generate executive dashboard charts | **PASSED** | `generate_dashboard` | 3 | 8.92s | `/static/charts/chart_20261017_224301_22d9b7b8_0.html` |
| 29 | build a network graph of invoice relationships | **PASSED** | `build_knowledge_graph` | 1 | 0.09s | `/static/charts/chart_20261017_224301_c38cfd42_0.html` |
| 30 | genrate a bar chrt of top suplpyers | **PASSED** | `generate_custom_graph` | 1 | 27.12s | `/static/charts/chart_20261017_224329_fa3247af_0.html` |
| 31 | generate the graph of 10 latest invoices | **PASSED** | `generate_custom_graph` | 1 | 10.46s | `/static/charts/chart_20261017_224339_697c867f_0.html` |
| 32 | plot invoice amounts grouped by status with white theme | **PASSED** | `generate_custom_graph` | 1 | 5.76s | `/static/charts/chart_20261017_224345_460379dd_0.html` |
