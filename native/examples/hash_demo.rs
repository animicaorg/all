//! Hash demo CLI for the native crate.
//!
//! Features:
//! - Compute BLAKE3, SHA-256, and Keccak-256 of a file (or stdin).
//! - Streamed hashing with a tunable chunk size (no unbounded buffering).
//! - Simple micro-benchmark: report elapsed time and throughput (MiB/s).
//!
//! Build & run:
//!   cargo run --release --example hash_demo -- --help
//!   cargo run --release --example hash_demo -- <path>               # all algos
//!   cargo run --release --example hash_demo -- <path> --algo=blake3
//!   cargo run --release --example hash_demo -- <path> --iters=3
//!   cargo run --release --example hash_demo -- - --algo=keccak256    # stdin
//!
//! Notes:
//! - Throughput numbers are approximate and depend on storage, CPU, and flags.
//! - If you pass `-` as the path, input is read from STDIN (size unknown).

use std::env;
use std::fs::File;
use std::io::{self, BufReader, Read};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use sha2::{Digest as _, Sha256};
use tiny_keccak::{Hasher as _, Keccak};

/// Which hashing algorithm to run.
#[derive(Clone, Copy, Debug)]
enum Algo {
    Blake3,
    Sha256,
    Keccak256,
}

impl Algo {
    fn parse(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "blake3" | "b3" => Some(Self::Blake3),
            "sha256" | "sha-256" | "sha2" => Some(Self::Sha256),
            "keccak256" | "keccak-256" | "keccak" => Some(Self::Keccak256),
            _ => None,
        }
    }

    fn name(&self) -> &'static str {
        match self {
            Algo::Blake3 => "BLAKE3",
            Algo::Sha256 => "SHA-256",
            Algo::Keccak256 => "Keccak-256",
        }
    }
}

#[derive(Debug)]
struct Opts {
    path: PathBuf,       // "-" denotes stdin
    algos: Vec<Algo>,    // empty => all
    chunk: usize,        // read buffer size
    iters: usize,        // repeat runs for micro-bench
}

fn print_help_and_exit() -> ! {
    eprintln!(
r#"hash_demo — stream-hash files and report throughput

USAGE:
  hash_demo <path|-> [--algo=blake3|sha256|keccak256]... [--chunk=BYTES] [--iters=N]

ARGS:
  <path|->         Path to file to hash. Use "-" to read from STDIN.

OPTIONS:
  --algo=...       Algorithm to run. May be repeated. Defaults to all three.
  --chunk=BYTES    Read chunk size (decimal). Default: 4194304 (4 MiB).
  --iters=N        Repeat hashing N times for timing. Default: 1.
  --help           Show this help.

EXAMPLES:
  hash_demo big.dat
  hash_demo big.dat --algo=blake3 --iters=3
  hash_demo - --algo=keccak256 --chunk=1048576
"#
    );
    std::process::exit(0);
}

fn parse_args() -> Result<Opts, String> {
    let mut args = env::args().skip(1).collect::<Vec<_>>();
    if args.is_empty() || args.iter().any(|a| a == "--help" || a == "-h") {
        print_help_and_exit();
    }

    // First non-flag is the path, everything else are flags.
    let mut path_opt: Option<PathBuf> = None;
    let mut algos: Vec<Algo> = Vec::new();
    let mut chunk: usize = 4 * 1024 * 1024; // 4 MiB
    let mut iters: usize = 1;

    while let Some(arg) = args.first().cloned() {
        args.remove(0);
        if !arg.starts_with("--") && path_opt.is_none() {
            path_opt = Some(PathBuf::from(arg));
            continue;
        }
        if let Some(rest) = arg.strip_prefix("--algo=") {
            if let Some(a) = Algo::parse(rest) {
                algos.push(a);
            } else {
                return Err(format!("Unknown --algo value: {}", rest));
            }
            continue;
        }
        if let Some(rest) = arg.strip_prefix("--chunk=") {
            chunk = rest.parse::<usize>()
                .map_err(|_| format!("Invalid --chunk bytes: {}", rest))?;
            if chunk == 0 {
                return Err("--chunk must be > 0".into());
            }
            continue;
        }
        if let Some(rest) = arg.strip_prefix("--iters=") {
            iters = rest.parse::<usize>()
                .map_err(|_| format!("Invalid --iters: {}", rest))?;
            if iters == 0 {
                return Err("--iters must be >= 1".into());
            }
            continue;
        }
        if arg == "--help" || arg == "-h" {
            print_help_and_exit();
        }
        return Err(format!("Unrecognized argument: {}", arg));
    }

    let path = path_opt.ok_or("Missing <path> argument".to_string())?;
    if algos.is_empty() {
        algos = vec![Algo::Blake3, Algo::Sha256, Algo::Keccak256];
    }

    Ok(Opts { path, algos, chunk, iters })
}

/// Read from a reader into the provided hasher update function, return (bytes, elapsed).
fn pump_reader<R: Read, F: FnMut(&[u8])>(mut rdr: R, mut update: F, chunk: usize)
-> io::Result<(u128 /*bytes*/, Duration)> {
    let mut buf = vec![0u8; chunk];
    let mut total: u128 = 0;
    let start = Instant::now();
    loop {
        let n = rdr.read(&mut buf)?;
        if n == 0 { break; }
        update(&buf[..n]);
        total += n as u128;
    }
    let elapsed = start.elapsed();
    Ok((total, elapsed))
}

fn hash_blake3<R: Read>(rdr: R, chunk: usize) -> io::Result<([u8; 32], u128, Duration)> {
    let mut hasher = blake3::Hasher::new();
    let (bytes, elapsed) = pump_reader(rdr, |b| hasher.update(b), chunk)?;
    let out = hasher.finalize();
    let mut digest = [0u8; 32];
    digest.copy_from_slice(out.as_bytes());
    Ok((digest, bytes, elapsed))
}

fn hash_sha256<R: Read>(rdr: R, chunk: usize) -> io::Result<([u8; 32], u128, Duration)> {
    let mut hasher = Sha256::new();
    let (bytes, elapsed) = pump_reader(rdr, |b| hasher.update(b), chunk)?;
    let out = hasher.finalize();
    let mut digest = [0u8; 32];
    digest.copy_from_slice(&out[..]);
    Ok((digest, bytes, elapsed))
}

fn hash_keccak256<R: Read>(rdr: R, chunk: usize) -> io::Result<([u8; 32], u128, Duration)> {
    let mut hasher = Keccak::v256();
    let (bytes, elapsed) = pump_reader(rdr, |b| hasher.update(b), chunk)?;
    let mut digest = [0u8; 32];
    hasher.finalize(&mut digest);
    Ok((digest, bytes, elapsed))
}

fn to_hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = vec![0u8; 64];
    for (i, b) in bytes.iter().enumerate() {
        out[2*i]   = HEX[(b >> 4) as usize];
        out[2*i+1] = HEX[(b & 0x0f) as usize];
    }
    String::from_utf8(out).unwrap()
}

fn is_stdin(path: &PathBuf) -> bool {
    path.as_os_str() == "-"
}

fn open_reader(path: &PathBuf) -> io::Result<Box<dyn Read>> {
    if is_stdin(path) {
        Ok(Box::new(io::stdin()))
    } else {
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}

fn format_bytes(n: u128) -> String {
    // Use decimal MB for "size", MiB/s for speed (common convention in benches)
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
    let secs = elapsed.as_secs_f64();
    (bytes as f64) / (1024.0 * 1024.0) / secs
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let opts = parse_args().map_err(|e| {
        eprintln!("error: {e}\nUse --help for usage."); e
    })?;

    println!("== hash_demo ==");
    println!("file      : {}", if is_stdin(&opts.path) { "<stdin>" } else { opts.path.to_string_lossy().as_ref() });
    println!("chunk     : {} bytes", opts.chunk);
    println!("iterations: {}", opts.iters);
    println!();

    for algo in &opts.algos {
        let mut times: Vec<Duration> = Vec::with_capacity(opts.iters);
        let mut last_digest = [0u8; 32];
        let mut last_bytes: u128 = 0;

        for i in 0..opts.iters {
            let rdr = open_reader(&opts.path)?;
            let (digest, bytes, elapsed) = match algo {
                Algo::Blake3   => hash_blake3(rdr, opts.chunk)?,
                Algo::Sha256   => hash_sha256(rdr, opts.chunk)?,
                Algo::Keccak256=> hash_keccak256(rdr, opts.chunk)?,
            };
            last_digest = digest;
            last_bytes = bytes;
            times.push(elapsed);

            println!(
                "[{alg} #{run}] digest={hex} size={size:>10} time={:.3}s speed={:.2} MiB/s",
                elapsed.as_secs_f64(),
                mib_per_s(bytes, elapsed),
                alg = algo.name(),
                run = i + 1,
                hex = to_hex32(&last_digest),
                size = format_bytes(bytes),
            );
        }

        if opts.iters > 1 {
            let mut speeds: Vec<f64> = times
                .iter()
                .map(|&t| mib_per_s(last_bytes, t))
                .collect();
            speeds.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let best = speeds.last().copied().unwrap_or(0.0);
            let worst = speeds.first().copied().unwrap_or(0.0);
            let avg = speeds.iter().copied().sum::<f64>() / (speeds.len() as f64);

            println!(
                "[{alg}  SUM] best={:.2} MiB/s  avg={:.2} MiB/s  worst={:.2} MiB/s  (iters={})",
                best, avg, worst, opts.iters, alg = algo.name()
            );
        }

        println!();
    }

    Ok(())
}
