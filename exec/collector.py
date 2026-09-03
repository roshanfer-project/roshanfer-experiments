"""Collector module.

Responsibilities:
1. Collect Generic Service Logs (via collect_logs.sh).
2. Parse RWG Output (CSV -> JSON) locally (using rwg binary).
3. Organize Metrics for Plotting (ensure JSONs in metrics/).
"""

from __future__ import annotations

import csv
import json
import subprocess
import shutil
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from .config import Config
from .models import RunUnit, RunResult, CollectorResult, is_sidecar_family
from .plots.plugins.lb_avg_queue_unit import (
    _entry_from_callgraph,
    _match_service,
)
import urllib.request
import urllib.error

INGRESS_MEAN_ONLY_SYSTEMS = ("p2c", "wrr", "amphiqueue", "amphiqueue-fcfs", "amphiqueue-edf")
INGRESS_MEAN_AND_MAX_SYSTEMS = ("plain", "roshanfer", "rajomon", "dagor")


def _frontend_replica_keys(api_data: Dict[str, Any], entry_ms: str | None) -> List[str]:
    if entry_ms:
        keys = [k for k in api_data if _match_service(k, [entry_ms]) == entry_ms]
        if keys:
            return keys
    frontend_keys = [k for k in api_data if _match_service(k, ["frontend"]) == "frontend"]
    nginx_keys = [k for k in api_data if _match_service(k, ["nginx"]) == "nginx"]
    if frontend_keys and nginx_keys:
        return []
    return frontend_keys or nginx_keys

class Collector:
    def __init__(self, config: Config):
        self.config = config

    def collect(self, unit: RunUnit, run_result: RunResult, unit_dir: Path, collect_service_logs: bool = True) -> CollectorResult:
        output_dir = unit_dir / "output"
        raw_dir = unit_dir / self.config.raw_artifact_subdir
        metrics_dir = unit_dir / self.config.metrics_subdir
        
        metrics_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        index: Dict[str, Any] = {
            "unit_name": unit.name,
            "apis": unit.apis,
            "reports": {},
            "notes": "",
        }
        metric_files: List[str] = []

        # 1. Collect Service Logs (Optional)
        if collect_service_logs:
             self._collect_service_logs(unit, raw_dir, metrics_dir)
             if self.config.nanolog_debug and is_sidecar_family(unit.system):
                 self._nanolog_decompress_and_plot(unit_dir, raw_dir)

        # 2. Generate/Validate JSON Reports from CSV (Local processing)
        # We assume Runner has already pulled out-{api}.csv to output_dir
        self._generate_reports(unit, output_dir, index, metric_files)

        # 3. Collect Prometheus Metrics (Now after reports to use overall-*.json)
        self._collect_prometheus_metrics(unit, metrics_dir, output_dir)

        # 4. Copy/Link Metrics for Plot Runner
        self._copy_metrics_for_plotting(output_dir, metrics_dir)

        # 5. Index Envoy stats CSVs (collect_logs.sh -> metrics/envoy/)
        self._index_envoy_metrics(metrics_dir, index)

        # Persist Index
        (metrics_dir / "_index.json").write_text(json.dumps(index, indent=2))

        return CollectorResult(
            unit_name=unit.name,
            metrics_dir=str(metrics_dir),
            metrics_files=metric_files,
            notes="",
        )

    def _collect_service_logs(self, unit: RunUnit, raw_dir: Path, metrics_dir: Path):
        """Invoke benchmarks/<bench>/collect_logs.sh to gather logs."""
        script_path = Path("benchmarks") / unit.bench / "collect_logs.sh"
        if not script_path.exists():
            logging.warning(f"No collect_logs.sh for {unit.bench}, skipping service log collection.")
            return

        # Pass context
        env = os.environ.copy()
        env["DEPLOYMENT_HOSTS"] = ",".join(unit.deployment_hosts)
        env["OUTPUT_DIR"] = str(raw_dir / "service_logs")
        env["SYSTEM"] = unit.system
        if unit.system == "envoy":
            envoy_metrics = metrics_dir / "envoy"
            envoy_metrics.mkdir(parents=True, exist_ok=True)
            env["ENVOY_METRICS_DIR"] = str(envoy_metrics.resolve())
        if self.config.nanolog_debug and is_sidecar_family(unit.system):
            env["COLLECT_SIDECAR_NANOLOG"] = "1"

        # Create subfolder
        (raw_dir / "service_logs").mkdir(parents=True, exist_ok=True)

        try:
            logging.info(f"Collecting service logs for {unit.bench}...")
            subprocess.run([str(script_path)], env=env, check=False)
        except Exception as e:
            logging.error(f"Failed to collect logs: {e}")

    def _nanolog_decompress_and_plot(self, unit_dir: Path, raw_dir: Path) -> None:
        service_logs = raw_dir / "service_logs"
        if not service_logs.is_dir():
            return
        clogs = sorted(service_logs.glob("*-sidecar.clog"))
        if not clogs:
            logging.info("nanolog_debug: no *-sidecar.clog in service_logs; skip decompress/plot")
            return
        dec = Path("benchmarks/sidecar/external/NanoLog/runtime/decompressor").resolve()
        if not dec.is_file() or not os.access(dec, os.X_OK):
            logging.warning("nanolog_debug: decompressor missing or not executable: %s", dec)
            return
        nanolog_dir = unit_dir / "nanolog"
        nanolog_dir.mkdir(parents=True, exist_ok=True)
        any_log = False
        for clog in clogs:
            out_log = clog.parent / (clog.stem + ".nanolog.log")
            try:
                with open(out_log, "w", encoding="utf-8") as fout:
                    cp = subprocess.run(
                        [str(dec), "decompress", str(clog)],
                        stdout=fout,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=600,
                    )
                if cp.returncode != 0:
                    logging.warning(
                        "nanolog decompress failed %s: %s",
                        clog.name,
                        (cp.stderr or "")[:500],
                    )
                    out_log.unlink(missing_ok=True)
                    continue
                any_log = True
            except (OSError, subprocess.TimeoutExpired) as e:
                logging.warning("nanolog decompress error %s: %s", clog.name, e)
                continue
            try:
                from .nanolog_metrics_plot import generate_nanolog_pdf

                out_pdf = nanolog_dir / f"metrics-{clog.stem}.pdf"
                generate_nanolog_pdf([out_log], out_pdf)
                logging.info("nanolog_debug: wrote %s", out_pdf)
            except Exception as e:
                logging.warning("nanolog_debug: plot failed %s: %s", clog.name, e)
        if not any_log:
            logging.warning("nanolog_debug: no .nanolog.log produced; skip plot")

    def _collect_prometheus_metrics(self, unit: RunUnit, metrics_dir: Path, output_dir: Path):
        """Collects specific Prometheus metrics and saves to json."""
        prom_metrics_file = metrics_dir / "prometheus.json"
        
        try:
            # 1. Get NodePort
            cmd_port = ["kubectl", "get", "svc", "prometheus-external", "-o", "jsonpath='{.spec.ports[0].nodePort}'"]
            res_port = subprocess.run(cmd_port, capture_output=True, text=True)
            if res_port.returncode != 0:
                logging.warning("Could not find prometheus-external service. Skipping Prometheus collection.")
                prom_metrics_file.write_text("{}")
                return
            
            node_port = res_port.stdout.strip("'")
            if not node_port.isdigit():
                 # Handle cases where output might be quoted or empty
                 node_port = node_port.replace("'", "")
            
            if not node_port or not node_port.isdigit():
                logging.warning(f"Invalid NodePort found: {node_port}. Skipping.")
                prom_metrics_file.write_text("{}")
                return

            # 2. Get Node IP (any node)
            if not unit.deployment_hosts:
                logging.warning("No deployment hosts found. Skipping Prometheus collection.")
                prom_metrics_file.write_text("{}")
                return
            
            node_ip = unit.deployment_hosts[0]
            if "@" in node_ip:
                node_ip = node_ip.split("@")[1]
            
            prom_url = f"http://{node_ip}:{node_port}"
            logging.info(f"Querying Prometheus at {prom_url}...")

            metrics_to_collect = [
                "max_queue",
                "avg_queue",
                "accepted_rpc_counter",
                "failed_rpc_counter",
            ]

            # Nested structure: data[api][service][metric] = value
            data: Dict[str, Dict[str, Dict[str, Any]]] = {}

            # Fetch and Parse
            for metric in metrics_to_collect:
                try:
                    query_url = f"{prom_url}/api/v1/query?query={metric}"
                    with urllib.request.urlopen(query_url, timeout=5) as response:
                        result = json.loads(response.read().decode('utf-8'))
                        
                        # Parse results
                        for item in result.get("data", {}).get("result", []):
                            m_labels = item.get("metric", {})
                            api = m_labels.get("api", "unknown")
                            service = m_labels.get("job", "unknown")
                            instance = m_labels.get("instance", "")
                            if instance:
                                service = f"{service}-{instance}"
                            
                            # Value tuple: [timestamp, value_str]
                            val_tuple = item.get("value", [])
                            if len(val_tuple) < 2:
                                continue
                            
                            try:
                                # Parse as float, or keep as string if desired. User said: 
                                # "value list is a tuple... I care about the second item".
                                # Usually better to store as number if possible
                                val_str = val_tuple[1]
                                val_num = float(val_str) if "." in val_str else int(val_str)
                            except ValueError:
                                val_num = val_tuple[1]

                            if api not in data:
                                data[api] = {}
                            if service not in data[api]:
                                data[api][service] = {}
                            
                            data[api][service][metric] = val_num
                            
                except Exception as e:
                    logging.warning(f"Failed to query metric {metric}: {e}")

            want_mean = unit.system in INGRESS_MEAN_ONLY_SYSTEMS or unit.system in INGRESS_MEAN_AND_MAX_SYSTEMS
            want_max = unit.system in INGRESS_MEAN_AND_MAX_SYSTEMS
            entry_ms = _entry_from_callgraph(unit.bench) if unit.bench else None

            for api in unit.apis:
                if not want_mean and not want_max:
                    break
                if api not in data:
                    continue

                overall_file = output_dir / f"overall-{api}.json"
                if not overall_file.exists():
                    logging.warning(f"Skipping ingress calc for {api}: {overall_file} not found")
                    continue

                try:
                    overall_data = json.loads(overall_file.read_text())
                    replica_keys = _frontend_replica_keys(data[api], entry_ms)
                    if not replica_keys:
                        fe = [k for k in data[api] if _match_service(k, ["frontend"]) == "frontend"]
                        ng = [k for k in data[api] if _match_service(k, ["nginx"]) == "nginx"]
                        if fe and ng:
                            logging.warning(
                                f"Skipping ingress calc for {api}: both 'frontend' and 'nginx' replicas found"
                            )
                        else:
                            logging.warning(
                                f"Skipping ingress calc for {api}: no frontend replicas "
                                f"(entry={entry_ms!r})"
                            )
                        continue

                    avgs = []
                    maxes = []
                    for key in replica_keys:
                        stats = data[api].get(key)
                        if not isinstance(stats, dict):
                            continue
                        if "avg_queue" in stats:
                            avgs.append(float(stats["avg_queue"]))
                        if "max_queue" in stats:
                            maxes.append(float(stats["max_queue"]))

                    if "ingress" not in data[api]:
                        data[api]["ingress"] = {}

                    if want_mean:
                        throughput = overall_data.get("throughput")
                        mean_latency = overall_data.get("mean_latency")
                        if throughput is None or mean_latency is None:
                            logging.warning(
                                f"Skipping ingress avg_queue for {api}: "
                                "throughput or mean_latency missing in overall json"
                            )
                        elif not avgs:
                            logging.warning(
                                f"Skipping ingress avg_queue for {api}: "
                                "no avg_queue on frontend replicas"
                            )
                        else:
                            frontend_mean = sum(avgs) / len(avgs)
                            avg_concurrency = throughput * (mean_latency / 1000.0)
                            data[api]["ingress"]["avg_queue"] = avg_concurrency - frontend_mean

                    if want_max:
                        max_workers = overall_data.get("maximum_workers")
                        if max_workers is None:
                            logging.warning(
                                f"Skipping ingress max_queue for {api}: "
                                "maximum_workers missing in overall json"
                            )
                        elif not maxes:
                            logging.warning(
                                f"Skipping ingress max_queue for {api}: "
                                "no max_queue on frontend replicas"
                            )
                        else:
                            data[api]["ingress"]["max_queue"] = max_workers - maxes[0]

                    if not data[api]["ingress"]:
                        del data[api]["ingress"]

                except Exception as e:
                    logging.warning(f"Error during ingress calc for {api}: {e}")

            
            prom_metrics_file.write_text(json.dumps(data, indent=2))
            logging.info(f"Prometheus metrics saved to {prom_metrics_file}")

            self._collect_queuing_timeseries(unit, prom_url, output_dir)

        except Exception as e:
            logging.warning(f"Top-level error collecting Prometheus metrics: {e}")
            prom_metrics_file.write_text("{}")

    def _prom_query_range(
        self, prom_url: str, query: str, start: float, end: float, step: str = "1s"
    ) -> List[Dict[str, Any]]:
        url = (
            f"{prom_url}/api/v1/query_range?query={quote(query, safe='')}"
            f"&start={start}&end={end}&step={step}"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("data", {}).get("result", [])

    def _realtime_window(self, realtime_csv: Path) -> Tuple[datetime, datetime] | None:
        if not realtime_csv.is_file():
            return None
        start_ts: datetime | None = None
        end_ts: datetime | None = None
        with realtime_csv.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = datetime.fromisoformat(row["timestamp"])
                if start_ts is None:
                    start_ts = ts
                end_ts = ts
        if start_ts is None or end_ts is None:
            return None
        return start_ts, end_ts

    def _service_label(self, metric: Dict[str, Any]) -> str:
        labels = metric.get("metric", {})
        service = labels.get("job", "unknown")
        instance = labels.get("instance", "")
        if instance:
            service = f"{service}-{instance}"
        return service

    def _collect_queuing_timeseries(self, unit: RunUnit, prom_url: str, output_dir: Path) -> None:
        quantile_fields = [
            ("p50_queuing_ms", 0.50),
            ("p95_queuing_ms", 0.95),
            ("p99_queuing_ms", 0.99),
        ]
        fieldnames = [
            "timestamp",
            "relative_time",
            "service",
            "p50_queuing_ms",
            "p95_queuing_ms",
            "p99_queuing_ms",
            "sample_count",
        ]

        for api in unit.apis:
            realtime_csv = output_dir / f"realtime-{api}.csv"
            window = self._realtime_window(realtime_csv)
            if window is None:
                logging.warning(f"Skipping queuing timeseries for {api}: {realtime_csv} missing or empty")
                continue

            start_ts, end_ts = window
            if start_ts.tzinfo is None:
                start_ts = start_ts.replace(tzinfo=timezone.utc)
            if end_ts.tzinfo is None:
                end_ts = end_ts.replace(tzinfo=timezone.utc)
            start = start_ts.timestamp()
            end = end_ts.timestamp()
            if end <= start:
                logging.warning(f"Skipping queuing timeseries for {api}: invalid time window")
                continue

            rows: Dict[Tuple[int, str], Dict[str, Any]] = {}
            for field, q in quantile_fields:
                query = (
                    f'histogram_quantile({q}, sum by (job, instance) '
                    f'(queuing_delay_microseconds{{api="{api}"}})) / 1000'
                )
                try:
                    series = self._prom_query_range(prom_url, query, start, end, step="1s")
                except Exception as e:
                    logging.warning(f"Failed queuing range query {query}: {e}")
                    continue

                for item in series:
                    service = self._service_label(item)
                    for ts_str, val_str in item.get("values", []):
                        ts = int(float(ts_str))
                        key = (ts, service)
                        if key not in rows:
                            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                            rows[key] = {
                                "timestamp": dt.isoformat(),
                                "relative_time": ts - int(start),
                                "service": service,
                                "p50_queuing_ms": "",
                                "p95_queuing_ms": "",
                                "p99_queuing_ms": "",
                                "sample_count": "",
                            }
                        try:
                            rows[key][field] = float(val_str)
                        except ValueError:
                            rows[key][field] = val_str

            count_query = f'sum by (job, instance) (queuing_delay_microseconds_count{{api="{api}"}})'
            try:
                series = self._prom_query_range(prom_url, count_query, start, end, step="1s")
                for item in series:
                    service = self._service_label(item)
                    for ts_str, val_str in item.get("values", []):
                        ts = int(float(ts_str))
                        key = (ts, service)
                        if key not in rows:
                            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                            rows[key] = {
                                "timestamp": dt.isoformat(),
                                "relative_time": ts - int(start),
                                "service": service,
                                "p50_queuing_ms": "",
                                "p95_queuing_ms": "",
                                "p99_queuing_ms": "",
                                "sample_count": "",
                            }
                        try:
                            rows[key]["sample_count"] = int(float(val_str))
                        except ValueError:
                            rows[key]["sample_count"] = val_str
            except Exception as e:
                logging.warning(f"Failed queuing count query {count_query}: {e}")

            if not rows:
                logging.warning(f"No queuing timeseries data for api={api}")
                continue

            out_path = output_dir / f"realtime-queuing-{api}.csv"
            with out_path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for (_, _), row in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                    writer.writerow(row)
            logging.info(f"Wrote queuing timeseries to {out_path}")

    def _run_rwg_parse(self, cmd_parse: List[str]) -> None:
        env = os.environ.copy()
        venv_bin = str((Path("rwg") / ".venv" / "bin").resolve())
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        subprocess.run(cmd_parse, capture_output=True, check=True, env=env)

    def _generate_reports(self, unit: RunUnit, output_dir: Path, index: Dict[str, Any], metric_files: List[str]):
        """Runs `rwg parse` to generate overall.json and realtime.csv."""
        version = "1"

        for api in unit.apis:
            rwg_output = output_dir / f"out-{api}.csv"
            if not rwg_output.exists():
                index["reports"][api] = {"status": "missing_csv", "file": str(rwg_output)}
                continue

            overall_json = output_dir / f"overall-{api}.json"
            slo = str(self.config.slos.get(api, 100))
            freq = unit.collector_freq if unit.collector_freq > 0 else 100
            realtime_csv = output_dir / f"realtime-{api}.csv"
            common = [
                self.config.rwg_binary_path, "parse",
                "--rwg_output", str(rwg_output),
                "--slo", slo,
                "--version", version,
                "--warmup", str(unit.warmup),
                "--cooldown", str(unit.cooldown),
            ]

            try:
                self._run_rwg_parse(common + [
                    "--overall_output", str(overall_json),
                    "--realtime_output", "",
                    "--freq", "0",
                ])
                self._run_rwg_parse(common + [
                    "--overall_output", "",
                    "--realtime_output", str(realtime_csv),
                    "--freq", str(freq),
                ])
                metric_files.append(str(overall_json))
                index["reports"][api] = {"status": "success", "file": str(overall_json)}
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode('utf-8') if e.stderr else 'No stderr'
                stdout = e.stdout.decode('utf-8') if e.stdout else 'No stdout'
                err_msg = f"RWG parse failed (code {e.returncode}).\nStderr: {stderr}\nStdout: {stdout}"
                logging.error(err_msg)
                index["reports"][api] = {"status": "error", "msg": err_msg}

    def _index_envoy_metrics(self, metrics_dir: Path, index: Dict[str, Any]) -> List[str]:
        envoy_dir = metrics_dir / "envoy"
        files: List[str] = []
        if not envoy_dir.is_dir():
            index["envoy_metrics"] = {}
            return files
        for fp in sorted(envoy_dir.glob("*.csv")):
            rel = str(fp.relative_to(metrics_dir))
            files.append(rel)
        index["envoy_metrics"] = {"dir": "envoy", "files": files}
        return files

    def _copy_metrics_for_plotting(self, output_dir: Path, metrics_dir: Path):
        """Copy overall-*.json from output_dir to metrics_dir so plot runner finds them."""
        for f in output_dir.glob("overall-*.json"):
            shutil.copy(f, metrics_dir / f.name)
