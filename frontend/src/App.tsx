import {
  Activity,
  ArrowUpRight,
  Bot,
  BrainCircuit,
  FlaskConical,
  Gauge,
  History as HistoryIcon,
  Play,
  Radar,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  TimerReset,
  Wallet,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  startTransition,
  type FormEvent,
  type ReactNode,
  useDeferredValue,
  useEffect,
  useState,
} from 'react'

import {
  createBacktestJob,
  fetchAgents,
  fetchBacktestJobs,
  fetchBacktestRun,
  fetchBacktestRuns,
  fetchHistory,
  fetchOverview,
  fetchPolicies,
  saveAgent,
  savePromotion,
  savePolicy,
} from './api'
import type {
  AgentConfig,
  BacktestJob,
  BacktestRun,
  BacktestRunDetail,
  DashboardView,
  HistoryPayload,
  HistoryRecord,
  OverviewPayload,
  PolicyVersion,
  PromotionRecord,
} from './types'

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

const decimalFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 2,
})

const compactNumberFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

const dateTimeFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

const blankAgent = (): AgentConfig => ({
  agent_name: '',
  description: '',
  status: 'active',
  broker: 'alpaca',
  mode: 'paper',
  symbols: ['ETH/USD'],
  decision_interval_seconds: 60,
  max_trades_per_hour: 6,
  max_risk_per_trade_pct: 0.005,
  max_daily_loss_pct: 0.02,
  max_position_notional_usd: 100,
  max_spread_bps: 20,
  min_decision_confidence: 0.6,
  cooldown_seconds_after_trade: 60,
  enable_agent_orders: false,
  strategy_policy_version_id: null,
  risk_params: {},
  analyst_params: {},
  execution_params: {},
  notes: '',
})

const blankPolicy = () => ({
  policy_name: '',
  version: 'v1',
  status: 'candidate',
  thresholds: {
    entry_momentum_3_bps: 8,
    entry_momentum_5_bps: 12,
    exit_momentum_3_bps: -8,
    exit_momentum_5_bps: -12,
    max_spread_bps: 20,
  },
  risk_params: {},
  strategy_config: {
    max_volatility_5_bps: 25,
  },
  notes: '',
})

const blankBacktest = () => ({
  run_name: 'primary-walk-forward',
  symbol: 'ETH/USD',
  timeframe: '1Min',
  location: 'us',
  lookback_days: 365,
  train_window_days: 90,
  test_window_days: 30,
  step_days: 30,
  warmup_bars: 20,
  starting_cash_usd: 10000,
  baseline_policy_version_id: '',
  candidate_policy_version_ids: [] as string[],
  agent_config_id: null as string | null,
  notes: '',
})

const navigation = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'strategies', label: 'Strategies', icon: BrainCircuit },
  { id: 'backtests', label: 'Backtests', icon: FlaskConical },
  { id: 'history', label: 'History', icon: HistoryIcon },
] satisfies Array<{ id: DashboardView; label: string; icon: typeof Gauge }>

function formatMoney(value: unknown) {
  const numeric = Number(value ?? 0)
  return Number.isFinite(numeric) ? currencyFormatter.format(numeric) : '—'
}

function formatCompact(value: unknown) {
  const numeric = Number(value ?? 0)
  return Number.isFinite(numeric) ? compactNumberFormatter.format(numeric) : '—'
}

function formatSigned(value: unknown, suffix = '') {
  const numeric = Number(value ?? 0)
  if (!Number.isFinite(numeric)) {
    return '—'
  }
  const sign = numeric > 0 ? '+' : ''
  return `${sign}${decimalFormatter.format(numeric)}${suffix}`
}

function formatDate(value: unknown) {
  if (!value) {
    return '—'
  }
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return dateTimeFormatter.format(date)
}

function labelForPolicy(policy: PolicyVersion) {
  return `${policy.policy_name}@${policy.version}`
}

function normalizeStatusTone(status: string | null | undefined) {
  const value = (status ?? '').toLowerCase()
  if (value === 'healthy' || value === 'active' || value === 'completed') {
    return 'positive'
  }
  if (value === 'paused' || value === 'queued' || value === 'running' || value === 'shadow') {
    return 'neutral'
  }
  return 'negative'
}

function filterRows<T extends object>(rows: T[], query: string) {
  if (!query.trim()) {
    return rows
  }
  const needle = query.trim().toLowerCase()
  return rows.filter((row) =>
    Object.values(row as Record<string, unknown>).some((value) =>
      String(value ?? '').toLowerCase().includes(needle),
    ),
  )
}

function toAgentForm(agent: AgentConfig | null | undefined) {
  if (!agent) {
    return blankAgent()
  }
  return {
    ...blankAgent(),
    ...agent,
    description: agent.description ?? '',
    notes: agent.notes ?? '',
  }
}

function resolvePolicyName(policyId: string | null | undefined, policies: PolicyVersion[]) {
  if (!policyId) {
    return 'Unassigned'
  }
  const policy = policies.find((entry) => entry.id === policyId)
  return policy ? `${labelForPolicy(policy)} [${policy.status}]` : 'Unknown strategy'
}

async function fetchCoreBundle() {
  return Promise.all([
    fetchOverview(),
    fetchAgents(),
    fetchPolicies(),
    fetchBacktestJobs(),
    fetchBacktestRuns(),
  ])
}

function App() {
  const [view, setView] = useState<DashboardView>('overview')
  const [overview, setOverview] = useState<OverviewPayload | null>(null)
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [policies, setPolicies] = useState<PolicyVersion[]>([])
  const [jobs, setJobs] = useState<BacktestJob[]>([])
  const [runs, setRuns] = useState<BacktestRun[]>([])
  const [runDetail, setRunDetail] = useState<BacktestRunDetail | null>(null)
  const [history, setHistory] = useState<HistoryPayload | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string>('')
  const [selectedAgentName, setSelectedAgentName] = useState<string>('create')
  const [historyAgentName, setHistoryAgentName] = useState<string>('all')
  const [historyKind, setHistoryKind] = useState<
    'decisions' | 'orders' | 'outcomes' | 'lessons' | 'promotions'
  >('decisions')
  const [historyQuery, setHistoryQuery] = useState('')
  const [agentDraft, setAgentDraft] = useState<AgentConfig>(blankAgent())
  const [policyDraft, setPolicyDraft] = useState(blankPolicy())
  const [backtestDraft, setBacktestDraft] = useState(blankBacktest())
  const [promotionDraft, setPromotionDraft] = useState<{
    agent_config_id: string
    new_policy_version_id: string
    rationale: string
  }>({
    agent_config_id: '',
    new_policy_version_id: '',
    rationale: '',
  })
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [savingAgent, setSavingAgent] = useState(false)
  const [savingPolicy, setSavingPolicy] = useState(false)
  const [runningBacktest, setRunningBacktest] = useState(false)
  const [promotingStrategy, setPromotingStrategy] = useState(false)
  const [message, setMessage] = useState<string>('')
  const [error, setError] = useState<string>('')

  const deferredHistoryQuery = useDeferredValue(historyQuery)

  async function syncCoreState() {
    setSyncing(true)
    setError('')
    try {
      const [overviewPayload, agentPayload, policyPayload, jobPayload, runPayload] =
        await fetchCoreBundle()
      setOverview(overviewPayload)
      setAgents(agentPayload)
      setPolicies(policyPayload)
      setJobs(jobPayload)
      setRuns(runPayload)
      setLoading(false)
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'Failed to refresh dashboard data.')
      setLoading(false)
    } finally {
      setSyncing(false)
    }
  }

  async function syncHistoryState(agentName?: string) {
    try {
      const payload = await fetchHistory(agentName)
      setHistory(payload)
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'Failed to refresh history.')
    }
  }

  useEffect(() => {
    let cancelled = false

    const runCore = async () => {
      setSyncing(true)
      setError('')
      try {
        const [overviewPayload, agentPayload, policyPayload, jobPayload, runPayload] =
          await fetchCoreBundle()
        if (cancelled) {
          return
        }
        setOverview(overviewPayload)
        setAgents(agentPayload)
        setPolicies(policyPayload)
        setJobs(jobPayload)
        setRuns(runPayload)
        setLoading(false)
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError instanceof Error ? fetchError.message : 'Failed to refresh dashboard data.')
          setLoading(false)
        }
      } finally {
        if (!cancelled) {
          setSyncing(false)
        }
      }
    }

    const runHistory = async (agentName?: string) => {
      try {
        const payload = await fetchHistory(agentName)
        if (!cancelled) {
          setHistory(payload)
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError instanceof Error ? fetchError.message : 'Failed to refresh history.')
        }
      }
    }

    void runCore()
    void runHistory()

    const interval = window.setInterval(() => {
      void runCore()
      if (view === 'history' || view === 'overview') {
        void runHistory(historyAgentName === 'all' ? undefined : historyAgentName)
      }
    }, 15000)

    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [historyAgentName, view])

  useEffect(() => {
    if (!selectedRunId && runs.length > 0) {
      setSelectedRunId(runs[0].id)
    }
  }, [runs, selectedRunId])

  useEffect(() => {
    if (!selectedRunId) {
      setRunDetail(null)
      return
    }

    let cancelled = false

    async function loadRunDetail() {
      try {
        const payload = await fetchBacktestRun(selectedRunId)
        if (!cancelled) {
          setRunDetail(payload)
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError instanceof Error ? fetchError.message : 'Failed to load backtest details.')
        }
      }
    }

    void loadRunDetail()

    return () => {
      cancelled = true
    }
  }, [selectedRunId])

  useEffect(() => {
    if (!agents.length) {
      return
    }

    if (selectedAgentName === 'create') {
      return
    }

    const agent = agents.find((item) => item.agent_name === selectedAgentName)
    if (agent) {
      setAgentDraft(toAgentForm(agent))
    }
  }, [agents, selectedAgentName])

  useEffect(() => {
    if (!policies.length) {
      return
    }

    setBacktestDraft((current) => {
      if (current.baseline_policy_version_id && current.candidate_policy_version_ids.length) {
        return current
      }

      const baseline = policies.find((policy) => policy.status === 'baseline') ?? policies[0]
      const candidates = policies
        .filter((policy) => policy.id !== baseline.id && policy.status === 'candidate')
        .slice(0, 2)
        .map((policy) => policy.id)

      return {
        ...current,
        baseline_policy_version_id: baseline.id,
        candidate_policy_version_ids: candidates,
      }
    })
  }, [policies])

  useEffect(() => {
    if (!agents.length) {
      return
    }

    setBacktestDraft((current) => {
      if (current.agent_config_id) {
        return current
      }
      return {
        ...current,
        agent_config_id: agents[0].id ?? null,
      }
    })
  }, [agents])

  useEffect(() => {
    if (!agents.length || !policies.length) {
      return
    }

    setPromotionDraft((current) => ({
      agent_config_id: current.agent_config_id || agents[0].id || '',
      new_policy_version_id: current.new_policy_version_id || policies[0].id,
      rationale: current.rationale,
    }))
  }, [agents, policies])

  async function handleAgentSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSavingAgent(true)
    setMessage('')
    setError('')
    try {
      const normalizedSymbol = agentDraft.symbols[0]?.trim() || ''
      if (!normalizedSymbol) {
        throw new Error('A single symbol is required for each agent.')
      }
      const payload = {
        ...agentDraft,
        agent_name: agentDraft.agent_name.trim(),
        symbols: [normalizedSymbol],
      }
      await saveAgent(payload)
      setMessage(`Saved agent ${payload.agent_name}.`)
      setSelectedAgentName(payload.agent_name)
      await syncCoreState()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Failed to save agent.')
    } finally {
      setSavingAgent(false)
    }
  }

  async function handlePolicySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSavingPolicy(true)
    setMessage('')
    setError('')
    try {
      await savePolicy(policyDraft)
      setMessage(`Saved strategy ${policyDraft.policy_name}@${policyDraft.version}.`)
      setPolicyDraft(blankPolicy())
      await syncCoreState()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Failed to save strategy.')
    } finally {
      setSavingPolicy(false)
    }
  }

  async function handleBacktestSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setRunningBacktest(true)
    setMessage('')
    setError('')
    try {
      const response = await createBacktestJob(backtestDraft)
      setMessage(`Queued backtest job ${response.job_id}.`)
      await syncCoreState()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Failed to queue backtest.')
    } finally {
      setRunningBacktest(false)
    }
  }

  async function handlePromotionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPromotingStrategy(true)
    setMessage('')
    setError('')
    try {
      if (!promotionDraft.agent_config_id || !promotionDraft.new_policy_version_id) {
        throw new Error('Choose both an agent and a strategy to promote.')
      }
      if (promotionDraft.rationale.trim().length < 8) {
        throw new Error('Add a short rationale before promoting a strategy.')
      }

      await savePromotion({
        agent_config_id: promotionDraft.agent_config_id,
        new_policy_version_id: promotionDraft.new_policy_version_id,
        source_run_id: selectedRunId || null,
        rationale: promotionDraft.rationale.trim(),
        metadata: {
          source: 'manual_promotion',
          selected_run_name: selectedRun?.run_name ?? null,
          comparison_status: selectedRun?.decision_payload?.status ?? null,
        },
      })
      setMessage('Promoted strategy to the selected agent.')
      setPromotionDraft((current) => ({ ...current, rationale: '' }))
      await syncCoreState()
      await syncHistoryState(historyAgentName === 'all' ? undefined : historyAgentName)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Failed to promote strategy.')
    } finally {
      setPromotingStrategy(false)
    }
  }

  const cumulativeOutcomeSeries =
    overview?.outcomes.reduce<Array<{ index: number; label: string; cumulative: number }>>(
      (series, record, index) => {
        const previous = index === 0 ? 0 : series[index - 1].cumulative
        const delta = Number(record.cash_delta ?? 0)
        series.push({
          index,
          label: formatDate(record.recorded_at),
          cumulative: previous + delta,
        })
        return series
      },
      [],
    ) ?? []

  const outcomeMix =
    overview?.outcomes.reduce<Record<string, number>>((accumulator, record) => {
      const key = String(record.outcome ?? 'unknown')
      accumulator[key] = (accumulator[key] ?? 0) + 1
      return accumulator
    }, {}) ?? {}

  const outcomeMixSeries = Object.entries(outcomeMix).map(([outcome, count]) => ({
    outcome,
    count,
  }))

  const scoreTrendSeries =
    runs.map((run) => ({
      label: formatDate(run.created_at),
      baseline: Number(run.baseline_metrics?.score ?? 0),
      candidate: Number(run.candidate_metrics?.score ?? 0),
    })) ?? []

  const comparisonSeries = runDetail
    ? [
        {
          metric: 'Score',
          baseline: Number(runDetail.run.baseline_metrics?.score ?? 0),
          candidate: Number(runDetail.run.candidate_metrics?.score ?? 0),
        },
        {
          metric: 'Realized PnL',
          baseline: Number(runDetail.run.baseline_metrics?.realized_pnl_bps ?? 0),
          candidate: Number(runDetail.run.candidate_metrics?.realized_pnl_bps ?? 0),
        },
        {
          metric: 'Avg Trade',
          baseline: Number(runDetail.run.baseline_metrics?.average_trade_bps ?? 0),
          candidate: Number(runDetail.run.candidate_metrics?.average_trade_bps ?? 0),
        },
        {
          metric: 'Max Drawdown',
          baseline: Number(runDetail.run.baseline_metrics?.max_drawdown_bps ?? 0),
          candidate: Number(runDetail.run.candidate_metrics?.max_drawdown_bps ?? 0),
        },
      ]
    : []

  const windowScoreSeries =
    runDetail?.windows.map((window) => ({
      label: `W${window.window_index}`,
      baseline: Number(window.metrics.baseline_metrics?.score ?? 0),
      selected: Number(window.metrics.metrics?.score ?? 0),
    })) ?? []

  const tradeCurveSeries =
    runDetail?.trades.reduce<Array<{ trade: number; cumulative: number; pnl: number }>>(
      (series, trade, index) => {
        const previous = index === 0 ? 0 : series[index - 1].cumulative
        const pnl = Number(trade.pnl_usd ?? 0)
        series.push({
          trade: index + 1,
          pnl,
          cumulative: previous + pnl,
        })
        return series
      },
      [],
    ) ?? []

  const strategySelectionSeries = runDetail
    ? Object.entries(
        runDetail.windows.reduce<Record<string, number>>((accumulator, window) => {
          const name = window.metrics.selected_policy_name ?? window.policy_name
          accumulator[name] = (accumulator[name] ?? 0) + 1
          return accumulator
        }, {}),
      ).map(([policy, wins]) => ({ policy, wins }))
    : []

  const recentPromotions = overview?.promotions ?? []

  const activeHistoryRows: Array<HistoryRecord | PromotionRecord> = history
    ? [...history[historyKind]]
    : []
  const filteredHistoryRows = filterRows(activeHistoryRows ?? [], deferredHistoryQuery)

  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? null

  return (
    <div className="app-shell">
      <div className="aurora aurora-left" />
      <div className="aurora aurora-right" />
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">
            <Radar size={26} />
          </div>
          <div>
            <p className="eyebrow">AT Research Control</p>
            <h1>Operator Deck</h1>
          </div>
        </div>

        <nav className="nav-stack">
          {navigation.map((item) => {
            const Icon = item.icon
            const active = item.id === view
            return (
              <button
                key={item.id}
                className={`nav-button ${active ? 'is-active' : ''}`}
                onClick={() => startTransition(() => setView(item.id))}
                type="button"
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="sidebar-card">
          <p className="eyebrow">Design Direction</p>
          <h3>Research first. Execution second.</h3>
          <p>
            The dashboard should make behavior legible before it makes behavior faster. That is the
            design bar for this project.
          </p>
        </div>
      </aside>

      <main className="content">
        <header className="hero">
          <div>
            <p className="eyebrow">Paper Trading Mission Control</p>
            <h2>Monitor the agents, adjust the rules, compare the strategies.</h2>
            <p className="hero-copy">
              Built for research-mode trading: live activity, policy control, walk-forward
              comparisons, and immutable performance history in one place.
            </p>
          </div>
          <div className="hero-actions">
            <button className="ghost-button" onClick={() => void syncCoreState()} type="button">
              <RefreshCw size={16} className={syncing ? 'spin' : ''} />
              Refresh
            </button>
            <div className={`sync-pill ${syncing ? 'is-busy' : ''}`}>
              <Sparkles size={14} />
              {syncing ? 'Syncing' : 'Live'}
            </div>
          </div>
        </header>

        {error ? <div className="banner banner-error">{error}</div> : null}
        {message ? <div className="banner banner-success">{message}</div> : null}

        {loading ? (
          <section className="loading-panel">
            <RefreshCw size={18} className="spin" />
            <span>Loading dashboard state...</span>
          </section>
        ) : null}

        {!loading && view === 'overview' ? (
          <section className="view-grid">
            <div className="metrics-row">
              <MetricCard
                icon={Bot}
                label="Configured Agents"
                value={String(overview?.summary.configured_agents ?? 0)}
                detail="Every configured runtime profile"
              />
              <MetricCard
                icon={Activity}
                label="Healthy Agents"
                value={String(overview?.summary.healthy_agents ?? 0)}
                detail="Latest heartbeat marked healthy"
              />
              <MetricCard
                icon={ShieldCheck}
                label="Recent Decisions"
                value={String(overview?.summary.recent_decisions ?? 0)}
                detail="Most recent logged analyst decisions"
              />
              <MetricCard
                icon={Wallet}
                label="Recent Orders"
                value={String(overview?.summary.recent_orders ?? 0)}
                detail="Submitted broker orders in the recent window"
              />
            </div>

            <Panel
              title="Agent Readiness"
              subtitle="Configured status, runtime health, current symbol, last action, and strategy assignment."
              actionLabel="Manage Agents"
              onAction={() => startTransition(() => setView('agents'))}
            >
              <div className="agent-grid">
                {(overview?.agent_statuses ?? []).map((agent) => (
                  <div key={agent.agent_name} className="agent-card">
                    <div className="agent-card-header">
                      <div>
                        <h3>{agent.agent_name}</h3>
                        <p>{agent.current_symbol ?? agent.symbols?.[0] ?? 'No symbol set'}</p>
                      </div>
                      <StatusPill label={agent.runtime_status ?? agent.configured_status} />
                    </div>
                    <dl className="detail-pairs">
                      <div>
                        <dt>Configured</dt>
                        <dd>{agent.configured_status}</dd>
                      </div>
                      <div>
                        <dt>Strategy</dt>
                        <dd>
                          {agent.strategy_policy_name
                            ? `${agent.strategy_policy_name}@${agent.strategy_version}`
                            : 'Unassigned'}
                        </dd>
                      </div>
                      <div>
                        <dt>Cash</dt>
                        <dd>{formatMoney(agent.cash)}</dd>
                      </div>
                      <div>
                        <dt>Equity</dt>
                        <dd>{formatMoney(agent.equity)}</dd>
                      </div>
                      <div>
                        <dt>Last Action</dt>
                        <dd>{agent.latest_decision_action ?? 'No recent decision'}</dd>
                      </div>
                      <div>
                        <dt>Updated</dt>
                        <dd>{formatDate(agent.latest_decision_at)}</dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>
            </Panel>

            <div className="chart-row">
              <Panel
                title="Outcome Cash Curve"
                subtitle="Cumulative cash delta from recorded trade outcomes."
              >
                <ChartContainer>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={cumulativeOutcomeSeries}>
                      <defs>
                        <linearGradient id="cashCurve" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#29d391" stopOpacity={0.7} />
                          <stop offset="95%" stopColor="#29d391" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                      <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={32} />
                      <YAxis tickLine={false} axisLine={false} />
                      <Tooltip />
                      <Area
                        type="monotone"
                        dataKey="cumulative"
                        stroke="#29d391"
                        strokeWidth={2}
                        fill="url(#cashCurve)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </ChartContainer>
              </Panel>

              <Panel title="Outcome Mix" subtitle="Distribution of recent trade review outcomes.">
                <ChartContainer>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={outcomeMixSeries}>
                      <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                      <XAxis dataKey="outcome" tickLine={false} axisLine={false} />
                      <YAxis tickLine={false} axisLine={false} />
                      <Tooltip />
                      <Bar dataKey="count" fill="#ffb756" radius={[10, 10, 4, 4]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartContainer>
              </Panel>
            </div>

            <div className="chart-row">
              <Panel title="Backtest Score Trend" subtitle="Baseline versus selected candidate over stored runs.">
                <ChartContainer>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={scoreTrendSeries}>
                      <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                      <XAxis dataKey="label" tickLine={false} axisLine={false} />
                      <YAxis tickLine={false} axisLine={false} />
                      <Tooltip />
                      <Line type="monotone" dataKey="baseline" stroke="#89a6ff" strokeWidth={2} dot={false} />
                      <Line type="monotone" dataKey="candidate" stroke="#29d391" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </ChartContainer>
              </Panel>

              <Panel title="Recent Backtest Queue" subtitle="Jobs in the backtest pipeline.">
                <Table
                  columns={['Run', 'Status', 'Symbol', 'Requested', 'Completed']}
                  rows={jobs.slice(0, 6).map((job) => [
                    job.run_name,
                    <StatusPill key={`${job.id}-status`} label={job.status} compact />,
                    `${job.symbol} · ${job.timeframe}`,
                    formatDate(job.requested_at),
                    formatDate(job.completed_at),
                  ])}
                />
              </Panel>
            </div>

            <Panel
              title="Recent Promotions"
              subtitle="Manual strategy promotions with rationale, so configuration changes remain auditable."
            >
              <Table
                columns={['Time', 'Agent', 'From', 'To', 'Reason']}
                rows={recentPromotions.slice(0, 6).map((promotion) => [
                  formatDate(promotion.created_at),
                  promotion.agent_name,
                  promotion.previous_policy_name
                    ? `${promotion.previous_policy_name}@${promotion.previous_policy_version}`
                    : 'Unassigned',
                  `${promotion.new_policy_name ?? 'Unknown'}@${promotion.new_policy_version ?? '—'}`,
                  promotion.rationale,
                ])}
              />
            </Panel>
          </section>
        ) : null}

        {!loading && view === 'agents' ? (
          <section className="split-view">
            <Panel
              title="Agent Registry"
              subtitle="Separate runtime profiles, status controls, and risk envelopes."
            >
              <div className="selector-row">
                <button
                  className={`selector-chip ${selectedAgentName === 'create' ? 'is-selected' : ''}`}
                  onClick={() => {
                    setSelectedAgentName('create')
                    setAgentDraft(blankAgent())
                  }}
                  type="button"
                >
                  New Agent
                </button>
                {agents.map((agent) => (
                  <button
                    key={agent.agent_name}
                    className={`selector-chip ${selectedAgentName === agent.agent_name ? 'is-selected' : ''}`}
                    onClick={() => {
                      setSelectedAgentName(agent.agent_name)
                      setAgentDraft(toAgentForm(agent))
                    }}
                    type="button"
                  >
                    {agent.agent_name}
                  </button>
                ))}
              </div>

              <div className="agent-grid">
                {agents.map((agent) => (
                  <button
                    key={agent.agent_name}
                    className="list-card"
                    onClick={() => {
                      setSelectedAgentName(agent.agent_name)
                      setAgentDraft(toAgentForm(agent))
                    }}
                    type="button"
                  >
                    <div className="list-card-top">
                      <strong>{agent.agent_name}</strong>
                      <StatusPill label={agent.status} />
                    </div>
                    <p>{agent.symbols[0]}</p>
                    <small>
                      Risk {formatSigned(agent.max_risk_per_trade_pct * 100, '%')} ·{' '}
                      {agent.enable_agent_orders ? 'Orders enabled' : 'Orders blocked'}
                    </small>
                  </button>
                ))}
              </div>
            </Panel>

            <Panel
              title={selectedAgentName === 'create' ? 'Create Agent' : `Edit ${selectedAgentName}`}
              subtitle="One agent per symbol is the recommended operating model at this stage."
            >
              <form className="form-grid" onSubmit={(event) => void handleAgentSubmit(event)}>
                <Field label="Agent Name">
                  <input
                    value={agentDraft.agent_name}
                    onChange={(event) => setAgentDraft({ ...agentDraft, agent_name: event.target.value })}
                    placeholder="eth-primary"
                  />
                </Field>
                <Field label="Description">
                  <input
                    value={agentDraft.description ?? ''}
                    onChange={(event) =>
                      setAgentDraft({ ...agentDraft, description: event.target.value })
                    }
                    placeholder="Momentum paper trader for ETH/USD"
                  />
                </Field>
                <Field label="Status">
                  <select
                    value={agentDraft.status}
                    onChange={(event) =>
                      setAgentDraft({ ...agentDraft, status: event.target.value as AgentConfig['status'] })
                    }
                  >
                    <option value="active">active</option>
                    <option value="paused">paused</option>
                    <option value="shadow">shadow</option>
                    <option value="stopped">stopped</option>
                  </select>
                </Field>
                <Field label="Symbol">
                  <input
                    value={agentDraft.symbols[0] ?? ''}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        symbols: [event.target.value],
                      })
                    }
                    placeholder="ETH/USD"
                  />
                </Field>
                <Field label="Strategy">
                  <select
                    value={agentDraft.strategy_policy_version_id ?? ''}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        strategy_policy_version_id: event.target.value || null,
                      })
                    }
                  >
                    <option value="">Unassigned</option>
                    {policies.map((policy) => (
                      <option key={policy.id} value={policy.id}>
                        {labelForPolicy(policy)} [{policy.status}]
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Decision Interval (sec)">
                  <input
                    type="number"
                    value={agentDraft.decision_interval_seconds}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        decision_interval_seconds: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Max Trades / Hour">
                  <input
                    type="number"
                    value={agentDraft.max_trades_per_hour}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        max_trades_per_hour: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Risk / Trade">
                  <input
                    type="number"
                    step="0.001"
                    value={agentDraft.max_risk_per_trade_pct}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        max_risk_per_trade_pct: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Max Daily Loss">
                  <input
                    type="number"
                    step="0.001"
                    value={agentDraft.max_daily_loss_pct}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        max_daily_loss_pct: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Max Position Notional">
                  <input
                    type="number"
                    value={agentDraft.max_position_notional_usd}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        max_position_notional_usd: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Max Spread Bps">
                  <input
                    type="number"
                    value={agentDraft.max_spread_bps}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        max_spread_bps: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Min Confidence">
                  <input
                    type="number"
                    step="0.01"
                    value={agentDraft.min_decision_confidence}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        min_decision_confidence: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Cooldown Seconds">
                  <input
                    type="number"
                    value={agentDraft.cooldown_seconds_after_trade}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        cooldown_seconds_after_trade: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Notes" wide>
                  <textarea
                    value={agentDraft.notes ?? ''}
                    onChange={(event) => setAgentDraft({ ...agentDraft, notes: event.target.value })}
                    placeholder="Operational notes, market regime notes, or why this agent exists."
                  />
                </Field>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={agentDraft.enable_agent_orders}
                    onChange={(event) =>
                      setAgentDraft({
                        ...agentDraft,
                        enable_agent_orders: event.target.checked,
                      })
                    }
                  />
                  <span>Allow paper order submission for this agent</span>
                </label>
                <div className="form-actions">
                  <button className="primary-button" disabled={savingAgent} type="submit">
                    <Save size={16} />
                    {savingAgent ? 'Saving...' : 'Save Agent'}
                  </button>
                </div>
              </form>
            </Panel>
          </section>
        ) : null}

        {!loading && view === 'strategies' ? (
          <section className="split-view">
            <Panel
              title="Strategy Registry"
              subtitle="Keep policy versions explicit. You are comparing hypotheses, not just numbers."
            >
              <div className="strategy-list">
                {policies.map((policy) => (
                  <article key={policy.id} className="strategy-card">
                    <div className="list-card-top">
                      <strong>{labelForPolicy(policy)}</strong>
                      <StatusPill label={policy.status} />
                    </div>
                    <p>{policy.notes || 'No notes recorded.'}</p>
                    <div className="meta-line">
                      <span>Entry 3bps {policy.thresholds.entry_momentum_3_bps ?? '—'}</span>
                      <span>Exit 3bps {policy.thresholds.exit_momentum_3_bps ?? '—'}</span>
                    </div>
                  </article>
                ))}
              </div>
            </Panel>

            <Panel title="New Strategy Version" subtitle="Add variants you want to compare in walk-forward tests.">
              <form className="form-grid" onSubmit={(event) => void handlePolicySubmit(event)}>
                <Field label="Policy Name">
                  <input
                    value={policyDraft.policy_name}
                    onChange={(event) =>
                      setPolicyDraft({ ...policyDraft, policy_name: event.target.value })
                    }
                    placeholder="conservative"
                  />
                </Field>
                <Field label="Version">
                  <input
                    value={policyDraft.version}
                    onChange={(event) => setPolicyDraft({ ...policyDraft, version: event.target.value })}
                    placeholder="v2"
                  />
                </Field>
                <Field label="Status">
                  <select
                    value={policyDraft.status}
                    onChange={(event) => setPolicyDraft({ ...policyDraft, status: event.target.value })}
                  >
                    <option value="candidate">candidate</option>
                    <option value="baseline">baseline</option>
                    <option value="shadow">shadow</option>
                    <option value="active">active</option>
                    <option value="retired">retired</option>
                    <option value="rejected">rejected</option>
                  </select>
                </Field>
                <Field label="Entry Momentum 3">
                  <input
                    type="number"
                    value={policyDraft.thresholds.entry_momentum_3_bps}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        thresholds: {
                          ...policyDraft.thresholds,
                          entry_momentum_3_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Entry Momentum 5">
                  <input
                    type="number"
                    value={policyDraft.thresholds.entry_momentum_5_bps}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        thresholds: {
                          ...policyDraft.thresholds,
                          entry_momentum_5_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Exit Momentum 3">
                  <input
                    type="number"
                    value={policyDraft.thresholds.exit_momentum_3_bps}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        thresholds: {
                          ...policyDraft.thresholds,
                          exit_momentum_3_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Exit Momentum 5">
                  <input
                    type="number"
                    value={policyDraft.thresholds.exit_momentum_5_bps}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        thresholds: {
                          ...policyDraft.thresholds,
                          exit_momentum_5_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Max Spread Bps">
                  <input
                    type="number"
                    value={policyDraft.thresholds.max_spread_bps}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        thresholds: {
                          ...policyDraft.thresholds,
                          max_spread_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Max Volatility 5">
                  <input
                    type="number"
                    value={Number(policyDraft.strategy_config.max_volatility_5_bps ?? 25)}
                    onChange={(event) =>
                      setPolicyDraft({
                        ...policyDraft,
                        strategy_config: {
                          ...policyDraft.strategy_config,
                          max_volatility_5_bps: Number(event.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="Notes" wide>
                  <textarea
                    value={policyDraft.notes}
                    onChange={(event) => setPolicyDraft({ ...policyDraft, notes: event.target.value })}
                    placeholder="What is this strategy trying to prove?"
                  />
                </Field>
                <div className="form-actions">
                  <button className="primary-button" disabled={savingPolicy} type="submit">
                    <Save size={16} />
                    {savingPolicy ? 'Saving...' : 'Save Strategy'}
                  </button>
                </div>
              </form>
            </Panel>
          </section>
        ) : null}

        {!loading && view === 'backtests' ? (
          <section className="backtest-layout">
            <Panel
              title="Backtest Studio"
              subtitle="Queue walk-forward jobs, compare the latest run, and inspect the trade path."
            >
              <form className="form-grid" onSubmit={(event) => void handleBacktestSubmit(event)}>
                <Field label="Run Name">
                  <input
                    value={backtestDraft.run_name}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, run_name: event.target.value })
                    }
                  />
                </Field>
                <Field label="Agent">
                  <select
                    value={backtestDraft.agent_config_id ?? ''}
                    onChange={(event) =>
                      setBacktestDraft({
                        ...backtestDraft,
                        agent_config_id: event.target.value || null,
                      })
                    }
                  >
                    <option value="">Use current default agent</option>
                    {agents.map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.agent_name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Symbol">
                  <input
                    value={backtestDraft.symbol}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, symbol: event.target.value })
                    }
                  />
                </Field>
                <Field label="Timeframe">
                  <input
                    value={backtestDraft.timeframe}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, timeframe: event.target.value })
                    }
                  />
                </Field>
                <Field label="Baseline Strategy">
                  <select
                    value={backtestDraft.baseline_policy_version_id}
                    onChange={(event) =>
                      setBacktestDraft({
                        ...backtestDraft,
                        baseline_policy_version_id: event.target.value,
                        candidate_policy_version_ids: backtestDraft.candidate_policy_version_ids.filter(
                          (policyId) => policyId !== event.target.value,
                        ),
                      })
                    }
                  >
                    {policies.map((policy) => (
                      <option key={policy.id} value={policy.id}>
                        {labelForPolicy(policy)} [{policy.status}]
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Candidate Strategies" wide>
                  <div className="candidate-grid">
                    {policies
                      .filter((policy) => policy.id !== backtestDraft.baseline_policy_version_id)
                      .map((policy) => {
                        const checked = backtestDraft.candidate_policy_version_ids.includes(policy.id)
                        return (
                          <label key={policy.id} className={`candidate-card ${checked ? 'is-checked' : ''}`}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                setBacktestDraft((current) => ({
                                  ...current,
                                  candidate_policy_version_ids: event.target.checked
                                    ? [...current.candidate_policy_version_ids, policy.id]
                                    : current.candidate_policy_version_ids.filter(
                                        (candidateId) => candidateId !== policy.id,
                                      ),
                                }))
                              }}
                            />
                            <span>{labelForPolicy(policy)}</span>
                            <small>{policy.status}</small>
                          </label>
                        )
                      })}
                  </div>
                </Field>
                <Field label="Lookback Days">
                  <input
                    type="number"
                    value={backtestDraft.lookback_days}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, lookback_days: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field label="Train Window">
                  <input
                    type="number"
                    value={backtestDraft.train_window_days}
                    onChange={(event) =>
                      setBacktestDraft({
                        ...backtestDraft,
                        train_window_days: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Test Window">
                  <input
                    type="number"
                    value={backtestDraft.test_window_days}
                    onChange={(event) =>
                      setBacktestDraft({
                        ...backtestDraft,
                        test_window_days: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Step Days">
                  <input
                    type="number"
                    value={backtestDraft.step_days}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, step_days: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field label="Warmup Bars">
                  <input
                    type="number"
                    value={backtestDraft.warmup_bars}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, warmup_bars: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field label="Starting Cash">
                  <input
                    type="number"
                    value={backtestDraft.starting_cash_usd}
                    onChange={(event) =>
                      setBacktestDraft({
                        ...backtestDraft,
                        starting_cash_usd: Number(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Notes" wide>
                  <textarea
                    value={backtestDraft.notes}
                    onChange={(event) =>
                      setBacktestDraft({ ...backtestDraft, notes: event.target.value })
                    }
                    placeholder="Market regime, objective, or comparison note."
                  />
                </Field>
                <div className="form-actions">
                  <button
                    className="primary-button"
                    disabled={runningBacktest || backtestDraft.candidate_policy_version_ids.length === 0}
                    type="submit"
                  >
                    <Play size={16} />
                    {runningBacktest ? 'Queueing...' : 'Queue Backtest'}
                  </button>
                </div>
              </form>
            </Panel>

            <div className="stacked-panels">
              <Panel title="Backtest Jobs" subtitle="Queued, running, completed, and failed jobs.">
                <Table
                  columns={['Run', 'Status', 'Symbol', 'Requested']}
                  rows={jobs.slice(0, 7).map((job) => [
                    job.run_name,
                    <StatusPill key={`${job.id}-job`} label={job.status} compact />,
                    `${job.symbol} · ${job.timeframe}`,
                    formatDate(job.requested_at),
                  ])}
                />
              </Panel>

              <Panel title="Stored Runs" subtitle="Select one run to inspect in detail.">
                <div className="selector-row">
                  {runs.slice(0, 8).map((run) => (
                    <button
                      key={run.id}
                      className={`selector-chip ${selectedRunId === run.id ? 'is-selected' : ''}`}
                      onClick={() => setSelectedRunId(run.id)}
                      type="button"
                    >
                      {run.run_name}
                    </button>
                  ))}
                </div>
                {selectedRun ? (
                  <div className="run-headline">
                    <div>
                      <p className="eyebrow">Selected Run</p>
                      <h3>{selectedRun.run_name}</h3>
                    </div>
                    <StatusPill label={selectedRun.decision_payload?.status ?? selectedRun.status} />
                  </div>
                ) : null}
              </Panel>
            </div>

            {runDetail ? (
              <>
                <div className="metrics-row">
                  <MetricCard
                    icon={ArrowUpRight}
                    label="Baseline Score"
                    value={formatSigned(runDetail.run.baseline_metrics?.score)}
                    detail={formatSigned(runDetail.run.baseline_metrics?.realized_pnl_bps, ' bps')}
                  />
                  <MetricCard
                    icon={Sparkles}
                    label="Candidate Score"
                    value={formatSigned(runDetail.run.candidate_metrics?.score)}
                    detail={formatSigned(runDetail.run.candidate_metrics?.realized_pnl_bps, ' bps')}
                  />
                  <MetricCard
                    icon={ShieldCheck}
                    label="Decision"
                    value={String(runDetail.run.decision_payload?.status ?? 'n/a').replace('_', ' ')}
                    detail={runDetail.run.decision_payload?.reason ?? 'No decision rationale recorded.'}
                  />
                  <MetricCard
                    icon={TimerReset}
                    label="Trades"
                    value={formatCompact(runDetail.trades.length)}
                    detail={`Bars ${formatCompact(runDetail.run.total_bars)}`}
                  />
                </div>

                <div className="chart-row">
                  <Panel title="Baseline vs Candidate" subtitle="Core metrics for the selected run.">
                    <ChartContainer>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={comparisonSeries}>
                          <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                          <XAxis dataKey="metric" tickLine={false} axisLine={false} />
                          <YAxis tickLine={false} axisLine={false} />
                          <Tooltip />
                          <Bar dataKey="baseline" fill="#89a6ff" radius={[8, 8, 0, 0]} />
                          <Bar dataKey="candidate" fill="#29d391" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </ChartContainer>
                  </Panel>

                  <Panel title="Window Score Drift" subtitle="Score by walk-forward window.">
                    <ChartContainer>
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={windowScoreSeries}>
                          <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                          <XAxis dataKey="label" tickLine={false} axisLine={false} />
                          <YAxis tickLine={false} axisLine={false} />
                          <Tooltip />
                          <Line type="monotone" dataKey="baseline" stroke="#89a6ff" strokeWidth={2} />
                          <Line type="monotone" dataKey="selected" stroke="#29d391" strokeWidth={2} />
                        </LineChart>
                      </ResponsiveContainer>
                    </ChartContainer>
                  </Panel>
                </div>

                <div className="chart-row">
                  <Panel
                    title="Selection Pressure"
                    subtitle="How often each concrete strategy won a walk-forward window in this run."
                  >
                    <ChartContainer>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={strategySelectionSeries}>
                          <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                          <XAxis dataKey="policy" tickLine={false} axisLine={false} />
                          <YAxis tickLine={false} axisLine={false} allowDecimals={false} />
                          <Tooltip />
                          <Bar dataKey="wins" fill="#d78bff" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </ChartContainer>
                  </Panel>

                  <Panel
                    title="Manual Promotion"
                    subtitle="Promotion is manual by design. Choose the target agent, choose the strategy, and leave a rationale."
                  >
                    <form className="form-grid" onSubmit={(event) => void handlePromotionSubmit(event)}>
                      <Field label="Target Agent">
                        <select
                          value={promotionDraft.agent_config_id}
                          onChange={(event) =>
                            setPromotionDraft({
                              ...promotionDraft,
                              agent_config_id: event.target.value,
                            })
                          }
                        >
                          <option value="">Select an agent</option>
                          {agents.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                              {agent.agent_name} · {agent.symbols[0]}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Strategy To Promote">
                        <select
                          value={promotionDraft.new_policy_version_id}
                          onChange={(event) =>
                            setPromotionDraft({
                              ...promotionDraft,
                              new_policy_version_id: event.target.value,
                            })
                          }
                        >
                          {policies.map((policy) => (
                            <option key={policy.id} value={policy.id}>
                              {labelForPolicy(policy)} [{policy.status}]
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Current Assignment" wide>
                        <div className="inline-note">
                          {resolvePolicyName(
                            agents.find((agent) => agent.id === promotionDraft.agent_config_id)
                              ?.strategy_policy_version_id,
                            policies,
                          )}
                        </div>
                      </Field>
                      <Field label="Promotion Rationale" wide>
                        <textarea
                          value={promotionDraft.rationale}
                          onChange={(event) =>
                            setPromotionDraft({
                              ...promotionDraft,
                              rationale: event.target.value,
                            })
                          }
                          placeholder="Why is this strategy good enough to become the assigned policy for this agent?"
                        />
                      </Field>
                      <div className="form-actions">
                        <button className="primary-button" disabled={promotingStrategy} type="submit">
                          <ShieldCheck size={16} />
                          {promotingStrategy ? 'Promoting...' : 'Promote Strategy'}
                        </button>
                      </div>
                    </form>
                  </Panel>
                </div>

                <Panel title="Trade PnL Path" subtitle="Cumulative realized PnL across replayed trades.">
                  <ChartContainer>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={tradeCurveSeries}>
                        <defs>
                          <linearGradient id="tradePnl" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#ffb756" stopOpacity={0.5} />
                            <stop offset="95%" stopColor="#ffb756" stopOpacity={0.02} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                        <XAxis dataKey="trade" tickLine={false} axisLine={false} />
                        <YAxis tickLine={false} axisLine={false} />
                        <Tooltip />
                        <Area
                          type="monotone"
                          dataKey="cumulative"
                          stroke="#ffb756"
                          fill="url(#tradePnl)"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </ChartContainer>
                </Panel>
              </>
            ) : null}
          </section>
        ) : null}

        {!loading && view === 'history' ? (
          <section className="history-layout">
            <Panel
              title="Decision And Trade History"
              subtitle="Immutable audit data. Review, filter, and compare; do not rewrite what happened."
            >
              <div className="history-toolbar">
                <select
                  value={historyAgentName}
                  onChange={(event) => setHistoryAgentName(event.target.value)}
                >
                  <option value="all">All agents</option>
                  {agents.map((agent) => (
                    <option key={agent.agent_name} value={agent.agent_name}>
                      {agent.agent_name}
                    </option>
                  ))}
                </select>
                <input
                  value={historyQuery}
                  onChange={(event) => setHistoryQuery(event.target.value)}
                  placeholder="Search decisions, outcomes, reasons, symbols..."
                />
                <button className="ghost-button" onClick={() => void syncHistoryState(historyAgentName === 'all' ? undefined : historyAgentName)} type="button">
                  <RefreshCw size={16} />
                  Reload
                </button>
              </div>

              <div className="selector-row">
                {(['decisions', 'orders', 'outcomes', 'lessons', 'promotions'] as const).map((kind) => (
                  <button
                    key={kind}
                    className={`selector-chip ${historyKind === kind ? 'is-selected' : ''}`}
                    onClick={() => setHistoryKind(kind)}
                    type="button"
                  >
                    {kind}
                  </button>
                ))}
              </div>

              <Table
                columns={historyColumnsForKind(historyKind)}
                rows={filteredHistoryRows.slice(0, 120).map((row) => historyCellsForKind(historyKind, row))}
              />
            </Panel>
          </section>
        ) : null}
      </main>
    </div>
  )
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Gauge
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="metric-card">
      <div className="metric-icon">
        <Icon size={18} />
      </div>
      <div>
        <p className="metric-label">{label}</p>
        <strong className="metric-value">{value}</strong>
        <p className="metric-detail">{detail}</p>
      </div>
    </div>
  )
}

function Panel({
  title,
  subtitle,
  children,
  actionLabel,
  onAction,
}: {
  title: string
  subtitle: string
  children: ReactNode
  actionLabel?: string
  onAction?: () => void
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">{title}</p>
          <h3>{subtitle}</h3>
        </div>
        {actionLabel && onAction ? (
          <button className="ghost-button" onClick={onAction} type="button">
            {actionLabel}
          </button>
        ) : null}
      </div>
      {children}
    </section>
  )
}

function StatusPill({ label, compact = false }: { label: string; compact?: boolean }) {
  return (
    <span className={`status-pill tone-${normalizeStatusTone(label)} ${compact ? 'is-compact' : ''}`}>
      {label}
    </span>
  )
}

function Field({
  label,
  children,
  wide = false,
}: {
  label: string
  children: ReactNode
  wide?: boolean
}) {
  return (
    <label className={`field ${wide ? 'field-wide' : ''}`}>
      <span>{label}</span>
      {children}
    </label>
  )
}

function ChartContainer({ children }: { children: ReactNode }) {
  return <div className="chart-shell">{children}</div>
}

function Table({
  columns,
  rows,
}: {
  columns: string[]
  rows: ReactNode[][]
}) {
  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="empty-cell" colSpan={columns.length}>
                No records to show yet.
              </td>
            </tr>
          ) : (
            rows.map((row, rowIndex) => (
              <tr key={`row-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`cell-${rowIndex}-${cellIndex}`}>{cell}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

function historyColumnsForKind(kind: 'decisions' | 'orders' | 'outcomes' | 'lessons' | 'promotions') {
  if (kind === 'decisions') {
    return ['Time', 'Agent', 'Symbol', 'Action', 'Confidence', 'Risk', 'Reason']
  }
  if (kind === 'orders') {
    return ['Time', 'Agent', 'Symbol', 'Side', 'Status', 'Requested', 'Filled']
  }
  if (kind === 'outcomes') {
    return ['Time', 'Agent', 'Outcome', 'Summary', 'Cash Delta', 'Position Delta']
  }
  if (kind === 'promotions') {
    return ['Time', 'Agent', 'From', 'To', 'Run', 'Rationale']
  }
  return ['Category', 'Message', 'Confidence', 'Source', 'Status', 'Last Seen']
}

function historyCellsForKind(
  kind: 'decisions' | 'orders' | 'outcomes' | 'lessons' | 'promotions',
  row: HistoryRecord | PromotionRecord,
) {
  if (kind === 'decisions') {
    const historyRow = row as HistoryRecord
    return [
      formatDate(historyRow.recorded_at),
      String(historyRow.agent_name ?? '—'),
      String(historyRow.symbol ?? '—'),
      String(historyRow.action ?? '—'),
      formatSigned(Number(historyRow.decision_confidence ?? 0) * 100, '%'),
      historyRow.risk_approved ? 'approved' : 'rejected',
      String(historyRow.risk_reason ?? historyRow.rationale ?? '—'),
    ]
  }
  if (kind === 'orders') {
    const historyRow = row as HistoryRecord
    return [
      formatDate(historyRow.created_at),
      String(historyRow.agent_name ?? '—'),
      String(historyRow.symbol ?? '—'),
      String(historyRow.side ?? '—'),
      String(historyRow.status ?? '—'),
      formatMoney(historyRow.requested_notional),
      formatMoney(historyRow.filled_avg_price),
    ]
  }
  if (kind === 'outcomes') {
    const historyRow = row as HistoryRecord
    return [
      formatDate(historyRow.recorded_at),
      String(historyRow.agent_name ?? '—'),
      String(historyRow.outcome ?? '—'),
      String(historyRow.summary ?? '—'),
      formatMoney(historyRow.cash_delta),
      formatSigned(historyRow.position_qty_delta),
    ]
  }
  if (kind === 'promotions') {
    const promotion = row as PromotionRecord
    return [
      formatDate(promotion.created_at),
      promotion.agent_name ?? '—',
      promotion.previous_policy_name
        ? `${promotion.previous_policy_name}@${promotion.previous_policy_version}`
        : 'Unassigned',
      `${promotion.new_policy_name ?? 'Unknown'}@${promotion.new_policy_version ?? '—'}`,
      promotion.source_run_name ?? 'Manual change',
      promotion.rationale,
    ]
  }
  const historyRow = row as HistoryRecord
  return [
    String(historyRow.category ?? '—'),
    String(historyRow.message ?? '—'),
    formatSigned(Number(historyRow.confidence ?? 0) * 100, '%'),
    String(historyRow.source ?? '—'),
    String(historyRow.status ?? '—'),
    formatDate(historyRow.last_seen_at),
  ]
}

export default App
