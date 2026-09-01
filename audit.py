#!/usr/bin/env python3
"""Run the auditor without installing it: `python audit.py --file model.pbit`."""

from pbi_auditor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
