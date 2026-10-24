"""
Backend for the end-to-end tests: ``python e2e/backend.py --port P --data DIR``.

Writes two synthetic GCxGC-MS runs (``DIR/run.cdf``, ``DIR/other.cdf``, from
``tests/synthetic.py``), their library (``DIR/library.msp``) and
``DIR/expected.json`` (``integrate_masks`` of the example FID with the example
mask TIFFs), then serves a fresh session with in-memory settings and the
built frontend. PubChem is stubbed: it knows Decane by its CAS number only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from synthetic import lib_from_dict, synthetic_run, write_andi  # noqa: E402
from test_structures import DECANE, pubchem  # noqa: E402

import pyGCxGC as gcgc  # noqa: E402
from pyGCxGC.app.server import serve  # noqa: E402
from pyGCxGC.app.session import Session  # noqa: E402

EXAMPLE = ROOT / "example_data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", default="e2e")
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    write_andi(synthetic_run()[0], data / "run.cdf")
    write_andi(synthetic_run(seed=5)[0], data / "other.cdf")
    chrom = gcgc.parse_2D_chromatogram(
        EXAMPLE / "example_chromatograms" / "Example_FID.csv", modulation_time=20
    )
    expected = gcgc.integrate_masks(chrom.chrom_2D, EXAMPLE / "example_masks")
    (data / "expected.json").write_text(json.dumps(expected))
    lib_from_dict().to_msp(data / "library.msp")

    def ready(port: int, _token: str) -> None:
        print(f"READY {port}", flush=True)  # noqa: T201

    shutil.rmtree(data / "cache", ignore_errors=True)  # structures looked up before
    session = Session(cache_dir=data / "cache")
    session.pubchem, _ = pubchem({"124-18-5": DECANE})
    serve(session, port=args.port, token=args.token, on_ready=ready)


if __name__ == "__main__":
    main()
