import subprocess
import sys
from datetime import datetime


def run_test(args):
    if subprocess.run(["pytest", "-q"] + args, capture_output=True, text=True).returncode != 0:
        sys.exit(subprocess.run(["pytest", "-q"] + args + ["--maxfail=1"]).returncode)


if __name__ == "__main__":
    run_test(["-m", "pedantic"])
    run_test([])
    run_test(["-m", "regression_test"])
    print(f"All tests passed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
