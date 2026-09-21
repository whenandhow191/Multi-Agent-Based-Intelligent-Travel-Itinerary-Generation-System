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

export interface ApiProblem {
  message: string;
  status?: number;
}
