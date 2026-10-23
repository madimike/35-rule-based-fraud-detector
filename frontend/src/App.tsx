import { useEffect, useState } from "react";
import "./App.css";

const navigationItems = [
  { label: "Overview", icon: "▦" },
  { label: "Transactions", icon: "⇄" },
  { label: "Assessments", icon: "◈" },
  { label: "Cases", icon: "▤" },
];

interface Transaction {
  transaction_id: string;
  customer_id: string;
  amount: string;
  currency: string;
  timestamp: string;
  merchant_category: string;
  channel: "card" | "online" | "transfer";
  device_id?: string | null;
  country_code?: string | null;
  created_at?: string;
}

interface RuleResult {
  rule_id: string;
  name: string;
  status: string;
  score: number;
  explanation: string;
}

interface Assessment {
  assessment_id: number;
  transaction_id: string;
  risk_score: number;
  risk_band: string;
  created_at: string;
  rule_results: RuleResult[];
}

type CaseStatus = "open" | "in_review" | "closed";

interface FraudCase {
  case_id: number;
  transaction_id: string;
  assessment_id: number;
  status: CaseStatus;
  notes: string;
  created_at: string;
  updated_at: string;
}

interface DashboardData {
  transactions: Transaction[];
  assessments: Assessment[];
  cases: FraudCase[];
}

type ConnectionStatus = "checking" | "connected" | "disconnected";

const emptyDashboardData: DashboardData = {
  transactions: [],
  assessments: [],
  cases: [],
};

function formatDate(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function formatRiskBand(value: string): string {
  if (!value) return "Unknown";

  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

function App() {
  const [activePage, setActivePage] = useState("Overview");

  const [dashboardData, setDashboardData] =
    useState<DashboardData>(emptyDashboardData);
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("checking");
  const [isLoading, setIsLoading] = useState(true);
  const [apiError, setApiError] = useState<string | null>(null);
  const [caseUpdateError, setCaseUpdateError] = useState<string | null>(null);
  const [updatingCaseId, setUpdatingCaseId] = useState<number | null>(null);
  const [transactionSearch, setTransactionSearch] = useState("");
  const [riskBandFilter, setRiskBandFilter] = useState("all");
  const [channelFilter, setChannelFilter] = useState("all");

  async function loadDashboardData(showLoading = true) {
    // Keep the loading indicator visible long enough to be noticed, even when
    // the local API responds very quickly.
    const loadingStartedAt = Date.now();
    const minimumLoadingDurationMs = 500;

    if (showLoading) {
      setIsLoading(true);
      setApiError(null);
    }

    try {
      const [
        healthResponse,
        transactionsResponse,
        assessmentsResponse,
        casesResponse,
      ] = await Promise.all([
        fetch("/api/health"),
        fetch("/api/transactions?limit=1000"),
        fetch("/api/assessments?limit=1000"),
        // No status filter: load open, in-review, and closed cases.
        fetch("/api/cases?limit=1000"),
      ]);

      if (!healthResponse.ok) {
        throw new Error("Backend health check failed.");
      }

      if (
        !transactionsResponse.ok ||
        !assessmentsResponse.ok ||
        !casesResponse.ok
      ) {
        throw new Error("Could not load all dashboard data.");
      }

      const [transactions, assessments, cases] = await Promise.all([
        transactionsResponse.json() as Promise<Transaction[]>,
        assessmentsResponse.json() as Promise<Assessment[]>,
        casesResponse.json() as Promise<FraudCase[]>,
      ]);

      setDashboardData({ transactions, assessments, cases });
      setConnectionStatus("connected");
    } catch (error) {
      setConnectionStatus("disconnected");
      setApiError(
        error instanceof Error
          ? error.message
          : "An unexpected error occurred.",
      );
    } finally {
      const elapsedMs = Date.now() - loadingStartedAt;
      const remainingLoadingMs = minimumLoadingDurationMs - elapsedMs;

      if (remainingLoadingMs > 0) {
        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, remainingLoadingMs);
        });
      }

      setIsLoading(false);
    }
  }

  useEffect(() => {
    // Start the initial request after the effect callback completes.
    void Promise.resolve().then(() => loadDashboardData(false));
  }, []);

  async function updateCaseStatus(caseId: number, newStatus: CaseStatus) {
    const currentCase = dashboardData.cases.find(
      (fraudCase) => fraudCase.case_id === caseId,
    );

    if (!currentCase || currentCase.status === newStatus) {
      return;
    }

    setUpdatingCaseId(caseId);
    setCaseUpdateError(null);

    try {
      const response = await fetch(`/api/cases/${caseId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ status: newStatus }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `Could not update case #${caseId}.`);
      }

      // Reload from the API so the displayed status reflects saved data.
      await loadDashboardData();
    } catch (error) {
      setCaseUpdateError(
        error instanceof Error
          ? error.message
          : "An unexpected error occurred while updating the case.",
      );
    } finally {
      setUpdatingCaseId(null);
    }
  }

  const openCaseCount = dashboardData.cases.filter(
    (fraudCase) => fraudCase.status === "open",
  ).length;

  const normalizedTransactionSearch = transactionSearch.trim().toLowerCase();
  const filteredTransactions = dashboardData.transactions.filter(
    (transaction) => {
      const assessment = dashboardData.assessments.find(
        (item) => item.transaction_id === transaction.transaction_id,
      );
      const riskBand = assessment?.risk_band.toLowerCase() ?? "unassessed";
      const matchesSearch =
        normalizedTransactionSearch.length === 0 ||
        [
          transaction.transaction_id,
          transaction.customer_id,
          transaction.merchant_category,
        ].some((value) =>
          value.toLowerCase().includes(normalizedTransactionSearch),
        );
      const matchesRiskBand =
        riskBandFilter === "all" || riskBand === riskBandFilter;
      const matchesChannel =
        channelFilter === "all" || transaction.channel === channelFilter;

      return matchesSearch && matchesRiskBand && matchesChannel;
    },
  );

  const hasActiveTransactionFilters =
    transactionSearch.trim().length > 0 ||
    riskBandFilter !== "all" ||
    channelFilter !== "all";

  function clearTransactionFilters() {
    setTransactionSearch("");
    setRiskBandFilter("all");
    setChannelFilter("all");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon" aria-hidden="true">
            <span>◆</span>
          </div>

          <div className="brand-copy">
            <span className="brand-name">FraudGuard</span>
            <span className="brand-caption">ANALYST WORKSPACE</span>
          </div>
        </div>

        <div className="sidebar-section-label">WORKSPACE</div>

        <nav className="navigation" aria-label="Main navigation">
          {navigationItems.map((item) => (
            <button
              className={`nav-item ${activePage === item.label ? "active" : ""}`}
              key={item.label}
              type="button"
              onClick={() => setActivePage(item.label)}
              aria-current={activePage === item.label ? "page" : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {item.icon}
              </span>

              <span>{item.label}</span>

              {activePage === item.label && (
                <span className="active-indicator" aria-hidden="true" />
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="status-dot" />

          <div>
            <p className="status-title">Rule engine</p>

            <p className="status-description">
              {connectionStatus === "checking"
                ? "Checking backend connection..."
                : connectionStatus === "connected"
                  ? "Backend connected"
                  : "Backend disconnected"}
            </p>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="breadcrumb-separator">/</span>
            <strong>{activePage}</strong>
          </div>

          <div className="topbar-right">
            <span className="environment-label">LOCAL ENVIRONMENT</span>

            <div className="avatar" aria-label="Analyst">
              A
            </div>
          </div>
        </header>

        <main className="main-content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">RULE-BASED FRAUD DETECTOR</p>
              <h1>{activePage}</h1>

              <p className="page-description">
                {activePage === "Overview"
                  ? "Monitor transaction risk and investigate suspicious activity."
                  : activePage === "Transactions"
                    ? "Browse transactions and review their assessment results."
                    : activePage === "Assessments"
                      ? "Inspect risk scores and understand which rules were triggered."
                      : "Review and manage cases requiring investigation."}
              </p>
            </div>

            <div className="heading-badge">
              <span className="heading-badge-dot" />
              Foundation preview
            </div>
          </div>

          {isLoading && (
            <div
              className="dashboard-loading-banner"
              role="status"
              aria-live="polite"
            >
              <span className="dashboard-loading-spinner" aria-hidden="true" />
              <span>Loading dashboard data from the backend…</span>
            </div>
          )}

          {activePage === "Overview" ? (
            <>
              <section className="metrics-grid" aria-label="Dashboard metrics">
                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-label">Transactions</span>
                    <span className="metric-icon blue">⇄</span>
                  </div>

                  <p className="metric-value">
                    {isLoading ? "…" : dashboardData.transactions.length}
                  </p>

                  <p className="metric-note">Records returned by API</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-label">High-risk alerts</span>
                    <span className="metric-icon red">!</span>
                  </div>

                  <p className="metric-value">
                    {isLoading
                      ? "…"
                      : dashboardData.assessments.filter(
                          (assessment) =>
                            assessment.risk_band.toLowerCase() === "high",
                        ).length}
                  </p>

                  <p className="metric-note">High-risk assessments returned</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-label">Assessments</span>
                    <span className="metric-icon purple">◈</span>
                  </div>

                  <p className="metric-value">
                    {isLoading ? "…" : dashboardData.assessments.length}
                  </p>

                  <p className="metric-note">Assessments returned by API</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-label">Open cases</span>
                    <span className="metric-icon amber">▤</span>
                  </div>

                  <p className="metric-value">
                    {isLoading ? "…" : openCaseCount}
                  </p>

                  <p className="metric-note">Cases awaiting review</p>
                </article>
              </section>

              {apiError && (
                <p role="alert" className="setup-note">
                  Dashboard data could not be loaded: {apiError}
                </p>
              )}

              <section className="content-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Analyst workspace</h2>
                    <p>Your shared starting point for fraud monitoring.</p>
                  </div>

                  <span className="panel-tag">
                    {connectionStatus === "connected"
                      ? "API CONNECTED"
                      : connectionStatus === "checking"
                        ? "CONNECTING"
                        : "API DISCONNECTED"}
                  </span>
                </div>

                <div className="welcome-content">
                  <div className="welcome-icon" aria-hidden="true">
                    ◆
                  </div>

                  <h3>
                    {connectionStatus === "connected"
                      ? "Your dashboard is connected"
                      : connectionStatus === "checking"
                        ? "Connecting to your dashboard"
                        : "Backend connection unavailable"}
                  </h3>

                  <p>
                    Transaction records, assessment results, and investigation
                    cases are retrieved from your local FastAPI backend.
                  </p>

                  <div className="setup-note">
                    <span className="setup-note-icon" aria-hidden="true">
                      i
                    </span>
                    The current dataset contains synthetic demonstration
                    records. Risk scores are rule-based weights, not calibrated
                    fraud probabilities.
                  </div>
                </div>
              </section>
            </>
          ) : activePage === "Transactions" ? (
            <section className="content-panel">
              <div className="panel-heading">
                <div>
                  <h2>All transactions</h2>
                  <p>Review transaction records and their risk assessments.</p>
                </div>

                <span className="panel-tag">
                  {isLoading
                    ? "LOADING"
                    : `${filteredTransactions.length} OF ${dashboardData.transactions.length} RECORDS`}
                </span>
              </div>

              <div
                className="transaction-filters"
                aria-label="Filter transactions"
              >
                <label className="transaction-filter-field">
                  <span>Search</span>
                  <input
                    type="search"
                    value={transactionSearch}
                    onChange={(event) =>
                      setTransactionSearch(event.target.value)
                    }
                    placeholder="Transaction, customer, or merchant"
                    aria-label="Search transactions"
                  />
                </label>

                <label className="transaction-filter-field">
                  <span>Risk band</span>
                  <select
                    value={riskBandFilter}
                    onChange={(event) => setRiskBandFilter(event.target.value)}
                    aria-label="Filter by risk band"
                  >
                    <option value="all">All risk bands</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                    <option value="unassessed">Not assessed</option>
                  </select>
                </label>

                <label className="transaction-filter-field">
                  <span>Channel</span>
                  <select
                    value={channelFilter}
                    onChange={(event) => setChannelFilter(event.target.value)}
                    aria-label="Filter by channel"
                  >
                    <option value="all">All channels</option>
                    <option value="card">Card</option>
                    <option value="online">Online</option>
                    <option value="transfer">Transfer</option>
                  </select>
                </label>

                {hasActiveTransactionFilters && (
                  <button
                    className="clear-filters-button"
                    type="button"
                    onClick={clearTransactionFilters}
                  >
                    Clear filters
                  </button>
                )}
              </div>

              {apiError && (
                <p role="alert" className="setup-note">
                  Transactions could not be loaded: {apiError}
                </p>
              )}

              {isLoading ? (
                <p className="empty-state">Loading transactions...</p>
              ) : dashboardData.transactions.length === 0 ? (
                <p className="empty-state">
                  No transactions were returned by the API.
                </p>
              ) : filteredTransactions.length === 0 ? (
                <div className="filter-empty-state">
                  <p className="empty-state">
                    No transactions match the current filters.
                  </p>
                  <button
                    className="clear-filters-button"
                    type="button"
                    onClick={clearTransactionFilters}
                  >
                    Clear filters and show all transactions
                  </button>
                </div>
              ) : (
                <div className="table-container">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Transaction ID</th>
                        <th>Customer</th>
                        <th>Amount</th>
                        <th>Risk band</th>
                        <th>Risk score</th>
                        <th>Merchant category</th>
                        <th>Channel</th>
                        <th>Country</th>
                        <th>Timestamp</th>
                      </tr>
                    </thead>

                    <tbody>
                      {filteredTransactions.map((transaction) => {
                        const assessment = dashboardData.assessments.find(
                          (item) =>
                            item.transaction_id === transaction.transaction_id,
                        );

                        return (
                          <tr key={transaction.transaction_id}>
                            <td>{transaction.transaction_id}</td>
                            <td>{transaction.customer_id}</td>
                            <td>
                              {transaction.currency}{" "}
                              {Number(transaction.amount).toLocaleString()}
                            </td>
                            <td>
                              {assessment ? (
                                <span
                                  className={`risk-badge risk-${assessment.risk_band.toLowerCase()}`}
                                >
                                  {formatRiskBand(assessment.risk_band)}
                                </span>
                              ) : (
                                <span className="risk-badge risk-unassessed">
                                  Not assessed
                                </span>
                              )}
                            </td>
                            <td>{assessment ? assessment.risk_score : "—"}</td>
                            <td>{transaction.merchant_category}</td>
                            <td>
                              <span className="channel-badge">
                                {transaction.channel}
                              </span>
                            </td>
                            <td>{transaction.country_code ?? "—"}</td>
                            <td>{formatDate(transaction.timestamp)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          ) : activePage === "Assessments" ? (
            <section className="content-panel">
              <div className="panel-heading">
                <div>
                  <h2>All assessments</h2>
                  <p>
                    Review risk scores and the rules evaluated for each
                    transaction.
                  </p>
                </div>

                <span className="panel-tag">
                  {isLoading
                    ? "LOADING"
                    : `${dashboardData.assessments.length} RECORDS`}
                </span>
              </div>

              {apiError && (
                <p role="alert" className="setup-note">
                  Assessments could not be loaded: {apiError}
                </p>
              )}

              {isLoading ? (
                <p className="empty-state">Loading assessments...</p>
              ) : dashboardData.assessments.length === 0 ? (
                <p className="empty-state">
                  No assessments were returned by the API.
                </p>
              ) : (
                <div className="assessment-list">
                  {dashboardData.assessments.map((assessment) => (
                    <details
                      className="assessment-item"
                      key={assessment.assessment_id}
                    >
                      <summary className="assessment-summary">
                        <div className="assessment-main">
                          <strong>
                            Assessment #{assessment.assessment_id}
                          </strong>

                          <span>{assessment.transaction_id}</span>
                        </div>

                        <div className="assessment-meta">
                          <span
                            className={`risk-badge risk-${assessment.risk_band.toLowerCase()}`}
                          >
                            {formatRiskBand(assessment.risk_band)}
                          </span>

                          <span className="assessment-score">
                            Score: {assessment.risk_score}
                          </span>
                        </div>
                      </summary>

                      <div className="assessment-details">
                        <p className="assessment-timestamp">
                          Created: {formatDate(assessment.created_at)}
                        </p>

                        <h3>Rule results</h3>

                        {assessment.rule_results.length === 0 ? (
                          <p className="empty-state">
                            No rule results are available for this assessment.
                          </p>
                        ) : (
                          <div className="rule-results">
                            {assessment.rule_results.map((rule) => (
                              <article
                                className="rule-result"
                                key={rule.rule_id}
                              >
                                <div className="rule-result-heading">
                                  <strong>{rule.name}</strong>

                                  <span
                                    className={`rule-status status-${rule.status.replace(/_/g, "-")}`}
                                  >
                                    {rule.status.replace(/_/g, " ")}
                                  </span>
                                </div>

                                <p>{rule.explanation}</p>

                                <span className="rule-score">
                                  Score contribution: {rule.score}
                                </span>
                              </article>
                            ))}
                          </div>
                        )}
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </section>
          ) : (
            <section className="content-panel">
              <div className="panel-heading">
                <div>
                  <h2>Fraud investigation cases</h2>
                  <p>
                    Review cases created for transactions that need analyst
                    attention.
                  </p>
                </div>

                <span className="panel-tag">
                  {isLoading
                    ? "LOADING"
                    : `${openCaseCount} OPEN · ${dashboardData.cases.length} TOTAL`}
                </span>
              </div>

              {apiError && (
                <p role="alert" className="setup-note">
                  Cases could not be loaded: {apiError}
                </p>
              )}

              {caseUpdateError && (
                <p role="alert" className="setup-note">
                  Case status could not be updated: {caseUpdateError}
                </p>
              )}

              {isLoading ? (
                <p className="empty-state">Loading cases...</p>
              ) : dashboardData.cases.length === 0 ? (
                <p className="empty-state">
                  There are currently no investigation cases.
                </p>
              ) : (
                <div className="case-list">
                  {dashboardData.cases.map((fraudCase) => {
                    const assessment = dashboardData.assessments.find(
                      (item) => item.assessment_id === fraudCase.assessment_id,
                    );

                    return (
                      <article className="case-item" key={fraudCase.case_id}>
                        <div className="case-heading">
                          <div>
                            <h3>Case #{fraudCase.case_id}</h3>
                            <p className="case-transaction">
                              Transaction: {fraudCase.transaction_id}
                            </p>
                          </div>

                          <label>
                            <span className="sr-only">
                              Status for case #{fraudCase.case_id}
                            </span>
                            <select
                              className={`case-status-select status-${fraudCase.status.replace(/_/g, "-")}`}
                              value={fraudCase.status}
                              disabled={updatingCaseId === fraudCase.case_id}
                              onChange={(event) =>
                                void updateCaseStatus(
                                  fraudCase.case_id,
                                  event.target.value as CaseStatus,
                                )
                              }
                            >
                              <option value="open">Open</option>
                              <option value="in_review">In Review</option>
                              <option value="closed">Closed</option>
                            </select>
                          </label>
                        </div>

                        <div className="case-meta">
                          <span>Assessment: #{fraudCase.assessment_id}</span>

                          {assessment && (
                            <span
                              className={`risk-badge risk-${assessment.risk_band.toLowerCase()}`}
                            >
                              {formatRiskBand(assessment.risk_band)} risk ·
                              Score {assessment.risk_score}
                            </span>
                          )}
                        </div>

                        <p className="case-notes">
                          {fraudCase.notes ||
                            "No investigation notes provided."}
                        </p>

                        <div className="case-timestamps">
                          <span>
                            Created: {formatDate(fraudCase.created_at)}
                          </span>

                          <span>
                            Updated: {formatDate(fraudCase.updated_at)}
                          </span>
                        </div>

                        {updatingCaseId === fraudCase.case_id && (
                          <p className="case-update-message" role="status">
                            Saving status...
                          </p>
                        )}
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          <footer className="page-footer">
            <span>© {new Date().getFullYear()} Rule-Based Fraud Detector</span>
            <span>Intelligent fraud detection. Explainable decisions.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default App;
