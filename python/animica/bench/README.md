# Animica Benchmarking Tools

This directory contains benchmarking utilities for measuring Animica's performance.

## Available Benchmarks

### Signature Verification (`bench_verify.py`)

Measures signature verification performance:
- Single transaction verification rate
- Batch verification scaling (1, 10, 100, 1000 tx)
- Block validation time vs transaction count

## Usage

### Run All Benchmarks

```bash
python -m animica.bench.bench_verify
```

### Run Specific Benchmarks

```bash
# Single signature verification only
python -m animica.bench.bench_verify --single

# Batch verification scaling only
python -m animica.bench.bench_verify --batch

# Block validation benchmarks only
python -m animica.bench.bench_verify --block
```

### Configuration Options

```bash
# Set number of worker processes
python -m animica.bench.bench_verify --workers=4

# Set iterations for single verification benchmark
python -m animica.bench.bench_verify --single --iterations=200
```

## Environment Variables

### `ANIMICA_VERIFY_WORKERS`

Control the number of worker processes for batch verification:

```bash
export ANIMICA_VERIFY_WORKERS=4
python -m animica.bench.bench_verify --batch
```

Default: `max(1, cpu_count() - 1)`

## Example Output

```
=== Single Signature Verification (n=100) ===
Generating 100 test signatures... done
Total time:    15.234s
Mean time:     152.34ms
Median time:   151.20ms
Stdev:         2.45ms
Rate:          6.6 verifications/sec

=== Batch Verification Scaling (workers=auto) ===
Generating 1 test signatures... done
Batch size    1:  0.152s total,  152.00ms/sig,     6.6 sigs/sec
Generating 10 test signatures... done
Batch size   10:  0.654s total,   65.40ms/sig,    15.3 sigs/sec
Generating 100 test signatures... done
Batch size  100:  3.421s total,   34.21ms/sig,    29.2 sigs/sec
Generating 1000 test signatures... done
Batch size 1000: 32.156s total,   32.16ms/sig,    31.1 sigs/sec

=== Block Validation Time (workers=auto) ===
Generating 10 test signatures... done
Block with   10 tx:  0.654s validation,    15.3 tx/sec throughput
Generating 50 test signatures... done
Block with   50 tx:  2.012s validation,    24.9 tx/sec throughput
Generating 100 test signatures... done
Block with  100 tx:  3.421s validation,    29.2 tx/sec throughput
Generating 500 test signatures... done
Block with  500 tx: 16.234s validation,    30.8 tx/sec throughput
Generating 1000 test signatures... done
Block with 1000 tx: 32.156s validation,    31.1 tx/sec throughput

=== Benchmark Complete ===
```

## Interpreting Results

### Single Verification

- **Mean time**: Average time per verification
- **Median time**: Middle value (less affected by outliers)
- **Stdev**: Variability in timing
- **Rate**: Throughput in verifications per second

Higher variability (stdev) may indicate:
- CPU frequency scaling
- Background processes
- Thermal throttling
- OS scheduler interference

### Batch Verification

Look for:
- **Scaling efficiency**: Does throughput improve with batch size?
- **Saturation point**: Where does throughput level off?
- **Worker impact**: Does increasing workers help?

Ideal: Linear speedup until CPU saturation.

### Block Validation

Shows real-world throughput for block processing:
- How many transactions can be validated per second?
- What's the maximum sustainable block size?

## Performance Tips

### For Better Measurements

1. **Run on idle system**: Close background applications
2. **Disable CPU frequency scaling**: Use `performance` governor
3. **Run multiple times**: Take median of several runs
4. **Warm up**: First run may be slower (cache effects)

```bash
# Linux: Disable CPU frequency scaling
sudo cpupower frequency-set --governor performance

# Run benchmark multiple times
for i in {1..5}; do
    python -m animica.bench.bench_verify --single
done
```

### For Better Throughput

1. **Increase worker count**: `--workers=N`
2. **Use batch verification**: Process multiple signatures at once
3. **Enable caching**: Reduces repeated work
4. **Optimize hot paths**: Profile and optimize critical sections

## Troubleshooting

### "PQ backend not available"

The PQ (post-quantum) cryptography module is not installed or configured.

Solution:
```bash
# Check PQ module
python -c "from animica.pq import sig_verify; print('PQ available')"

# Set PQ mode if needed
export ANIMICA_PQ_MODE=pure
```

### Very slow performance

Possible causes:
- CPU throttling (thermal or power management)
- Debug mode enabled
- Running in VM or container with limited resources
- Pure Python implementation (no native acceleration)

### High variability

Timing tests can be noisy due to:
- OS scheduler preemption
- Garbage collection pauses
- Other processes competing for CPU
- CPU frequency scaling

Use `--iterations` to increase sample size and get more stable averages.

## Adding New Benchmarks

To add a new benchmark:

1. Create a function in `bench_verify.py` or a new module
2. Follow the pattern: generate test data, warmup, measure, report stats
3. Use `time.perf_counter()` for precise timing
4. Take median of multiple runs to reduce noise
5. Add CLI argument if needed

Example:

```python
def bench_new_operation(iterations: int = 100):
    """Benchmark new operation."""
    print(f"\n=== New Operation Benchmark (n={iterations}) ===")
    
    # Generate test data
    data = generate_test_data(iterations)
    
    # Warmup
    for _ in range(min(5, iterations)):
        operation(data[0])
    
    # Benchmark
    times = []
    for item in data:
        start = time.perf_counter()
        operation(item)
        end = time.perf_counter()
        times.append(end - start)
    
    # Report
    mean_time = statistics.mean(times)
    median_time = statistics.median(times)
    print(f"Mean time:   {mean_time*1000:.2f}ms")
    print(f"Median time: {median_time*1000:.2f}ms")
```

## See Also

- `python/animica/security/` - Security utilities
- `SECURITY.md` - Timing side-channel documentation
- `python/animica/security/tests/test_timing_variability.py` - Timing tests
