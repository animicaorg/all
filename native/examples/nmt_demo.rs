//! NMT demo CLI for the native crate.
//!
//! Computes a Namespaced Merkle Tree (NMT) root for a randomly generated
//! dataset of namespaced leaves and reports timing/throughput.
//!
//! Build & run:
//!   cargo run --release --example nmt_demo -- --help
//!   cargo run --release --example nmt_demo --
//!   cargo run --release --example nmt_demo -- --leaves=1024 --leaf-size=1024 --ns=64
//!   cargo run --release --example nmt_demo -- --seed=1337 --print-sample=5
//!
//! Notes:
//! - This demo synthesizes data; it's useful for sanity and perf smoke tests.
//! - Namespace IDs are 8-byte identifiers uniformly sampled from [0, ns_count).

use std::collections::HashSet;
use std::env;
use std::time::{Duration, Instant};

use rand::{RngCore, SeedableRng};
use rand::rngs::StdRng;

use animica_native::nmt::nmt_root;
use animica_native::nmt::types::{Leaf, NamespaceId};

#[derive(Debug, Clone, Copy)]
struct Opts {
    leaves: usize,      // number of leaves
    leaf_size: usize,   // payload bytes per leaf
    ns_count: usize,    // distinct namespaces to draw from
    seed: u64,          // RNG seed for reproducibility
    print_sample: usize // show first K leaves (ns + prefix)
}

fn print_help_and_exit() -> ! {
    eprintln!(
r#"nmt_demo — compute NMT root for a random namespaced dataset

USAGE:
  nmt_demo [--leaves=N] [--leaf-size=B] [--ns=K] [--seed=U64] [--print-sample=K]

OPTIONS:
  --leaves=N         Number of leaves to generate. Default: 256
  --leaf-size=B      Payload size (bytes) per leaf. Default: 512
  --ns=K             Number of distinct namespaces to sample from. Default: 32
  --seed=U64         RNG seed for reproducibility. Default: 42
  --print-sample=K   Print first K leaves (namespace + first 16 bytes). Default: 0
  --help             Show this help.

EXAMPLES:
  nmt_demo --leaves=4096 --leaf-size=256 --ns=128
  nmt_demo --seed=1234 --print-sample=3
"#
    );
    std::process::exit(0);
}

fn parse_args() -> Result<Opts, String> {
    let mut args = env::args().skip(1).collect::<Vec<_>>();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help_and_exit();
    }
    let mut leaves = 256usize;
    let mut leaf_size = 512usize;
    let mut ns_count = 32usize;
    let mut seed = 42u64;
    let mut print_sample = 0usize;

    while let Some(arg) = args.first().cloned() {
        args.remove(0);
        if let Some(v) = arg.strip_prefix("--leaves=") {
            leaves = v.parse::<usize>().map_err(|_| format!("Invalid --leaves: {v}"))?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--leaf-size=") {
            leaf_size = v.parse::<usize>().map_err(|_| format!("Invalid --leaf-size: {v}"))?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--ns=") {
            ns_count = v.parse::<usize>().map_err(|_| format!("Invalid --ns: {v}"))?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--seed=") {
            seed = v.parse::<u64>().map_err(|_| format!("Invalid --seed: {v}"))?;
            continue;
        }
        if let Some(v) = arg.strip_prefix("--print-sample=") {
            print_sample = v.parse::<usize>().map_err(|_| format!("Invalid --print-sample: {v}"))?;
            continue;
        }
        return Err(format!("Unrecognized argument: {arg} (use --help)"));
    }

    if leaves == 0 { return Err("--leaves must be >= 1".into()); }
    if leaf_size == 0 { return Err("--leaf-size must be >= 1".into()); }
    if ns_count == 0 { return Err("--ns must be >= 1".into()); }

    Ok(Opts { leaves, leaf_size, ns_count, seed, print_sample })
}

fn ns_from_index(idx: u64) -> NamespaceId {
    // NamespaceId is 8 bytes; encode index as big-endian.
    let mut ns = [0u8; 8];
    ns[0] = ((idx >> 56) & 0xff) as u8;
    ns[1] = ((idx >> 48) & 0xff) as u8;
    ns[2] = ((idx >> 40) & 0xff) as u8;
    ns[3] = ((idx >> 32) & 0xff) as u8;
    ns[4] = ((idx >> 24) & 0xff) as u8;
    ns[5] = ((idx >> 16) & 0xff) as u8;
    ns[6] = ((idx >>  8) & 0xff) as u8;
    ns[7] = ( idx        & 0xff) as u8;
    ns
}

fn gen_random_leaves(opts: &Opts) -> (Vec<Leaf>, u128 /*total bytes*/) {
    let mut rng = StdRng::seed_from_u64(opts.seed);
    let mut leaves = Vec::with_capacity(opts.leaves);
    let mut total: u128 = 0;
    for _ in 0..opts.leaves {
        let ns_idx = if opts.ns_count == 1 {
            0u64
        } else {
            // sample uniformly in [0, ns_count)
            (rng.next_u64() % (opts.ns_count as u64))
        };
        let ns = ns_from_index(ns_idx);
        let mut data = vec![0u8; opts.leaf_size];
        rng.fill_bytes(&mut data);
        total += data.len() as u128;
        leaves.push(Leaf { ns, data });
    }
    (leaves, total)
}

fn hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = vec![0u8; 64];
    for (i, b) in bytes.iter().enumerate() {
        out[2*i]   = HEX[(b >> 4) as usize];
        out[2*i+1] = HEX[(b & 0x0f) as usize];
    }
    String::from_utf8(out).unwrap()
}

fn hex8(bytes: &[u8; 8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = vec![0u8; 16];
    for (i, b) in bytes.iter().enumerate() {
        out[2*i]   = HEX[(b >> 4) as usize];
        out[2*i+1] = HEX[(b & 0x0f) as usize];
    }
    String::from_utf8(out).unwrap()
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

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let opts = parse_args().map_err(|e| { eprintln!("error: {e}"); e })?;

    println!("== nmt_demo ==");
    println!("leaves        : {}", opts.leaves);
    println!("leaf size     : {} bytes", opts.leaf_size);
    println!("namespaces    : {}", opts.ns_count);
    println!("seed          : {}", opts.seed);
    println!("print sample  : {}", opts.print_sample);
    println!();

    let (leaves, total_bytes) = gen_random_leaves(&opts);

    if opts.print_sample > 0 {
        let sample = leaves.iter().take(opts.print_sample);
        println!("-- sample leaves --");
        for (i, leaf) in sample.enumerate() {
            let mut prefix = String::new();
            let take = leaf.data.len().min(16);
            for b in &leaf.data[..take] {
                prefix.push_str(&format!("{:02x}", b));
            }
            println!(
                "  #{:>4} ns=0x{} data[0..{}]={}{}",
                i,
                hex8(&leaf.ns),
                take,
                prefix,
                if take < leaf.data.len() { "…" } else { "" }
            );
        }
        println!();
    }

    // unique namespaces (sanity)
    let uniq: HashSet<[u8; 8]> = leaves.iter().map(|l| l.ns).collect();

    let t0 = Instant::now();
    let root = nmt_root(&leaves);
    let elapsed = t0.elapsed();

    println!("root          : 0x{}", hex32(&root));
    println!("size (data)   : {}", format_bytes(total_bytes));
    println!("elapsed       : {:.3}s", elapsed.as_secs_f64());
    println!("throughput    : {:.2} MiB/s", mib_per_s(total_bytes, elapsed));
    println!("uniq ns count : {}", uniq.len());
    Ok(())
}
