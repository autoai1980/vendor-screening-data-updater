# Vendor Screening Data Updater

Automated official-source sanctions, terrorism, and debarment data updates for the Vendor Screening Tool.

## Sources

- Global Affairs Canada Consolidated Canadian Autonomous Sanctions List
- United Nations Security Council Consolidated List
- Public Safety Canada currently listed terrorist entities
- Canadian RIUNRST schedule
- World Bank ineligible firms and individuals
- Inter-American Development Bank sanctioned firms and individuals

The scheduled workflow retrieves all required sources, normalizes them into the screening tool's 15-field record format, validates source coverage and record counts, and publishes only complete packages. Any retrieval, parsing, schema, or reconciliation failure blocks publication and preserves the previous dataset.

The repository stores public-source data only. Vendor names and screening results are never transmitted to or stored in this repository.
