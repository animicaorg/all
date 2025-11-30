use std::time::Instant;
use std::env;

fn run_bench() -> Result<(), String> {
    // Message sizes to test (bytes)
    let sizes = [32usize, 256usize, 1024usize, 4096usize];
    let iterations = 500usize; // per size
    let scheme = "Dilithium3";

    println!("PQ verify benchmark (scheme={})", scheme);

    for &sz in &sizes {
        // prepare message
        let msg = vec![0x42u8; sz];

        // Generate a keypair & signature using the crate's verify_rust if available
        #[cfg(feature = "with-oqs")]
        {
            // Use oqs directly via the library API (through the verify_rust helper: we still need keys)
            // For simplicity create a signature using the oqs binding here if available.
            use oqs::sig::Sig;

            let signer = Sig::new(scheme).map_err(|e| format!("oqs init failed: {:?}", e))?;
            let (pk, sk) = signer.keypair().map_err(|e| format!("keypair failed: {:?}", e))?;
            let signature = signer.sign(&msg, &sk).map_err(|e| format!("sign failed: {:?}", e))?;

            // Warmup
            for _ in 0..10 { let _ = signer.verify(&msg, &signature, &pk); }

            // Collect per-iteration latencies to compute percentiles
            let mut lens: Vec<f64> = Vec::with_capacity(iterations);
            for _ in 0..iterations {
                let t0 = Instant::now();
                let _ = signer.verify(&msg, &signature, &pk);
                let dt = t0.elapsed().as_secs_f64() * 1000.0;
                lens.push(dt);
            }

            // Compute stats
            lens.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let sum: f64 = lens.iter().sum();
            let avg = sum / lens.len() as f64;
            let p50 = lens[lens.len() / 2];
            let p95_idx = ((lens.len() as f64) * 0.95).ceil() as usize - 1;
            let p95 = lens[p95_idx.min(lens.len()-1)];
            let p99_idx = ((lens.len() as f64) * 0.99).ceil() as usize - 1;
            let p99 = lens[p99_idx.min(lens.len()-1)];

            // Print a JSON line per size for easy parsing
            let json = serde_json::json!({
                "size": sz,
                "iterations": iterations,
                "avg_ms": avg,
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99
            });
            println!("PQ_BENCH_JSON: {}", json.to_string());
        }

        #[cfg(not(feature = "with-oqs"))]
        {
            println!("Skipping real PQ benchmark for size={} bytes: built without oqs feature", sz);
        }
    }

    Ok(())
}

fn main() {
    if let Err(e) = run_bench() {
        eprintln!("Error: {}", e);
        std::process::exit(2);
    }
}
