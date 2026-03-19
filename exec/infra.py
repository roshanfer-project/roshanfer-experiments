
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
            # Filter out comments and empty lines
            self.hosts = [
                line.strip() 
                for line in f 
                if line.strip() and not line.strip().startswith("#")
            ]
        
        if not self.hosts:
            raise ValueError(f"No valid hosts found in {self.hosts_file}")
            raise ValueError(f"No valid hosts found in {self.hosts_file}")

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

    def provision_hosts(self, provision_script: Path, log_path: Optional[Path] = None):
        """
        Runs the provisioning script on all hosts. 
        Assumes the script handles its own SSH loops or we call it once because it iterates hosts.
        
        Based on user's 'benchmarks/provisioning/provision.sh', it iterates over 'hosts.txt'.
        So we just need to run it once.
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
            
            run_with_logging([str(provision_script)], env=env, log_path=log_path)
            logging.info("Provisioning completed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Provisioning failed with code {e.returncode}")
            raise e

    def setup_k8s(self, k8s_script: Path, deployment_hosts: List[str], log_path: Optional[Path] = None):
        """
        Runs the K8s setup script on the deployment hosts.
        Creates a temporary hosts file to pass to the script via HOSTS_FILE env var.
        """
        if not k8s_script.exists():
            raise FileNotFoundError(f"K8s script not found: {k8s_script}")

        logging.info(f"Setting up K8s on {len(deployment_hosts)} hosts...")
        
        # Create a temp hosts file for the K8s script
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            for h in deployment_hosts:
                tmp.write(f"{h}\n")
            tmp_hosts_path = tmp.name
            
        try:
            env = os.environ.copy()
            env["HOSTS_FILE"] = tmp_hosts_path
            
            run_with_logging([str(k8s_script)], env=env, log_path=log_path)
            logging.info("K8s setup completed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"K8s setup failed with code {e.returncode}")
            # Clean up? 
            # We raise so executor can stop
            raise e
        finally:
            if os.path.exists(tmp_hosts_path):
                os.remove(tmp_hosts_path)
