import type {
  AgentConfig,
  BacktestJob,
  BacktestRun,
  BacktestRunDetail,
  HistoryPayload,
  OverviewPayload,
  PolicyVersion,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed for ${path}`)
  }

  return response.json() as Promise<T>
}

export function fetchOverview() {
  return request<OverviewPayload>('/api/overview')
}

export function fetchAgents() {
  return request<AgentConfig[]>('/api/agents')
}

export function saveAgent(agent: AgentConfig) {
  return request<{ id: string; agent_name: string }>('/api/agents', {
    method: 'POST',
    body: JSON.stringify(agent),
  })
}

export function fetchPolicies() {
  return request<PolicyVersion[]>('/api/policies')
}

export function savePolicy(payload: {
  policy_name: string
  version: string
  status: string
  thresholds: Record<string, number>
  risk_params: Record<string, unknown>
  strategy_config: Record<string, unknown>
  notes: string
}) {
  return request<{ id: string }>('/api/policies', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchBacktestJobs() {
  return request<BacktestJob[]>('/api/backtests/jobs')
}

export function createBacktestJob(payload: Record<string, unknown>) {
  return request<{ job_id: string; status: string }>('/api/backtests/jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchBacktestRuns() {
  return request<BacktestRun[]>('/api/backtests/runs')
}

export function fetchBacktestRun(runId: string) {
  return request<BacktestRunDetail>(`/api/backtests/runs/${runId}`)
}

export function savePromotion(payload: {
  agent_config_id: string
  new_policy_version_id: string
  source_run_id?: string | null
  promoted_by?: string
  rationale: string
  metadata?: Record<string, unknown>
}) {
  return request<{ id: string }>('/api/promotions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchHistory(agentName?: string) {
  const query = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : ''
  return request<HistoryPayload>(`/api/history${query}`)
}
