# AICF Provider Troubleshooting

## GPU not detected

- Verify `nvidia-smi` is available in PATH.
- Check driver installation and GPU visibility.
- Re-run `benchmark` mode and inspect output JSON.

## Heartbeat failures

- Confirm `api_base_url` and `provider_token` are valid.
- Verify outbound HTTPS access to AICF API.
- Inspect `logs/provider-worker.log` for HTTP status details.

## No jobs assigned

- Ensure benchmark has been submitted for this node.
- Check provider reputation and required stake settings.
- Confirm worker node labels/capabilities match active model queues.
