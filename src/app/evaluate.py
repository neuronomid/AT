from pathlib import Path

from agents.analyst import AnalystAgent
from app.config import get_settings
from data.schemas import EvaluationReport
from evaluation.challenger import Challenger
from evaluation.replay import ReplayEngine
from infra.logging import configure_logging, get_logger
from memory.journal import Journal


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    journal = Journal(settings.journal_path)
    records = journal.read_all()

    baseline_policy = AnalystAgent(policy_name="baseline")
    challenger_policy = AnalystAgent(
        policy_name="challenger",
        max_spread_bps=18.0,
        exit_momentum_3_bps=-6.0,
        exit_momentum_5_bps=-10.0,
        entry_momentum_3_bps=6.0,
        entry_momentum_5_bps=10.0,
        max_volatility_5_bps=35.0,
    )

    replay_engine = ReplayEngine()
    baseline_metrics = replay_engine.run(records, baseline_policy)
    candidate_metrics = replay_engine.run(records, challenger_policy)

    challenger = Challenger(
        min_closed_trades=settings.evaluation_min_closed_trades,
        min_score_improvement=settings.evaluation_min_score_improvement,
        max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
    )
    decision = challenger.compare(baseline_metrics, candidate_metrics)
    report = EvaluationReport(
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        decision=decision,
    )

    output_path = Path(settings.evaluation_report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        "evaluation_report_written path=%s baseline_score=%.2f candidate_score=%.2f decision=%s",
        output_path,
        report.baseline.score,
        report.candidate.score,
        report.decision.status,
    )
    print(report.model_dump_json(indent=2))
