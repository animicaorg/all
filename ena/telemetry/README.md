# ENA Telemetry

Opt-in data collection system for improving ENA with user consent.

## Privacy-First Design

- **Opt-in by default** - Disabled unless user enables
- **Aggressive redaction** - Removes emails, phone numbers, API keys
- **Local control** - All data stored locally until approved
- **Full transparency** - Inspect and delete anytime
- **No auto-upload** - Manual curation required

## Quick Start

### Enable Telemetry

```bash
# Enable (opt-in)
animica config set telemetry.opt_in true

# Use ENA (data collected automatically)
animica ena chat "What is the capital of France?"

# Inspect buffer
animica data inspect

# Curate and upload
animica data curate --auto --threshold 0.5 --mock
```

### Disable Telemetry

```bash
# Disable
animica config set telemetry.opt_in false

# Clear buffer
animica data clear --force
```

## CLI Commands

### Data Commands

```bash
animica data curate [--auto] [--threshold 0.5] [--mock]
animica data inspect [--limit 10] [--id SAMPLE_ID]
animica data clear [--id SAMPLE_ID] [--force]
```

### Config Commands

```bash
animica config set telemetry.opt_in true|false
animica config get telemetry
animica config show
```

## Redaction

Automatically redacts:
- Emails: `test@example.com` → `[EMAIL_REDACTED]`
- Long numbers (11+ digits): `12345678901` → `[NUMBER_REDACTED]`
- API keys (32+ chars): `sk_abc...` → `[KEY_REDACTED]`

## Quality Scoring

Samples scored 0.0 to 1.0 based on:
- Feedback score (if provided)
- User edits (negative signal)
- Flagged samples (rejected)
- Redaction count
- Reasonable length

## Configuration

**Location:** `~/.animica/ena_telemetry.json`

**Buffer:** `~/.animica/telemetry_buffer/`

**Default:**

```json
{
  "opt_in": false,
  "collect_prompts": true,
  "collect_responses": true,
  "redact_emails": true,
  "redact_long_numbers": true,
  "redact_api_keys": true,
  "max_buffer_size": 1000,
  "auto_curate": false
}
```

## Programmatic Usage

```python
from ena.telemetry import TelemetryCollector, TelemetryCurator

# Collect
collector = TelemetryCollector()
sample_id = collector.collect(
    prompt="Hello",
    response="Hi there!",
    model_version="ena-v1.0",
    feedback_score=0.9,
)

# Inspect
samples = collector.inspect(limit=10)

# Curate
curator = TelemetryCurator(mock_mode=True)
result = curator.curate(auto=True, quality_threshold=0.5)
```

## See Also

- Parent directory `ENA_WORKERS_TELEMETRY_IMPLEMENTATION.md` for full documentation
