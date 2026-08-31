"""Print the reproducible r20 token calibration artifact."""

from __future__ import annotations

import argparse

from app.experiment.r20.calibration import calibration_from_archive, canonical_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("template_path")
    args = parser.parse_args()
    print(canonical_json(calibration_from_archive(args.archive, args.template_path)), end="")


if __name__ == "__main__":
    main()
