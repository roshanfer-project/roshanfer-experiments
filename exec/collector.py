"""Collector module.

Scaffolding to collect metrics after a RunUnit execution. The design goals:
1. Parameterized entirely by Config (no hard‑coded IPs / queries here).
2. Append-only: every collect writes new files; never overwrites previous runs.
3. Clear placeholders for you to implement domain-specific reliability / retry logic.

High-level flow in collect():
  a. Derive precise time window from run_result (fallback to now if missing)
  b. Execute configured Prometheus queries (if client available)
  c. Persist each raw query result as JSON (metrics/<query_name>.json)
  d. Build a lightweight summary index (metrics/_index.json)
  e. (Placeholder) Perform health / anomaly checks; decide if rerun needed

To implement later (see TODO markers):
  - query window adjustments (add offset / grace periods)
  - anomaly detection (missing series, NaNs, zero traffic, high error ratio, etc.)
  - retry signaling (return a flag or raise custom exception)
  - additional sinks (CSV, Parquet) if needed
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any
import json
import math
from .utils import format_query

from .config import Config
from .models import RunUnit, RunResult, CollectorResult

try:  # Optional dependency already listed in requirements.txt
	from prometheus_api_client import PrometheusConnect  # type: ignore
except Exception:  # noqa: BLE001
	PrometheusConnect = None  # type: ignore


class Collector:
	"""Collects metrics for a completed run.

	Attributes:
		config: Global configuration.
		prom: Lazy / best-effort Prometheus client (None if unavailable).
		queries: Dict of metric_name -> PromQL (sourced from config.extra['metrics'] or defaults).
	"""

	DEFAULT_QUERIES = {}

	def __init__(self, config: Config):
		self.config = config
		self.prom = None
		if PrometheusConnect is not None:
			try:
				self.prom = PrometheusConnect(url=config.prometheus_url, disable_ssl=True)
			except Exception:  # noqa: BLE001
				self.prom = None
		# Global fallback metrics (deprecated in favor of experiment_metrics)
		metrics_cfg = config.metrics if isinstance(config.metrics, dict) else {}
		self.global_queries: Dict[str, str] = {**self.DEFAULT_QUERIES, **metrics_cfg}

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------
	def collect(self, unit: RunUnit, run_result: RunResult, unit_dir: Path) -> CollectorResult:
		"""Collect metrics for a single run unit.

		Returns a CollectorResult listing produced metric files.
		Does NOT raise on query failures; instead logs placeholder error files so later aggregation can proceed.
		"""
		metrics_dir = unit_dir / self.config.metrics_subdir
		metrics_dir.mkdir(parents=True, exist_ok=True)

		# Derive time window
		start_ts = run_result.start_timestamp
		end_ts = run_result.end_timestamp
		# Preserve original for queries; create JSON-safe forms for index
		start_ts_json = start_ts.isoformat() if hasattr(start_ts, "isoformat") else start_ts
		end_ts_json = end_ts.isoformat() if hasattr(end_ts, "isoformat") else end_ts

		index: Dict[str, Any] = {
			"unit_name": unit.name,
			"time_window": {
				"start_timestamp": start_ts_json,
				"end_timestamp": end_ts_json,
				"duration_sec": unit.duration,
			},
			"queries": {},
			"anomalies": [],  # To be filled by user logic
			"notes": "",
			"step": unit.collector_step,
			"range": unit.collector_range,
		}

		metric_files: List[str] = []

		# only check health for our system
		if unit.system == "sidecar" or unit.system == "sidecar-queue":
			self._evaluate_health(unit)

		# Select per-experiment metrics if configured
		if unit.type in self.config.experiment_metrics:
			self.queries = self.config.experiment_metrics[unit.type]
		else:
			self.queries = self.global_queries
		
		# populate queries
		for api in unit.apis:
			values = {
				"api": api,
				"rate_interval": unit.collector_range
			}
			if len(unit.services) == 0:
				unit.services.append("all")
			
			for service in unit.services:
				values["service"] = service
				for query_name, query in self.queries.items():
					formatted_query = format_query(query, values, strict=True)
					name = f"{query_name}_{api}_{service}"

					if self.prom is None:
						placeholder = metrics_dir / "PROMETHEUS_UNAVAILABLE.txt"
						placeholder.write_text("Prometheus client not initialized or connection failed.")
						metric_files.append(str(placeholder))
					else:
						file_path = metrics_dir / f"{name}.json"
						try:
							result = self._query_range(formatted_query, start_ts, end_ts, step=unit.collector_step)
							file_path.write_text(json.dumps({
								"query": formatted_query,
								"result": result,
							}, indent=2))
							metric_files.append(str(file_path))
							index["queries"][name] = "success"
						except Exception as e:  # noqa: BLE001
							err_path = metrics_dir / f"{name}_error.txt"
							err_path.write_text(str(e.__repr__()))
							metric_files.append(str(err_path))
							index["queries"][name] = {"error": str(e.__repr__()), "query": formatted_query}

		# Persist index file AFTER queries so partial failure still yields index.
		(metrics_dir / "_index.json").write_text(json.dumps(index, indent=2))

		return CollectorResult(
			unit_name=unit.name,
			metrics_dir=str(metrics_dir),
			metrics_files=metric_files,
			notes="",  # TODO: fill with anomaly summary / retry info
		)

	# ------------------------------------------------------------------
	# Helper / internal
	# ------------------------------------------------------------------
	def _query_range(self, query: str, start: datetime, end: datetime, step: str = "5s") -> Any:
		if self.prom is None:
			raise RuntimeError("Prometheus client unavailable")
		# prometheus_api_client expects naive datetimes in UTC or timezone-aware? It accepts datetime objects.
		return self.prom.custom_query_range(query=query, start_time=start, end_time=end, step=step)

	# ------------------------------------------------------------------
	# Placeholders for user-implemented logic
	# ------------------------------------------------------------------
	def _evaluate_health(self, unit: RunUnit) -> bool:
		"""Placeholder health/anomaly evaluation.

		Implement your domain-specific checks here. Return a list of anomaly descriptions.
		Example checks you might add:
		  - Required query missing or returned zero series
		  - Latency percentiles above threshold
		  - Request rate below expected minimum
		  - Error ratio above threshold
		Based on anomalies you can decide to trigger a retry by raising a custom exception.
		"""
		
		query = "ppm_k6_goodput_counter_total"
		result = self.prom.custom_query(query)

		if len(result) == 0:
			raise Exception("No results found for goodput check")

		value = result[0]["value"][-1]
		total_duration = unit.duration + 5
		if int(value) < unit.base * total_duration * 9.5:
			raise Exception(f"Goodput {value} below expected threshold {unit.base * total_duration * 9.5}")


		fail_other_query = "ppm_k6_fail_other_counter_total"
		result = self.prom.custom_query(fail_other_query)

		if len(result) != 0:
			raise Exception(f"fail other is not empty, result: {result}")
