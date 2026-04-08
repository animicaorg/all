# ENA Scraping Controls

ENA is designed for professional ingestion, not unrestricted scraping.

## Defaults

- `robots.txt` respected
- login disabled
- browser automation disabled
- request cap enforced
- per-domain rate limit enforced
- response size cap enforced
- retries and backoff enabled
- allow/deny domain policy applied

## Config Knobs

The main network policy fields are:

- `allow_domains`
- `deny_domains`
- `max_requests`
- `max_depth`
- `size_limit_bytes`
- `request_timeout_seconds`
- `retries`
- `backoff_seconds`
- `rate_limit_per_domain_per_minute`
- `user_agent`
- `respect_robots`
- `allow_browser_automation`
- `allow_login`

## Example Workspace Config

```toml
[network]
allow_domains = ["docs.animica.org", "github.com"]
deny_domains = ["example.com"]
max_requests = 100
max_depth = 2
respect_robots = true
rate_limit_per_domain_per_minute = 20
```

## Operational Guidance

- Keep allowlists narrow for mining jobs
- Encode request/depth limits in the job spec as well as the base config
- Verify provenance on every output batch before converting it into datasets
- Treat scrape outputs as raw material, not training-ready data
