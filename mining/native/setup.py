"""Build the animica_fastpow native extension.

Metadata lives in pyproject.toml; this file declares the C extension.

    python setup.py build_ext --inplace   # local build (.so/.pyd in-place)
    pip install .                          # install into the active env
    python -m build --wheel                # platform wheel (ship in the miner release)

On Windows this compiles with MSVC (VS Build Tools); the resulting
animica_fastpow/_fastpow.*.pyd is what was missing from the 0.1.1 archive.
"""
from setuptools import Extension, setup

setup(
    packages=["animica_fastpow"],
    package_data={"animica_fastpow": ["*.pyi", "py.typed", "*.h"]},
    ext_modules=[
        Extension(
            "animica_fastpow._fastpow",
            sources=["animica_fastpow/fastpow.c", "animica_fastpow/sha3.c"],
            extra_compile_args=["-O3"],  # MSVC ignores -O3 and uses its own /O2
        )
    ],
)
