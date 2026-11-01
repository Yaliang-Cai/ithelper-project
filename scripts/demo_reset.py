"""Offline business reset. Stop the local server first; never resets spending.

From repository root: python -m scripts.demo_reset [--runtime-dir runtime]
"""
import argparse
import json
from pathlib import Path
from app.config import Settings
from app.domain import Domain
from app.storage import Store


def reset(runtime_dir):
    root = Path(runtime_dir)
    ledger = root / 'usage.sqlite'
    before = ledger.read_bytes() if ledger.exists() else None
    domain = Domain(Store(root / 'business.sqlite').initialize())
    domain.store.reset_demo()
    unchanged = (ledger.read_bytes() if ledger.exists() else None) == before
    if not unchanged:
        raise RuntimeError('Spending ledger unexpectedly changed')
    return {'business_reset': True, 'spending_ledger_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path)
    args = parser.parse_args()
    settings = Settings.load()
    print(json.dumps(reset(args.runtime_dir or settings.runtime_dir)))


if __name__ == '__main__':
    main()
