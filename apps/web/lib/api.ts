const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const API_ACCESS_TOKEN = process.env.NEXT_PUBLIC_API_ACCESS_TOKEN;

export type PlanCreateResponse = {
  plan_id: string;
  status: string;
  parsed_goal: Record<string, unknown>;
  warnings?: string[];
  llm_used?: boolean;
};

export type RouteItem = {
  item_id: string;
  date?: string | null;
  start_time: string;
  end_time: string;
  spot_name: string;
  shoot_goal: string;
  final_score?: number;
  light_label?: string;
  location_hint?: string;
  transfer_to_next?: {
    mode?: string;
    mode_label?: string;
    summary?: string;
    duration_minutes?: number;
    source?: string;
    recommendation_reason?: string;
    travel_options?: TravelOption[];
  };
  guide: Record<string, unknown>;
};

export type TravelOption = {
  mode?: string;
  mode_label?: string;
  summary?: string;
  duration_minutes?: number;
  distance_m?: number;
  cost?: string;
  score?: number;
  success?: boolean;
  source?: string;
  recommendation_reason?: string;
};

export type SpotTimeOption = {
  option_id: string;
  spot_name: string;
  time_window: string;
  shoot_goal: string;
  light_label?: string;
  final_score: number;
  risks: string[];
  recommended_shots: string[];
};

export type AgentStep = {
  task_id: string;
  step_type?: string;
  reasoning_summary?: string;
  tool_name?: string | null;
  tool_input?: Record<string, unknown>;
  tool_output?: Record<string, unknown> | unknown[];
  observation?: Record<string, unknown> | unknown[];
  duration_ms?: number | null;
};

export type RepairIssue = {
  code?: string;
  severity?: string;
  message?: string;
  evidence?: Record<string, unknown>;
};

export type RepairContext = {
  evaluation?: {
    status?: string;
    issues?: RepairIssue[];
    recommended_action?: string;
    needs_llm_review?: boolean;
  };
  llm_used?: boolean;
  llm_review?: {
    decision?: string;
    user_facing_warning?: string;
    confidence?: number;
    evidence_refs?: string[];
  } | null;
  applied?: boolean;
  llm_warning?: string;
};

export type PlanGenerateResponse = {
  plan_id: string;
  status: string;
  parsed_goal: Record<string, unknown>;
  visual_goal: Record<string, unknown>;
  weather_context: Record<string, unknown>;
  sunlight_context: Record<string, unknown>;
  map_context: Record<string, unknown>;
  reference_context: Record<string, unknown>;
  discovery_context: Record<string, unknown>;
  image_analysis: Record<string, unknown>;
  repair_context: RepairContext;
  task_plan: Array<Record<string, unknown>>;
  agent_steps: AgentStep[];
  final_markdown: string;
  route: RouteItem[];
  spot_time_options: SpotTimeOption[];
  backup_plan: Array<Record<string, unknown>>;
  warnings: string[];
  llm_used: boolean;
};

export type PlanSummary = {
  plan_id: string;
  status?: string | null;
  user_input: string;
  destination?: string | null;
  date_range: string[];
  warnings: string[];
  llm_used: boolean;
  final_markdown?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type PlanResponse = PlanGenerateResponse & {
  user_input: string;
  reference_images: string[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type PlanMessage = {
  id: string;
  plan_id: string;
  role: "user" | "assistant" | string;
  content: string;
  reference_images: string[];
  tool_requests: Array<Record<string, unknown>>;
  tool_results: Array<Record<string, unknown>>;
  response: Record<string, unknown>;
  warnings: string[];
  created_at?: string | null;
};

export type FollowUpResponse = {
  plan_id: string;
  status: string;
  answer: string;
  changes: Array<Record<string, unknown>>;
  tool_results: Array<Record<string, unknown>>;
  warnings: string[];
  messages: PlanMessage[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (API_ACCESS_TOKEN) {
    headers.set("x-api-token", API_ACCESS_TOKEN);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers
  });

  if (!response.ok) {
    const detail = await response.text();
    let message = detail;
    try {
      const payload = JSON.parse(detail) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (payload.detail) {
        message = JSON.stringify(payload.detail);
      }
    } catch {
      message = detail;
    }
    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function createPlan(userInput: string, referenceImages: string[] = []): Promise<PlanCreateResponse> {
  return request<PlanCreateResponse>("/api/plans", {
    method: "POST",
    body: JSON.stringify({ user_input: userInput, reference_images: referenceImages })
  });
}

export async function generatePlan(planId: string): Promise<PlanGenerateResponse> {
  return request<PlanGenerateResponse>(`/api/plans/${planId}/generate`, {
    method: "POST"
  });
}

export async function listPlans(): Promise<PlanSummary[]> {
  return request<PlanSummary[]>("/api/plans");
}

export async function getPlan(planId: string): Promise<PlanResponse> {
  return request<PlanResponse>(`/api/plans/${planId}`);
}

export async function getPlanMessages(planId: string): Promise<PlanMessage[]> {
  return request<PlanMessage[]>(`/api/plans/${planId}/messages`);
}

export async function deletePlan(planId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/plans/${planId}`, {
    method: "DELETE"
  });
}

export async function askFollowUp(
  planId: string,
  question: string,
  referenceImages: string[] = []
): Promise<FollowUpResponse> {
  return request<FollowUpResponse>(`/api/plans/${planId}/followups`, {
    method: "POST",
    body: JSON.stringify({ question, reference_images: referenceImages })
  });
}
