"""Build the animica_fastpow native extension.

The extension is OPTIONAL: if the C compiler is missing or the build fails, the
package still installs (just without the native `_fastpow` module), and
`animica_fastpow.available()` returns False so callers fall back to pure Python.
This is what makes it safe to be a *default* dependency of `animica` — a missing
platform wheel or toolchain can never break `pip install animica`.

    python setup.py build_ext --inplace   # local build (.so/.pyd in-place)
    pip install .                          # install into the active env
    python -m build --wheel                # platform wheel (ships in the miner release)

cibuildwheel / a real wheel build always has a compiler, so the wheel always
contains the native module; only the no-compiler *sdist* install falls back.
"""
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class OptionalBuildExt(build_ext):
    """Never fail the install because the native extension didn't compile."""

    def run(self):
        try:
            super().run()
        except Exception as exc:  # noqa: BLE001
            self._warn(exc)

    def build_extension(self, ext):
        try:
            super().build_extension(ext)
        except Exception as exc:  # noqa: BLE001
            self._warn(exc)

    @staticmethod
    def _warn(exc):
        import sys
        sys.stderr.write(
            "\n[animica-fastpow] native extension build skipped "
            f"({type(exc).__name__}: {exc}).\n"
            "    The miner will use the pure-Python hash loop. To get native\n"
            "    hashrate, install a C compiler (gcc/clang, or MSVC Build Tools)\n"
            "    and reinstall, or wait for a prebuilt wheel for your platform.\n\n"
        )


setup(
    packages=["animica_fastpow"],
    package_data={"animica_fastpow": ["*.pyi", "py.typed", "*.h"]},
    ext_modules=[
        Extension(
            "animica_fastpow._fastpow",
            sources=["animica_fastpow/fastpow.c", "animica_fastpow/sha3.c"],
            extra_compile_args=["-O3"],  # MSVC ignores -O3 and uses its own /O2
            optional=True,  # also hints setuptools the ext is non-essential
        )
    ],
    cmdclass={"build_ext": OptionalBuildExt},
)
