/**
 * charts.js — Ink-Style Ledger Visualizations for Paper Invoice Theme
 */

let trendChartInstance = null;
let donutChartInstance = null;
let vendorChartInstance = null;

function cleanVendorName(raw) {
  if (!raw) return "Unknown Vendor";
  if (typeof raw === "object") {
    return raw.name || raw.vendor_name || raw.company_name || raw.seller || raw.title || "Unknown Vendor";
  }
  let str = String(raw).trim();
  if (!str || str.toLowerCase() === "unknown" || str.toLowerCase() === "unknown vendor" || str.toLowerCase() === "n/a") {
    return "Unknown Vendor";
  }
  if ((str.startsWith("{") && str.endsWith("}")) || (str.startsWith("(") && str.endsWith(")"))) {
    try {
      const normalized = str.replace(/'/g, '"');
      const parsed = JSON.parse(normalized);
      if (typeof parsed === "object" && parsed !== null) {
        return parsed.name || parsed.vendor_name || parsed.company_name || parsed.seller || "Unknown Vendor";
      }
    } catch (e) {
      const match = str.match(/['"](?:name|vendor_name|company|seller)['"]\s*:\s*['"]([^'"]+)['"]/i);
      if (match && match[1]) return match[1].trim();
    }
  }
  return str;
}

function parseInvoiceDate(inv) {
  const raw = inv.invoice_date || inv.date || inv.issue_date || inv.created_at || "";
  if (!raw || raw === "N/A" || raw === "Unknown") return null;
  const str = String(raw).trim();

  const isoMatch = str.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (isoMatch) {
    const y = isoMatch[1];
    const m = isoMatch[2].padStart(2, "0");
    const d = isoMatch[3].padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  const dmyMatch = str.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/);
  if (dmyMatch) {
    const d = dmyMatch[1].padStart(2, "0");
    const m = dmyMatch[2].padStart(2, "0");
    const y = dmyMatch[3];
    return `${y}-${m}-${d}`;
  }

  const parsed = new Date(str);
  if (!isNaN(parsed.getTime())) {
    const y = parsed.getFullYear();
    const m = String(parsed.getMonth() + 1).padStart(2, "0");
    const d = String(parsed.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  return null;
}

export const charts = {
  /**
   * Set global Chart.js defaults to fit Paper Invoice theme
   */
  initDefaults() {
    if (typeof Chart === "undefined") return;

    Chart.defaults.color = "#6E6455"; // --ink-muted
    Chart.defaults.font.family = "'IBM Plex Mono', monospace";
    Chart.defaults.font.size = 10;
    Chart.defaults.plugins.tooltip.backgroundColor = "#FDFBF5";
    Chart.defaults.plugins.tooltip.titleColor = "#1C1710";
    Chart.defaults.plugins.tooltip.bodyColor = "#1C1710";
    Chart.defaults.plugins.tooltip.borderColor = "#1C1710";
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 8;
    Chart.defaults.plugins.tooltip.cornerRadius = 0;
  },

  /**
   * Render or update charts and ruled tables
   */
  renderAll(invoices = []) {
    if (typeof Chart === "undefined") return;
    this.initDefaults();

    this.renderSpendTrend(invoices);
    this.renderStatusDonut(invoices);
    this.renderTopVendors(invoices);
  },

  /**
   * 1. Spend Trajectory — Connected ink line, chronological date grouping, Rupee amounts
   */
  renderSpendTrend(invoices) {
    const canvas = document.getElementById("chart-spend-trend");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const dateMap = {};
    invoices.forEach((inv) => {
      const parsedDate = parseInvoiceDate(inv);
      const key = parsedDate || new Date().toISOString().slice(0, 10);
      const amt = parseFloat(inv.total_amount) || 0;
      dateMap[key] = (dateMap[key] || 0) + amt;
    });

    const sortedKeys = Object.keys(dateMap).sort();
    let labels = [];
    let values = [];

    if (sortedKeys.length === 0) {
      labels = ["Baseline", "Live"];
      values = [0, 0];
    } else if (sortedKeys.length === 1) {
      // Provide an opening anchor point so a multi-point trajectory line renders cleanly
      const singleDate = sortedKeys[0];
      const prevDateObj = new Date(singleDate);
      prevDateObj.setDate(prevDateObj.getDate() - 7);
      const prevDateKey = prevDateObj.toISOString().slice(0, 10);
      labels = [prevDateKey, singleDate];
      values = [0, dateMap[singleDate]];
    } else {
      labels = sortedKeys;
      values = sortedKeys.map((k) => dateMap[k]);
    }

    if (trendChartInstance) {
      trendChartInstance.destroy();
    }

    trendChartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Disbursement (₹)",
            data: values,
            borderColor: "#1C1710", // Pure Ink line
            borderWidth: 2,
            backgroundColor: "transparent",
            fill: false, // Strictly no area fill
            tension: 0, // Sharp crisp ledger line
            pointBackgroundColor: "#1C1710",
            pointBorderColor: "#FDFBF5",
            pointBorderWidth: 1.5,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            grid: {
              color: "#E2D9C4",
              borderDash: [2, 2],
            },
            ticks: {
              color: "#6E6455",
              font: { family: "'IBM Plex Mono', monospace", size: 10 },
            },
          },
          y: {
            grid: {
              color: "#E2D9C4",
              borderDash: [2, 2],
            },
            ticks: {
              color: "#6E6455",
              font: { family: "'IBM Plex Mono', monospace", size: 10 },
              callback: (val) => `₹${Number(val).toLocaleString("en-IN")}`,
            },
          },
        },
      },
    });
  },

  /**
   * 2. Settlement Status — Ruled rows with stamp counts & subtle ink donut
   */
  renderStatusDonut(invoices) {
    let paidCount = 0;
    let unpaidCount = 0;
    let overdueCount = 0;

    invoices.forEach((inv) => {
      const status = (inv.payment_status || "").toLowerCase();
      if (status.includes("paid") && !status.includes("unpaid")) {
        paidCount++;
      } else if (status.includes("overdue")) {
        overdueCount++;
      } else {
        unpaidCount++;
      }
    });

    const total = paidCount + unpaidCount + overdueCount || 1;
    const paidPct = Math.round((paidCount / total) * 100);
    const unpaidPct = Math.round((unpaidCount / total) * 100);
    const overduePct = Math.round((overdueCount / total) * 100);

    // Update ruled row counts & percentages
    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    setText("stat-paid-count", paidCount.toString());
    setText("stat-unpaid-ledger-count", unpaidCount.toString());
    setText("stat-overdue-count", overdueCount.toString());

    setText("legend-pct-paid", `${paidPct}%`);
    setText("legend-pct-unpaid", `${unpaidPct}%`);
    setText("legend-pct-overdue", `${overduePct}%`);

    const canvas = document.getElementById("chart-status-donut");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    if (donutChartInstance) {
      donutChartInstance.destroy();
    }

    donutChartInstance = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Paid", "Pending", "Overdue"],
        datasets: [
          {
            data: [paidCount, unpaidCount, overdueCount],
            backgroundColor: ["#2E7D4F", "#A86A12", "#B5342A"],
            borderColor: "#FDFBF5",
            borderWidth: 1.5,
            hoverOffset: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "80%",
        plugins: {
          legend: { display: false },
        },
      },
    });
  },

  /**
   * 3. Top Vendors — Rendered as a ruled ledger table
   */
  renderTopVendors(invoices) {
    const vendorMap = {};
    invoices.forEach((inv) => {
      const vendor = cleanVendorName(inv.vendor_name || inv.seller);
      if (vendor && vendor !== "Unknown Vendor" && vendor !== "Unknown" && vendor !== "N/A") {
        const amt = parseFloat(inv.total_amount) || 0;
        vendorMap[vendor] = (vendorMap[vendor] || 0) + amt;
      }
    });

    const sortedVendors = Object.entries(vendorMap)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);

    // 1. Populate Ruled Table
    const tableBody = document.getElementById("top-vendors-table-body");
    if (tableBody) {
      if (sortedVendors.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding:1.5rem; color:var(--ink-muted);">No vendor records filed yet.</td></tr>`;
      } else {
        tableBody.innerHTML = sortedVendors
          .map(([vendor, amt], idx) => `
            <tr>
              <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--ink-muted);">${idx + 1}.</td>
              <td><strong>${escapeHtml(vendor)}</strong></td>
              <td class="mono-amount" style="text-align:right;">₹${amt.toLocaleString("en-IN", { minimumFractionDigits: 2 })}</td>
            </tr>
          `)
          .join("");
      }
    }

    // 2. Also keep canvas updated if element present
    const canvas = document.getElementById("chart-vendor-bars");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const labels = sortedVendors.map((item) => (item[0].length > 18 ? item[0].slice(0, 15) + "..." : item[0]));
    const data = sortedVendors.map((item) => item[1]);

    if (vendorChartInstance) {
      vendorChartInstance.destroy();
    }

    vendorChartInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels.length ? labels : ["No data"],
        datasets: [
          {
            label: "Spend (₹)",
            data: data.length ? data : [0],
            backgroundColor: "#1C1710",
            borderColor: "#1C1710",
            borderWidth: 1,
            borderRadius: 0,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            grid: { color: "#E2D9C4", borderDash: [2, 2] },
            ticks: {
              color: "#6E6455",
              font: { family: "'IBM Plex Mono', monospace", size: 9 },
              callback: (val) => `₹${Number(val).toLocaleString("en-IN")}`,
            },
          },
          y: {
            grid: { display: false },
            ticks: {
              color: "#1C1710",
              font: { family: "'IBM Plex Mono', monospace", size: 9 },
            },
          },
        },
      },
    });
  },
};

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
