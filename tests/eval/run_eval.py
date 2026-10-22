"""Replay labelled photos through identification. Usage: run_eval.py cases.json"""
import json
import sys
import tempfile
from pathlib import Path

from parcel_finder.identify import identify
from parcel_finder.runlog import RunLog


def main(cases_file: str) -> int:
    cases = json.loads(Path(cases_file).read_text())
    log_dir = Path(tempfile.mkdtemp(prefix="pf-eval-"))
    failures = 0
    for case in cases:
        r = identify(Path(case["photo"]).expanduser(), RunLog.start(Path(case["photo"]), log_dir))
        text = f"{r.item} {r.evidence}".lower()
        ok = (not case.get("source") or r.source == case["source"]) \
            and all(w.lower() in text for w in case.get("must_mention", [])) \
            and not any(w.lower() in text for w in case.get("must_not_mention", []))
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} {case['name']}: [{r.source}/{r.confidence}] {r.item}")
    print(f"{len(cases) - failures}/{len(cases)} passed; logs in {log_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
