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

## Reliability and publication checks

The update runner is pinned to Ubuntu 24.04 for compatibility with the pinned Playwright release. IDB data is downloaded directly from the official DataStore CSV resource using the same standard-library HTTP client as the source-access probe; the metadata API is not required.

Publication requires every configured required source to contain records. Each source and the combined dataset must retain at least the configured 80% of the previous published count. An unexpected drop blocks publication for review; lowering the threshold is not an automatic recovery step. The absolute combined minimum and schema checks also remain active.

Transient requests-library transport failures and HTTP 429/5xx errors receive at most three attempts. Access denials are not suppressed. An IDB download error blocks publication, with the previous package and its original retrieval dates retained. A successful diagnostic probe alone does not establish that the full publication workflow succeeded.

Run `python -m unittest discover -s tests -v` after installing requirements. The workflow runs these tests before collecting live sources. To confirm recovery, require a successful full build on the pull request and a successful publication on main, then inspect the dataset version, source retrieval dates and counts in `data/manifest.json`.
