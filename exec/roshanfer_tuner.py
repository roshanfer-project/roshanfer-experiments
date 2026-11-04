
import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    print(f"Empty tuner called for {args.system} on {args.bench}. Returning default parameters.")

    # Return valid but empty parameters to proceed with defaults
    result = {
        "parameters": {},
        "metadata": {
            "source": "empty_roshanfer_tuner"
        }
    }
    
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
