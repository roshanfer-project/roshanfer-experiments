
from glob import glob
import json
import os
import subprocess
import argparse
from .models import RunUnit
from .config import Config

class EnvSetter:
    def __init__(self, config, env_fields):
        self.config = config
        self.env_fields = env_fields

    def _write_env_fields(self):
        # create a file named env-setter.env with env fields and then send it to the remote host
        with open("env-setter.env", "w") as f:
            for key, value in self.env_fields.items():
                f.write(f"{key}={value}\n")

        subprocess.run(
            ["scp",
                "env-setter.env",
            f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}:{self.config.remote_microservice_path}/.."],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    
    def _clear_microservice(self) -> None:
        env_cmd = f"cd {self.config.remote_microservice_path}/.. && rm -f env-setter.env"
        remote_cmd = f"cd {self.config.remote_microservice_path} && ./clean.sh"
        subprocess.run(
            ["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", remote_cmd],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", env_cmd],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


def load_optimal_parameters(bench, method, system):
    # load most recent paramters from a file in this format: bopt_rajomon_<method>_<timestamp>.json
    files = glob(f'rajomon_tune_run/bopt_{bench}_{system}_{method}_*.json')
    if not files:
        raise Exception("not found")
    latest_file = max(files, key=os.path.getctime)
    with open(latest_file, 'r') as f:
        return json.load(f)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Set environment for microservice execution.")
    parser.add_argument("bench", type=str, help="Benchmark name")
    parser.add_argument("method", type=str, help="Method name")
    parser.add_argument("system", type=str, help="System name")
    args = parser.parse_args()

    bench = args.bench
    method = args.method
    system = args.system

    config = Config()
    config.remote_microservice_user = "farzad"
    config.remote_microservice_host = "192.168.1.100"
    config.remote_microservice_path = f"/home/farzad/files/ppm/bench/{bench}/exec"
    config.k6_scripts_root = f"bench/{bench}/k6"

    tuner_parameters = load_optimal_parameters(bench, method, system)["parameters"]

    setter = EnvSetter(config, tuner_parameters)
    setter._clear_microservice()
    setter._write_env_fields()
