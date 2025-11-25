//! Reed–Solomon demo CLI for the native crate.
//!
//! Encodes a payload into (k data + m parity) shards, simulates erasures,
//! reconstructs the missing shards, and verifies the recovered payload.
//!
//! Build & run:
//!   cargo run --release --example rs_demo -- --help
//!   cargo run --release --example rs_demo --
//!   cargo run --release --example rs_demo -- --k=8 --m=4 --shard-size=131072 --erase=4
//!   cargo run --release --example rs_demo -- --loss-indexes=1,3,8 --print-layout
//!   cargo run --release --example rs_demo -- --seed=1337 --erase=2 --verbose
//!
//! Notes:
//! - Erasures must be <= m, otherwise reconstruction is impossible.
//! - By default, we erase up to min(m, max(1, m/2))) randomly chosen shards.

use std::env;
use std::time::{Duration, Instant};

use rand::{Rng, SeedableRng};
use rand::rngs::StdRng;

use animica_native::rs::{encode, reconstruct};

#[derive(Debug, Clone)]
struct Opts {
    k: usize,                // data shards
    m: usize,                // parity shards
    shard_size: usize,       // bytes per data shard (payload size = k * shard_size)
    seed: u64,               // rng seed
    erase: Option<usize>,    // number of shards to erase (randomly)
    loss_indexes: Option<Vec<usize>>, // explicit list of shard indexes to erase
    print_layout: bool,      // print shard presence map
    verbose: bool,           // extra logs
}

fn print_help_and_exit() -> ! {
    eprintln!(
r#"rs_demo — encode and reconstruct with Reed–Solomon erasures

USAGE:
  rs_demo [--k=DATA] [--m=PARITY] [--shard-size=BYTES]
          [--seed=U64] [--erase=N | --loss-indexes=i,j,...]
          [--print-layout] [--verbose] [--help]

OPTIONS:
  --k=DATA           Number of data shards (k). Default: 8
  --m=PARITY         Number of parity shards (m). Default: 4
  --shard-size=BYTES Bytes per data shard (payload = k*BYTES). Default: 131072 (128 KiB)
  --seed=U64         RNG seed for reproducibility. Default: 42
  --erase=N          Randomly erase N shards (N must be <= m). Mutually exclusive with --loss-indexes.
  --loss-indexes=L   Comma-separated shard indexes to erase (0-based within total k+m).
  --print-layout     Print presence map of shards before/after reconstruction.
  --verbose          Extra logs.
  --help             Show this help.

EXAMPLES:
  rs_demo --k=10 --m=6 --shard-size=262144 --erase=5
  rs_demo --loss-indexes=0,5,9 --print-layout --verbose
"#
    );
    std::process::exit(0);
}

fn parse_usize(name: &str, v: &str) -> Result<usize, String> {
    v.parse::<usize>().map_err(|_| format!("Invalid {name}: {v}"))
}

fn parse_u64(name: &str, v: &str) -> Result<u64, String> {
    v.parse::<u64>().map_err(|_| format!("Invalid {name}: {v}"))
}

fn parse_csv_indexes(v: &str) -> Result<Vec<usize>, String> {
    if v.trim().is_empty() { return Ok(vec![]); }
    let mut out = Vec::new();
    for part in v.split(',') {
        let p = part.trim();
        if p.is_empty() { continue; }
        out.push(parse_usize("index", p)?);
    }
    out.sort_unstable();
    out.dedup();
    Ok(out)
}

fn parse_args() -> Result<Opts, String> {
    let mut args = env::args().skip(1).collect::<Vec<_>>();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help_and_exit();
    }

    let mut k = 8usize;
    let mut m = 4usize;
    let mut shard_size = 131072usize; // 128 KiB
    let mut seed = 42u64;
    let mut erase: Option<usize> = None;
    let mut loss_indexes: Option<Vec<usize>> = None;
    let mut print_layout = false;
    let mut verbose = false;

    while let Some(arg) = args.first().cloned() {
        args.remove(0);
        if let Some(v) = arg.strip_prefix("--k=") {
            k = parse_usize("--k", v)?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--m=") {
            m = parse_usize("--m", v)?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--shard-size=") {
            shard_size = parse_usize("--shard-size", v)?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--seed=") {
            seed = parse_u64("--seed", v)?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--erase=") {
            erase = Some(parse_usize("--erase", v)?);
            continue;
        }
        if let Some(v) = arg.strip_prefix("--loss-indexes=") {
            loss_indexes = Some(parse_csv_indexes(v)?);
            continue;
        }
        if arg == "--print-layout" {
            print_layout = true;
            continue;
        }
        if arg == "--verbose" {
            verbose = true;
            continue;
        }
        return Err(format!("Unrecognized argument: {arg} (use --help)"));
    }

    if k == 0 { return Err("--k must be >= 1".into()); }
    if m == 0 { return Err("--m must be >= 1".into()); }
    if shard_size == 0 { return Err("--shard-size must be >= 1".into()); }
    if erase.is_some() && loss_indexes.is_some() {
        return Err("Use either --erase or --loss-indexes (not both)".into());
    }

    Ok(Opts { k, m, shard_size, seed, erase, loss_indexes, print_layout, verbose })
}

fn format_bytes(n: u128) -> String {
    if n >= 1_000_000_000_000 {
        format!("{:.2} TB", (n as f64) / 1_000_000_000_000.0)
    } else if n >= 1_000_000_000 {
        format!("{:.2} GB", (n as f64) / 1_000_000_000.0)
    } else if n >= 1_000_000 {
        format!("{:.2} MB", (n as f64) / 1_000_000.0)
    } else if n >= 1_000 {
        format!("{:.2} KB", (n as f64) / 1_000.0)
    } else {
        format!("{} B", n)
    }
}

fn mib_per_s(bytes: u128, elapsed: Duration) -> f64 {
    if elapsed.is_zero() { return f64::INFINITY; }
    (bytes as f64) / 1024.0 / 1024.0 / elapsed.as_secs_f64()
}

fn presence_map<T>(v: &[Option<T>]) -> String {
    // '□' for present, '×' for erased
    v.iter().map(|o| if o.is_some() { '□' } else { '×' }).collect()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let opts = parse_args().map_err(|e| { eprintln!("error: {e}"); e })?;

    let total_shards = opts.k + opts.m;
    let payload_len = opts.k * opts.shard_size;

    println!("== rs_demo ==");
    println!("k (data)     : {}", opts.k);
    println!("m (parity)   : {}", opts.m);
    println!("shard size   : {} bytes", opts.shard_size);
    println!("payload size : {} ({} × {})",
             format_bytes(payload_len as u128), opts.k, opts.shard_size);
    println!("seed         : {}", opts.seed);

    // Prepare payload
    let t_seed = Instant::now();
    let mut rng = StdRng::seed_from_u64(opts.seed);
    let mut payload = vec![0u8; payload_len];
    rng.fill(&mut payload[..]);
    let _ = t_seed.elapsed();

    // Encode -> shards
    let t0 = Instant::now();
    let shards_vec = encode(&payload, opts.k, opts.m)?;
    let enc_elapsed = t0.elapsed();

    if shards_vec.len() != total_shards {
        return Err(format!("encode returned {} shards, expected {}", shards_vec.len(), total_shards).into());
    }
    let shard_len = shards_vec[0].len();
    if opts.verbose {
        println!("encoded shards: {} (each {} bytes)", shards_vec.len(), shard_len);
    }

    // Build erasure pattern
    let loss_indexes = if let Some(ls) = opts.loss_indexes.clone() {
        for &i in &ls {
            if i >= total_shards {
                return Err(format!("loss index {} out of range 0..{}", i, total_shards-1).into());
            }
        }
        ls
    } else {
        // Choose N random indexes to erase
        let default_erase = (opts.m / 2).max(1).min(opts.m);
        let to_erase = opts.erase.unwrap_or(default_erase).min(opts.m);
        if to_erase == 0 {
            Vec::new()
        } else {
            let mut pool: Vec<usize> = (0..total_shards).collect();
            // simple Fisher–Yates style partial shuffle
            for i in 0..to_erase {
                let j = (rng.gen::<usize>() % (total_shards - i)) + i;
                pool.swap(i, j);
            }
            pool[..to_erase].to_vec()
        }
    };

    if loss_indexes.len() > opts.m {
        return Err(format!("erasures {} exceed parity m={}", loss_indexes.len(), opts.m).into());
    }

    println!("erasures     : {} {}", loss_indexes.len(),
        if loss_indexes.is_empty() {
            String::new()
        } else {
            format!("at indexes {:?}", loss_indexes)
        }
    );

    // Apply erasures
    let mut shards_opt: Vec<Option<Vec<u8>>> =
        shards_vec.into_iter().map(Some).collect();
    for &idx in &loss_indexes {
        shards_opt[idx] = None;
    }

    if opts.print_layout {
        let map = presence_map(&shards_opt);
        println!("layout (pre) : {}", map);
        println!("  legend: [0..k-1]=data, [k..k+m-1]=parity; □ present, × erased");
    }

    // Reconstruct in-place
    let t1 = Instant::now();
    reconstruct(&mut shards_opt)?;
    let rec_elapsed = t1.elapsed();

    if opts.print_layout {
        let map = presence_map(&shards_opt);
        println!("layout (post): {}", map);
    }

    // Verify reconstruction by reassembling the original payload
    let mut recovered = Vec::with_capacity(payload_len);
    for i in 0..opts.k {
        match &shards_opt[i] {
            Some(bytes) => recovered.extend_from_slice(bytes),
            None => return Err(format!("data shard {} still missing after reconstruction", i).into()),
        }
    }

    let ok = recovered == payload;
    println!("verify       : {}", if ok { "OK ✅" } else { "MISMATCH ❌" });
    if !ok {
        return Err("recovered payload does not match original".into());
    }

    // Throughput stats
    let encoded_bytes = (opts.k * shard_len) as u128;
    let reconstructed_bytes = (total_shards * shard_len) as u128; // upper bound work shown

    println!("encode time  : {:.3}s  ({:.2} MiB/s)",
             enc_elapsed.as_secs_f64(), mib_per_s(encoded_bytes, enc_elapsed));
    println!("recon time   : {:.3}s  ({:.2} MiB/s)",
             rec_elapsed.as_secs_f64(), mib_per_s(reconstructed_bytes as u128, rec_elapsed));

    Ok(())
}
