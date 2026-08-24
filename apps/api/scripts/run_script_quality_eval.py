import argparse
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.evals.script_quality import (  # noqa: E402
    build_blind_pairwise_packet,
    evaluate_script_output,
    load_output_directory,
    load_script_quality_cases,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline script contract regression and blind pairwise packet builder."
    )
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--outputs", type=Path)
    parser.add_argument("--compare-a", type=Path)
    parser.add_argument("--compare-b", type=Path)
    parser.add_argument("--packet-out", type=Path)
    parser.add_argument("--key-out", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    cases = load_script_quality_cases(args.cases)
    if args.outputs:
        outputs = load_output_directory(args.outputs)
        missing = [str(case["id"]) for case in cases if str(case["id"]) not in outputs]
        results = [
            evaluate_script_output(case, outputs[str(case["id"])])
            for case in cases
            if str(case["id"]) in outputs
        ]
        print(
            json.dumps(
                {
                    "case_count": len(cases),
                    "evaluated": len(results),
                    "missing": missing,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if missing and not args.allow_partial:
            return 2
        return 1 if any(not result["passed"] for result in results) else 0

    if args.compare_a and args.compare_b and args.packet_out and args.key_out:
        packet, key = build_blind_pairwise_packet(
            cases,
            load_output_directory(args.compare_a),
            load_output_directory(args.compare_b),
        )
        args.packet_out.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        args.key_out.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case_count": len(cases), "paired": len(packet)}, ensure_ascii=False))
        if len(packet) != len(cases) and not args.allow_partial:
            return 2
        return 0

    print(json.dumps({"case_count": len(cases), "provider_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
