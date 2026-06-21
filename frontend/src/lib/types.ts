export interface User {
  id: string;
  email: string;
  full_name: string;
  tier: string;
  is_active: boolean;
}

export type Confidence = "Low" | "Medium" | "High";
export type TaskStatus = "PENDING" | "PROCESSING" | "WAITING_FOR_INPUT" | "SUCCESS" | "FAILED";

export interface MarketDataEntry {
  label: string;
  price: number;
  change_1d_pct: number | null;
  status: "ok" | "error";
  error?: string;
}

export interface WorldBankEntry {
  status: "ok" | "no_data" | "error";
  year?: string;
  value?: number;
  source?: string;
  error?: string;
}

export interface CountrySignals {
  trade_gdp: WorldBankEntry;
  oil_rents?: WorldBankEntry;
}

export interface Signals {
  market_data?: Record<string, MarketDataEntry>;
  countries?: Record<string, CountrySignals>;
  note?: string;
}

export type AssetType = "stock" | "etf" | "crypto" | "commodity" | "bond";
export type Verdict = "Bullish" | "Bearish" | "Neutral";

export interface PortfolioHolding {
  id: string;
  ticker: string;
  name: string;
  asset_type: AssetType;
  quantity: number | null;
  cost_basis_usd: number | null;
  last_price_usd: number | null;
  price_updated_at: string | null;
  created_at: string;
}

export interface PortfolioHoldingImpact {
  ticker: string;
  name: string;
  verdict: Verdict;
  short_term_impact: string;
  long_term_impact: string;
  confidence: Confidence;
  reasoning: string;
}

export interface AnalysisResult {
  market_impacts: string[];
  risks: string[];
  scenarios: string[];
  investor_takeaway: string[];
  confidence: Confidence;
  sources: string[];
  signals: Signals;
  portfolio_impacts?: PortfolioHoldingImpact[] | null;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  result: AnalysisResult | null;
  error: string | null;
  sub_questions?: string[];
  created_at: string;
  completed_at: string | null;
  /** Set after successful DB persist — used by the feedback flow */
  analysis_id?: string | null;
}

export interface FeedbackRequest {
  score: 0 | 1;
  comment?: string;
}

export interface FeedbackResponse {
  status: string;
  feedback_id?: string | null;
}

export interface HistoryItem {
  id: string;
  query: string;
  confidence: Confidence;
  created_at: string;
  market_impacts: string[];
  result: AnalysisResult | null;
}
