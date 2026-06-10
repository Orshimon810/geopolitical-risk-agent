export interface User {
  id: string;
  email: string;
  full_name: string;
  tier: string;
  is_active: boolean;
}

export type Confidence = "Low" | "Medium" | "High";
export type TaskStatus = "PENDING" | "PROCESSING" | "SUCCESS" | "FAILED";

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

export interface AnalysisResult {
  market_impacts: string[];
  risks: string[];
  scenarios: string[];
  investor_takeaway: string[];
  confidence: Confidence;
  sources: string[];
  signals: Signals;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  result: AnalysisResult | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface HistoryItem {
  id: string;
  query: string;
  confidence: Confidence;
  created_at: string;
  market_impacts: string[];
  result: AnalysisResult | null;
}
