export type RunState =
  "running" | "waiting_user" | "succeeded" | "cancelled" | "failed";

export interface TripRequestInput {
  request_id: string;
  origin: string;
  destination: string;
  start_date: string;
  end_date: string;
  days: number;
  timezone: string;
  party: { adults: number; children: number; seniors: number };
  budget: {
    total: string;
    currency: string;
    policy: "hard" | "flexible";
    flexibility_percent: number;
  };
  hard_constraints: {
    must_visit: string[];
    excluded_places: string[];
  };
  soft_preferences: {
    interests: string[];
    pace: "relaxed" | "balanced" | "intensive";
    transport_modes: string[];
  };
  natural_language_notes: string;
}

export interface TripRun {
  run_id: string;
  state: RunState;
  fixture_mode: boolean;
  request: TripRequestInput;
  created_at: string;
  updated_at: string;
  result_available: boolean;
  current_version: number;
}

export interface RunSession {
  run: TripRun;
  accessToken: string;
}

export interface RunProgressEvent {
  sequence: number;
  event_type: string;
  task_id: string | null;
  agent_id: string | null;
  state: string;
  message: string;
  occurred_at: string;
  artifact_ids: string[];
  tool_calls: string[];
  estimated_cost_microunits: number;
}

export interface CostEstimate {
  kind: "known" | "range" | "unknown";
  currency: string;
  lower: string | null;
  upper: string | null;
  basis: string;
}

export interface ItineraryItem {
  plan_item_id: string;
  start_at: string;
  end_at: string;
  place_id: string;
  activity: string;
  estimated_cost: CostEstimate;
  travel_mode_from_previous: string | null;
  travel_minutes_from_previous: number | null;
  evidence_ids: string[];
}

export interface ItineraryPlan {
  plan_id: string;
  title: string;
  strategy: string;
  days: Array<{
    date: string;
    timezone: string;
    items: ItineraryItem[];
    daily_cost: CostEstimate;
    warnings: string[];
  }>;
  total_cost: CostEstimate;
  score_breakdown: Record<string, number>;
  is_executable: boolean;
  unresolved_risks: string[];
}

export interface PlanComparisonEntry {
  plan_id: string;
  total_cost: CostEstimate;
  activity_count: number;
  commute_minutes: number;
  free_minutes: number;
  preference_coverage: number;
  risk_count: number;
  evidence_coverage: number;
}

export interface TripRunResult {
  run_id: string;
  version: number;
  bundle: {
    plans: ItineraryPlan[];
    comparison: { entries: PlanComparisonEntry[] };
    evidence: Array<{
      evidence_id: string;
      source_name: string;
      source_url_or_provider_id: string;
      freshness: string;
    }>;
    assumptions: string[];
    collaboration_summary: { revision_rounds: number };
  };
  map_points: Array<{
    place_id: string;
    name: string;
    longitude: number;
    latitude: number;
    evidence_ids: string[];
  }>;
  markdown: string;
}

export interface RunVersionSummary {
  version: number;
  instruction: string;
  created_at: string;
  changed_task_ids: string[];
  invalidated_artifact_ids: string[];
  current: boolean;
}

export interface RunVersionDiff {
  run_id: string;
  from_version: number;
  to_version: number;
  changed_paths: string[];
  summary: string;
}

export interface ApiProblem {
  message: string;
  status?: number;
}
