
import logging
import subprocess
from pathlib import Path
import sys
import sys
from typing import List, Tuple, Optional
from .utils import run_with_logging

class InfraBuilder:
    def __init__(self, hosts_file: Path):
        self.hosts_file = hosts_file
        self.hosts: List[str] = []
        self._load_hosts()

    def _load_hosts(self):
        if not self.hosts_file.exists():
            raise FileNotFoundError(f"Hosts file not found: {self.hosts_file}")
        
        with open(self.hosts_file, "r") as f:
            self.hosts = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]

        if not self.hosts:
            raise ValueError(f"No valid hosts found in {self.hosts_file}")
        for h in self.hosts:
            if "@" not in h:
                raise ValueError(f"Host line must be user@host: {h}")

    # _run_with_logging removed, using utils.run_with_logging instead

    def partition_hosts(self, num_generators: int, min_required: int = 1) -> Tuple[List[str], List[str]]:
        """
        Partitions the hosts into generators and deployment nodes.
        
        Args:
            num_generators: Number of generator nodes explicitly requested.
            min_required: Minimum number of generators required by the workload (e.g. max APIs).
            
        Returns:
            (generator_hosts, deployment_hosts)
        """
        if num_generators < min_required:
             raise ValueError(
                f"Configured 'num_generators' ({num_generators}) is insufficient for "
                f"workload requiring {min_required} APIs/generators."
            )

        if len(self.hosts) < num_generators + 1:
            raise ValueError(
                f"Not enough hosts. Need at least {num_generators + 1} ({num_generators} gen + 1 deploy), "
                f"but check found {len(self.hosts)} hosts."
            )

        # First N hosts are generators
        generators = self.hosts[:num_generators]
        # The rest are deployment
        deployment = self.hosts[num_generators:]

        logging.info(f"Partitioned hosts: {len(generators)} Generators, {len(deployment)} Deployment")
        return generators, deployment

    def provision_hosts(
        self,
        provision_script: Path,
        log_path: Optional[Path] = None,
        provision_host_logs_dir: Optional[Path] = None,
    ):
        """
        Runs the provisioning script on all hosts. 
        Assumes the script handles its own SSH loops or we call it once because it iterates hosts.

        provision_host_logs_dir: if set, passed as PROVISION_LOG_DIR so per-host provision
        transcripts (e.g. provision_host_*.log) are written there; default in the script is
        benchmarks/provisioning/provision_logs/... when unset.
        """
        if not provision_script.exists():
            raise FileNotFoundError(f"Provision script not found: {provision_script}")

        logging.info("Starting provisioning...")
        try:
            # The provision script expects to find 'hosts.txt' relative to itself or we might need to point it?
            # Looking at provision.sh: HOSTS_FILE="$SCRIPT_DIR/hosts.txt"
            # So if we provide a different hosts file we might need to update that or ensure consistency.
            # Ideally we ensure the hosts file in use is the one we loaded.
            # So if we provide a different hardcoded hosts file we might need to update that or ensure consistency.
            # Ideally we ensure the hosts file in use is the one we loaded.
            
            import os
            env = os.environ.copy()
            env["HOSTS_FILE"] = str(self.hosts_file.resolve())

            if provision_host_logs_dir is not None:
                hl = provision_host_logs_dir.resolve()
                hl.mkdir(parents=True, exist_ok=True)
                env["PROVISION_LOG_DIR"] = str(hl)

            run_with_logging([str(provision_script)], env=env, log_path=log_path)
            if provision_host_logs_dir is not None:
                logging.info("Per-host provision transcripts under %s", provision_host_logs_dir.resolve())
            logging.info("Provisioning completed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Provisioning failed with code {e.returncode}")
            raise e

    def setup_k8s(self, k8s_script: Path, num_generators: int, log_path: Optional[Path] = None):
        """Run K8s setup. create.sh reads HOSTS_FILE and skips the first NUM_GENERATORS lines."""
        if not k8s_script.exists():
            raise FileNotFoundError(f"K8s script not found: {k8s_script}")

        import os

        logging.info(
            "Setting up K8s from %s (skip first %s generator line(s))",
            self.hosts_file,
            num_generators,
        )
        env = os.environ.copy()
        env["HOSTS_FILE"] = str(self.hosts_file.resolve())
        env["NUM_GENERATORS"] = str(num_generators)
        try:
            run_with_logging([str(k8s_script)], env=env, log_path=log_path)
            logging.info("K8s setup completed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"K8s setup failed with code {e.returncode}")
            raise e
