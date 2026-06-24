"""The canonical Animica Studio example.

Run it locally (no chain, no node) — every call executes in a subprocess sandbox:

    ANIMICA_STUDIO_MODE=local python studio/examples/hello.py
    # or via the CLI:
    animica studio run studio/examples/hello.py::greet --name world
    animica studio run studio/examples/hello.py::fib --n 30
"""

import animica.studio as studio

app = studio.App("hello")


@app.function()
def greet(name: str = "world") -> str:
    return f"hello {name} from Animica Studio"


@app.function(timeout=60)
def fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


@app.function(image=studio.Image.debian_slim().pip_install("numpy"))
def noisy_mean(seed: int) -> float:
    import numpy as np  # installed via the image in distributed mode
    return float(np.random.default_rng(seed).standard_normal(100_000).mean())


if __name__ == "__main__":
    with app.run(mode="local"):
        print(greet.remote("world"))
        print("fib(30) =", fib.remote(30))
        print("fan-out fib:", fib.map(range(10)))
        # noisy_mean needs numpy in this interpreter to run in *local* mode;
        # in distributed mode the image installs it on the worker.
        try:
            print("noisy_mean:", noisy_mean.remote(0))
        except Exception as exc:  # numpy not installed locally is fine for the demo
            print("noisy_mean skipped locally:", type(exc).__name__)
