# Grain Marketing Desk

A marketing and risk tracker for farm clients. Farmers record their own crops,
sales and contracts, storage, break-even and crop insurance. The consultant
sees every client farm in one dashboard.

Published as a Claude artifact: https://claude.ai/artifact/PW6VnKWDqicDLenhK9TTL2

## Files

- `grain-desk.html`: the whole app (page, styles and script). Publishing wraps it
  in the standard HTML document skeleton, so it has no `<html>`/`<head>` tags of its own.

## Data

Farm records are not stored in this repository. Each farm is one document at
`farms/<id>` in the artifact's database:

- Farmers read and write only their own `farms/<their id>` document.
- The consultant (artifact owner) reads and writes all of them.

## Snowflake

In the consultant view, **Export for Snowflake** downloads a zip with one CSV per
table (FARMS, CROP_PLANS, CONTRACTS, LOADS, PRICING_EVENTS, BINS), a
`snowflake_setup.sql` script and a README. Each row carries `EXPORTED_AT`, so
repeated exports build a history; the `*_CURRENT` views show the latest one.
