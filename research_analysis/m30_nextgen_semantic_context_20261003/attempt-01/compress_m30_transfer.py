"""Create a deterministic lossless Git-friendly copy of the large M30 CSV."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "eeg_transfer_per_trial.csv"
COMPRESSED = ROOT / "eeg_transfer_per_trial.csv.gz"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError("uncompressed M30 transfer CSV is missing")
    if COMPRESSED.exists():
        raise FileExistsError("refusing to overwrite the existing compressed artifact")
    with SOURCE.open("rb") as source, COMPRESSED.open("xb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination,
                           compresslevel=9, mtime=0) as compressor:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                compressor.write(block)
    source_hash = _sha256(SOURCE)
    recovered_hash = hashlib.sha256()
    recovered_bytes = 0
    with gzip.open(COMPRESSED, "rb") as recovered:
        for block in iter(lambda: recovered.read(1024 * 1024), b""):
            recovered_hash.update(block)
            recovered_bytes += len(block)
    if recovered_hash.hexdigest() != source_hash or recovered_bytes != SOURCE.stat().st_size:
        raise ValueError("compressed copy does not round-trip byte-for-byte")
    print("source_bytes={}".format(SOURCE.stat().st_size))
    print("compressed_bytes={}".format(COMPRESSED.stat().st_size))
    print("round_trip_sha256={}".format(source_hash))
    print("source_preserved=true")


if __name__ == "__main__":
    main()
