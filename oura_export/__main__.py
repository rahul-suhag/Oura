"""Allows `python -m oura_export <command>` (used by the Windows scheduler)."""

from .cli import main

raise SystemExit(main())
