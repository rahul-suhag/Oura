"""oura_export: scheduled download of your own Oura data export (CSV).

Clean-room implementation. This project only interacts with:
  - the Oura Membership Hub data-export page (https://membership.ouraring.com/data-export),
    logged in as you, to request and download YOUR export, and
  - your local filesystem.

It contains no Oura code and does not decompile, instrument, or reverse-engineer
the Oura app. It relies on the official data-export feature (EU data portability).
"""

__version__ = "0.1.0"
