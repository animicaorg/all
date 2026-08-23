# animica-serve

Serve Animica AICF inference jobs from anything with a CPU — **phones under
Termux included**. Torch-free and dependency-free: llama.cpp does the
inference, this package does the earning. Each job you win credits its full
estimated cost in ANM to your wallet address on the network's worker ledger.

The zero-install browser version of this worker lives at
**https://pool.animica.org/serve** — this package is the terminal lane: it
survives reboots in a `tmux`/`sv` session, doesn't need the screen on, and
uses llama.cpp's native ARM speed.

## Termux (Android)

```sh
pkg install python llama-cpp        # llama.cpp built for your phone's CPU
pip install animica-serve
animica-serve --address anim1yourwallet
```

The default model (Qwen 2.5 1.5B Instruct, Q4_K_M, ~1.1 GB) downloads once and
is cached; use `--model qwen2.5-0.5b` on older/low-RAM phones. Add
`--charge-only` to pause while unplugged (needs the Termux:API app +
`pkg install termux-api`). Keep Termux alive in the background with
`termux-wake-lock`.

## Anywhere else

Any Linux/macOS box works the same way. If you already run a llama-server or
Ollama, skip the model download and point at it:

```sh
animica-serve --address anim1… --openai-url http://127.0.0.1:11434/v1 --openai-model qwen2.5:1.5b
```

No `llama-server` binary? `pip install 'animica-serve[python-backend]'` uses
llama-cpp-python in-process.

## How earning works

Your worker registers your address (no keys ever leave your device), claims
jobs from the shared AICF queue on `rpc.animica.org`, answers locally, and
submits. Jobs are **raced** across several workers — the first good answer
wins and is credited (`animica-serve earnings --address anim1…` shows your
ledger). Losing races to faster desktop GPUs is normal; you win whenever you
are the fastest or only worker awake.
