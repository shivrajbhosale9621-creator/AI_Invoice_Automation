import io
import os
import json
import re

from config import DEFAULT_INDEX_PATH, GROQ_MODEL_NAME, GROQ_API_KEY
from vector_store import (
	load_index,
	get_all_invoices,
	get_all_invoices_matrix,
	get_all_invoices_df,
	semantic_search_text,
)

import pandas as pd
import networkx as nx
import plotly.graph_objects as go
import plotly.express as px
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool


# Backwards compatibility alias
get_all_invoices_matric = get_all_invoices_matrix


@tool
def build_knowledge_graph():
	"""Constructs the interactive Plotly knowledge graph showing relationships (Vendors -> Invoices -> Buyers)."""
	data = get_all_invoices_matric(load_index())
	if not data or data == "No invoices found in storage.":
		print("No invoices found in storage.")
		return None

	# Parse Markdown table into DataFrame
	lines = [line.strip() for line in data.strip().splitlines() if not re.match(r'^\s*\|?\s*:?-+', line.strip())]
	df = pd.read_csv(io.StringIO('\n'.join(lines)), sep='|', engine='python').dropna(how='all')
	df.columns = [c.strip() for c in df.columns]
	df = df.loc[:, ~df.columns.str.contains('^Unnamed')]

	G = nx.DiGraph()

	for _, row in df.iterrows():
		inv_num = str(row.get("invoice_number", "N/A")).strip()
		vendor = str(row.get("vendor_name", "Unknown")).strip()
		buyer = str(row.get("buyer_name", "N/A")).strip()
		date = str(row.get("invoice_date", "N/A")).strip()
		amount = str(row.get("total_amount", "0")).strip()
		currency = str(row.get("currency", "USD")).strip()
		status = str(row.get("payment_status", "Unpaid")).strip()

		inv_id = f"invoice_{inv_num}"
		G.add_node(
			inv_id,
			label="Invoice",
			name=inv_num,
			hover=f"Invoice: {inv_num}<br>Date: {date}<br>Amount: {amount} {currency}<br>Status: {status}"
		)

		if vendor and vendor != "Unknown":
			v_id = f"vendor_{vendor}"
			if not G.has_node(v_id):
				G.add_node(v_id, label="Vendor", name=vendor, hover=f"Vendor: {vendor}")
			G.add_edge(v_id, inv_id, relationship="issued")

		if buyer and buyer != "N/A":
			b_id = f"buyer_{buyer}"
			if not G.has_node(b_id):
				G.add_node(b_id, label="Buyer", name=buyer, hover=f"Buyer: {buyer}")
			G.add_edge(inv_id, b_id, relationship="bought_by")

	pos = nx.spring_layout(G, seed=42)

	# Build Edge traces
	edge_x = []
	edge_y = []
	for u, v in G.edges():
		x0, y0 = pos[u]
		x1, y1 = pos[v]
		edge_x.extend([x0, x1, None])
		edge_y.extend([y0, y1, None])

	edge_trace = go.Scatter(
		x=edge_x,
		y=edge_y,
		line=dict(width=1.2, color="#888888"),
		hoverinfo="none",
		mode="lines",
		showlegend=False
	)

	traces = [edge_trace]
	colors = {"Invoice": "#1f77b4", "Vendor": "#ff7f0e", "Buyer": "#2ca02c"}
	symbols = {"Invoice": "circle", "Vendor": "square", "Buyer": "diamond"}

	for label in ["Invoice", "Vendor", "Buyer"]:
		sub_nodes = [n for n, d in G.nodes(data=True) if d.get("label") == label]
		if not sub_nodes:
			continue
		xs = [pos[n][0] for n in sub_nodes]
		ys = [pos[n][1] for n in sub_nodes]
		names = [G.nodes[n].get("name", "") for n in sub_nodes]
		hovers = [G.nodes[n].get("hover", "") for n in sub_nodes]

		traces.append(go.Scatter(
			x=xs,
			y=ys,
			mode="markers+text",
			name=f"{label}s",
			text=names,
			textposition="top center",
			hoverinfo="text",
			hovertext=hovers,
			marker=dict(
				size=20,
				color=colors.get(label, "#333333"),
				symbol=symbols.get(label, "circle"),
				line=dict(width=1, color="white")
			)
		))

	fig = go.Figure(
		data=traces,
		layout=go.Layout(
			title="Enterprise Invoice Knowledge Graph",
			showlegend=True,
			hovermode="closest",
			margin=dict(b=20, l=20, r=20, t=40),
			xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
			yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)
		)
	)

	return fig
@tool
def generate_dashboard():
	"""Analyzes invoice data and dynamically generates 3 to 4 executive dashboard charts."""
	data = get_all_invoices_matric(load_index())
	if not data or data == "No invoices found in storage.":
		print("No invoices found in storage.")
		return []

	lines = [line.strip() for line in data.strip().splitlines() if not re.match(r'^\s*\|?\s*:?-+', line.strip())]
	df = pd.read_csv(io.StringIO('\n'.join(lines)), sep='|', engine='python').dropna(how='all')
	df.columns = [c.strip() for c in df.columns]
	df = df.loc[:, ~df.columns.str.contains('^Unnamed')]

	# Ensure amount columns are numeric if present
	for col in df.columns:
		if any(k in col.lower() for k in ["amount", "total", "price", "subtotal", "tax"]):
			df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

	llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0.2, max_tokens=4096)

	prompt = ChatPromptTemplate.from_template(
		"""
		You are an expert Data Scientist and Python Plotly developer.

		Inspect the provided invoice dataset below and automatically design 3 to 4 of the most meaningful, insightful analytical charts for an executive dashboard.

		### Available Invoice Data (First rows & schema):
		{data}

		### Available Columns in `df`:
		{columns}

		### Instructions:
		1. Inspect the columns that actually exist in `df`.
		2. Decide the 3-4 most insightful charts (e.g. vendor spend breakdown, timeline trends, buyer analysis, payment status distribution, etc.).
		3. Use Plotly Express (`px`) or Graph Objects (`go`) to build the charts.
		4. Give each chart a descriptive title and clean styling (`template="plotly_white"`).
		5. PANDAS 2.0 COMPATIBILITY RULE:
		   - In modern Pandas, `df[col].value_counts().reset_index()` creates columns `[col, "count"]`. NEVER use `names='index'`. Use `names=col` and `values='count'`.
		   - DO NOT call `.rename(...)` after `.value_counts().reset_index()`.
		   - For date conversions, use `pd.to_datetime(df[col], errors="coerce")` and drop NaT values before plotting timeline charts.
		6. Append all created Plotly figures into a single list named `figures`:
		   ```python
		   figures = [fig1, fig2, fig3]
		   ```
		7. Do not call `.show()`.
		8. Output only executable Python code with no markdown fences, explanations, or JSON.
		"""
	)

	chain = prompt | llm
	response = chain.invoke({
		"data": df.head(10).to_markdown(index=False),
		"columns": list(df.columns)
	})

	code = response.content.strip()
	if code.startswith("```"):
		match = re.search(r"```(?:python)?\s*(.*?)\s*```", code, re.DOTALL)
		if match:
			code = match.group(1).strip()
		else:
			code = re.sub(r"^```(?:python)?\n?", "", code)
			code = re.sub(r"\n?```$", "", code)

	scope = {"df": df.copy(), "pd": pd, "px": px, "go": go, "figures": []}
	figures = []
	try:
		exec(code, scope)
		figures = scope.get("figures", [])
	except Exception as e:
		print(f"Error executing dynamic dashboard code: {e}")

	if not figures:
		try:
			# Fallback: reliably generate 3 executive dashboard charts directly
			if "vendor_name" in df.columns and any("amount" in c.lower() for c in df.columns):
				amt_col = next(c for c in df.columns if "amount" in c.lower())
				vendor_df = df.groupby("vendor_name")[amt_col].sum().reset_index().sort_values(amt_col, ascending=False).head(5)
				fig1 = px.bar(vendor_df, x="vendor_name", y=amt_col, title="Top 5 Vendors by Total Spend", template="plotly_white")
				figures.append(fig1)

			if "payment_status" in df.columns:
				status_df = df["payment_status"].value_counts().reset_index()
				c_name = status_df.columns[0]
				cnt_name = status_df.columns[1]
				fig2 = px.pie(status_df, names=c_name, values=cnt_name, hole=0.4, title="Invoice Payment Status Distribution", template="plotly_white")
				figures.append(fig2)

			date_col = next((c for c in df.columns if "date" in c.lower()), None)
			amt_col = next((c for c in df.columns if "amount" in c.lower()), None)
			if date_col and amt_col:
				date_df = df.dropna(subset=[date_col]).sort_values(date_col)
				fig3 = px.bar(date_df, x=date_col, y=amt_col, title="Invoice Amounts Timeline", template="plotly_white")
				figures.append(fig3)
		except Exception as fb_err:
			print(f"Fallback dashboard generation error: {fb_err}")

	return figures


# -------------------------------------------------------------
# 3. Dynamic Custom Graph Generator from User Prompts
# -------------------------------------------------------------
def node_vector_search(search_prompt: str, top_k: int = 5, index=None) -> str:
	"""
	Executes Vector Similarity Search using dynamically calculated top_k.
	"""
	return semantic_search_text(query=search_prompt, top_k=top_k, index=index)


def route_graph_intent(query: str, matrix: str) -> dict:
	"""
	Evaluates whether user prompt can be answered from Matrix alone or needs Vector Search,
	and dynamically calculates the exact optimal top_k integer based on how many matching invoices
	appear in the matrix table so no invoice is missed.
	"""
	q_lower = query.lower()
	if any(k in q_lower for k in ["item", "product", "description", "line item", "goods", "sku", "material"]):
		return {"can_build_from_matrix": False, "top_k": 12, "search_query": query}
	if any(k in q_lower for k in ["vendor", "supplier", "buyer", "date", "month", "status", "paid", "unpaid", "overdue", "currency", "amount", "total", "invoice"]):
		return {"can_build_from_matrix": True, "top_k": 5, "search_query": query}

	llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0)
	prompt = ChatPromptTemplate.from_template(
		"""
		You are a smart Invoice Graph Router.
		Analyze the User Question/Prompt and the Global Summary Matrix Table below.

		Global Summary Matrix:
		{matrix}

		User Request: "{query}"

		Task:
		1. Can the requested visualization be generated using the Global Summary Matrix Table alone?
		   (e.g., spend by vendor, invoices over time, payment status, general comparisons of vendors/buyers/dates/amounts).
		2. Or does it require deep line-item or itemized document inspection from individual original documents?
		   (e.g., comparing specific item prices like "printers" or "chairs", warranty terms, serial numbers, tax breakdowns).
		3. If vector search IS needed:
		   - Inspect the Global Summary Matrix to count how many invoice files exist for this specific vendor/buyer/topic.
		   - Calculate an optimal `top_k` integer that covers ALL matching invoices (minimum 5, or exact count matching the topic) so no invoice is left out.
		   - Provide a specific `search_query` for semantic retrieval.

		Return ONLY a JSON response:
		{{
			"can_build_from_matrix": true/false,
			"top_k": integer,
			"search_query": "string"
		}}
		"""
	)
	chain = prompt | llm
	try:
		response = chain.invoke({"matrix": matrix, "query": query})
		content = getattr(response, "content", str(response)).strip()

		if content.startswith("```"):
			match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
			if match:
				content = match.group(1).strip()

		return json.loads(content)
	except Exception:
		return {"can_build_from_matrix": True, "top_k": 5, "search_query": query}


@tool
def generate_custom_graph(query: str):
	"""Generates a custom Plotly visualization chart based on the user's specific natural language prompt."""
	index = load_index()

	matrix = get_all_invoices_matric(index)
	if not matrix or matrix == "No invoices found in storage.":
		print("No invoices found in storage.")
		return None

	# Step 1: Check intent and dynamically determine top_k
	route = route_graph_intent(query, matrix)
	can_build_from_matrix = route.get("can_build_from_matrix", True)
	top_k = int(route.get("top_k", 5))
	search_query = route.get("search_query") or query

	# Parse summary matrix into DataFrame
	lines = [line.strip() for line in matrix.strip().splitlines() if not re.match(r'^\s*\|?\s*:?-+', line.strip())]
	df = pd.read_csv(io.StringIO('\n'.join(lines)), sep='|', engine='python').dropna(how='all')
	df.columns = [c.strip() for c in df.columns]
	df = df.loc[:, ~df.columns.str.contains('^Unnamed')]

	for col in df.columns:
		if any(k in col.lower() for k in ["amount", "total", "price"]):
			df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

	detailed_context = ""
	if not can_build_from_matrix:
		# Step 2: Semantic vector search using dynamically calculated top_k
		detailed_context = node_vector_search(search_query, top_k=top_k, index=index)

	# Step 3: LLM generates custom Plotly chart code
	llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0.2, max_tokens=4096)
	code_prompt = ChatPromptTemplate.from_template(
		"""
		You are an expert Python Data Visualization developer specializing in Plotly.

		Generate Python code to create an interactive Plotly visualization for the following user request:
		User Request: "{query}"

		### Available Data:
		Global Invoice Matrix DataFrame `df`:
		{df_summary}

		{extra_context}

		### Instructions:
		1. Generate a single Plotly figure object named `fig` that directly answers the user's request.
		2. If `df` is used, work on `df.copy()`. If detailed context/JSON was retrieved, extract the required fields into a DataFrame.
		3. Choose the optimal chart type (e.g. Bar, Line, Scatter, Pie, Treemap, Box, Histogram).
		4. Give the chart a descriptive title and clean layout (`template="plotly_white"`).
		5. PANDAS 2.0 RULE: Avoid duplicate column names. When computing counts, `df[col].value_counts().reset_index()` already has columns `[col, "count"]`. Do NOT call `.rename(..., col: "count")`.
		6. Safe Categorical Indexing: Never assume specific categories (such as 'Overdue', 'Unpaid', or a specific vendor) exist as keys or index entries. Always handle missing categories gracefully with `.get()` or filter masks like `df[df[col].str.contains(..., na=False)]`.
		7. Store the final Plotly figure in the variable named:
		   ```python
		   fig
		   ```
		8. Do not call `fig.show()`.
		9. Output ONLY executable Python code. Do not include markdown fences, explanations, or JSON.
		"""
	)

	extra_text = f"Detailed Document Text (Retrieved via Semantic Search with top_k={top_k}):\n{detailed_context}" if detailed_context else ""

	try:
		chain = code_prompt | llm
		response = chain.invoke({
			"query": query,
			"df_summary": df.head(10).to_markdown(index=False),
			"extra_context": extra_text
		})

		code = response.content.strip()
		if code.startswith("```"):
			match = re.search(r"```(?:python)?\s*(.*?)\s*```", code, re.DOTALL)
			if match:
				code = match.group(1).strip()
			else:
				code = re.sub(r"^```(?:python)?\n?", "", code)
				code = re.sub(r"\n?```$", "", code)

		scope = {"df": df.copy(), "pd": pd, "px": px, "go": go, "json": json, "io": io, "fig": None}
		exec(code, scope)
		fig = scope.get("fig")
		if fig is not None and _figure_has_data(fig):
			return fig
		if fig is not None:
			print("LLM chart had no data traces; using fallback chart builder.")
	except Exception as e:
		print(f"Error executing custom graph code / LLM: {e}")


	# Resilient heuristic fallback if custom code fails or returns None
	try:
		q_low = query.lower()
		if "status" in q_low or "paid" in q_low or "overdue" in q_low or "settlement" in q_low:
			s_col = next((c for c in df.columns if "status" in c.lower()), None)
			if s_col:
				s_data = df[s_col].value_counts().reset_index()
				return px.pie(s_data, names=s_data.columns[0], values=s_data.columns[1], title="Payment Status Breakdown", template="plotly_white")
		if "vendor" in q_low or "supplier" in q_low or "seller" in q_low:
			v_col = next((c for c in df.columns if "vendor" in c.lower() or "seller" in c.lower()), None)
			a_col = next((c for c in df.columns if "amount" in c.lower() or "total" in c.lower()), None)
			if v_col and a_col:
				v_data = df.groupby(v_col)[a_col].sum().reset_index().sort_values(by=a_col, ascending=False).head(5)
				return px.bar(v_data, x=v_col, y=a_col, title="Top Vendors by Spend", template="plotly_white")
		a_col = next((c for c in df.columns if "amount" in c.lower() or "total" in c.lower()), None)
		if a_col:
			return px.histogram(df, x=a_col, title="Invoice Amount Distribution", template="plotly_white")
	except Exception as fb_err:
		print(f"Fallback generation error: {fb_err}")

	return None

PAPER_PALETTE = ["#1C1710", "#A86A12", "#2E7D4F", "#B5342A", "#6E6455", "#8C7B5E", "#4A6B8A", "#7A3E2E"]


def _figure_has_data(fig) -> bool:
	"""A chart with a title but no traces (or only empty traces) is worse
	than no chart: it renders as an empty box like the old Payment Status pie."""
	try:
		for trace in getattr(fig, "data", []) or []:
			x = getattr(trace, "x", None)
			y = getattr(trace, "y", None)
			labels = getattr(trace, "labels", None)
			values = getattr(trace, "values", None)
			if labels is not None and values is not None and len(labels) and len(values):
				return True
			if x is not None and y is not None and len(x) and len(y):
				return True
		return False
	except Exception:
		return True  # never drop a figure we cannot inspect


def _apply_paper_theme(fig) -> None:
	"""Restyle a Plotly figure to the paper-invoice theme (no default blues)."""
	fig.update_layout(
		paper_bgcolor="#FDFBF5",
		plot_bgcolor="#FDFBF5",
		font=dict(family="Inter, sans-serif", color="#1C1710"),
		margin=dict(l=40, r=40, t=50, b=40),
	)
	for idx, trace in enumerate(getattr(fig, "data", []) or []):
		if getattr(trace, "type", "") == "bar":
			trace.marker = dict(color=PAPER_PALETTE[idx % len(PAPER_PALETTE)])
		elif getattr(trace, "type", "") == "pie":
			trace.marker = dict(colors=PAPER_PALETTE)
		elif getattr(trace, "type", "") == "scatter" and getattr(trace, "mode", "") != "markers+text":
			trace.line = dict(color=PAPER_PALETTE[0])


class GraphAgent:
	"""
	Visual & Knowledge Graph Sub-Agent for Orchestrator.
	Returns standardized dictionary envelope with a list of Plotly figures.
	"""

	def __init__(self, llm=None, index_path=DEFAULT_INDEX_PATH):
		self.llm = llm or ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0)
		self.index_path = index_path
		self.user_id = None  # set by orchestrator; scopes all data reads
		self.tools = [
			build_knowledge_graph,
			generate_dashboard,
			generate_custom_graph
		]
		self.tools_map = {t.name: t for t in self.tools}
		self.agent = self.llm.bind_tools(self.tools)

	def run(self, request: str) -> dict:
		"""
		Executes Graph agent and returns standardized dictionary to Orchestrator:
		{
			"status": "success" | "failed",
			"chart_type": str,
			"figures": list[go.Figure],
			"message": str,
			"error": str | None
		}
		"""
		# Scope all invoice-data reads in this run to the owning user
		try:
			from vector_store import set_current_user_id as _set_uid
			_set_uid(self.user_id)
		except Exception:
			pass
		if not os.path.exists(self.index_path) or not os.listdir(self.index_path):
			return {
				"status": "failed",
				"chart_type": "none",
				"figures": [],
				"message": "No invoices found in storage. Please process invoices first.",
				"error": f"No index found at '{self.index_path}'"
			}

		try:
			req_lower = request.lower()
			tool_name = None
			tool_args = {}

			if "dashboard" in req_lower or "executive" in req_lower:
				tool_name = "generate_dashboard"
				tool_args = {}
			elif "knowledge graph" in req_lower or "relationship" in req_lower or "network" in req_lower:
				tool_name = "build_knowledge_graph"
				tool_args = {}
			elif any(w in req_lower for w in ["chart", "graph", "plot", "spend", "vendor", "item", "top", "trend", "histogram", "pie", "bar", "scatter", "box", "amount", "invoice"]):
				tool_name = "generate_custom_graph"
				tool_args = {"query": request}

			if tool_name and tool_name in self.tools_map:
				tool = self.tools_map[tool_name]
			else:
				response = self.agent.invoke(request)
				if not response.tool_calls:
					return {
						"status": "success",
						"chart_type": "text",
						"figures": [],
						"chart_paths": [],
						"message": response.content,
						"error": None
					}
				tool_call = response.tool_calls[0]
				tool_name = tool_call['name']
				tool_args = tool_call['args']
				tool = self.tools_map.get(tool_name)

			if not tool:
				return {
					"status": "failed",
					"chart_type": "none",
					"figures": [],
					"chart_paths": [],
					"message": f"Tool '{tool_name}' not found.",
					"error": f"Tool '{tool_name}' not in tools_map"
				}

			result = tool.invoke(tool_args) if hasattr(tool, "invoke") else tool(**tool_args)

			if result is None:
				return {
					"status": "failed",
					"chart_type": tool_name,
					"figures": [],
					"chart_paths": [],
					"message": "Chart generation failed or returned no data.",
					"error": "Tool execution returned None"
				}

			# Normalize figures: always return a list of figures
			raw_figures = result if isinstance(result, list) else [result]
			figures = [f for f in raw_figures if f is not None and _figure_has_data(f)]

			if not figures:
				return {
					"status": "failed",
					"chart_type": tool_name,
					"figures": [],
					"chart_paths": [],
					"message": "The chart came back empty, so nothing was pinned. Try rephrasing the request.",
					"error": "All generated figures had no data"
				}

			chart_paths = []
			static_charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "charts")
			os.makedirs(static_charts_dir, exist_ok=True)

			import uuid as _uuid
			import datetime as _dt
			for idx, fig in enumerate(figures):
				try:
					_apply_paper_theme(fig)
					chart_id = f"chart_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(_uuid.uuid4())[:8]}_{idx}.html"
					chart_file_path = os.path.join(static_charts_dir, chart_id)
					fig.write_html(chart_file_path, include_plotlyjs="cdn", full_html=True, config={"responsive": True})
					chart_paths.append(f"/static/charts/{chart_id}")
				except Exception as _ce:
					print(f"Error saving chart html: {_ce}")

			return {
				"status": "success",
				"chart_type": tool_name,
				"figures": figures,
				"chart_paths": chart_paths,
				"message": f"Successfully generated visualization using '{tool_name}'.",
				"error": None
			}

		except Exception as e:
			return {
				"status": "failed",
				"chart_type": "none",
				"figures": [],
				"chart_paths": [],
				"message": f"Error generating visualization: {str(e)}",
				"error": str(e)
			}





