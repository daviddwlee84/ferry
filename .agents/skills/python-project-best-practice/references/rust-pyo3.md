# Rust extensions with PyO3 and maturin

Load this when adding a compiled extension, or when a Python change to a mixed
project mysteriously has no effect.

Scope: enough to lay the project out correctly and avoid the traps. For deep
PyO3 work — lifetimes, GIL handling, error conversion, async bridging — read
<https://pyo3.rs/> directly.

## When it is worth it

When you have profiled, the hot loop is genuinely CPU-bound, and vectorising it
in NumPy or Polars is not possible. Rust buys you nothing on I/O-bound work,
and it costs you: a toolchain requirement for contributors, cross-compilation
for releases, and a second language in every code review.

## Layout

```
pyproject.toml          build-backend = "maturin"
rust/
  Cargo.toml            [lib] name = "_rust", crate-type = ["cdylib"]
  src/lib.rs
src/my_tool/
  __init__.py           the Python API; re-exports from _rust
  _rust.pyi             HAND-WRITTEN stubs for the compiled module
  core.py               the pure-Python path
```

```toml
[tool.maturin]
python-source = "src"
module-name = "my_tool._rust"
manifest-path = "rust/Cargo.toml"
features = ["pyo3/extension-module"]
```

Keep the compiled module private (`_rust`) and export a Python-level API around
it. That leaves you free to change the Rust signature, add a pure-Python
fallback, or drop Rust entirely without breaking your users' imports.

## The trap that costs an afternoon

**`uv sync` does not rebuild the extension after you edit `rust/`.** It sees
the package as already installed, does nothing, and you keep running the stale
binary. There is no warning. Your Rust change appears to have had no effect,
and the natural next move — adding print statements — also has no effect,
because those are in the stale binary too.

```bash
uv sync --reinstall-package my-tool     # rebuilds; only needs uv
uv run maturin develop --uv             # faster incremental loop
```

Put it behind `just build-rust` so nobody has to remember, and make it the
first thing you check when a Rust change "does nothing".

## Type checkers cannot see a .so

`ty` and mypy report `Module my_tool has no member _rust`, and editors give you
no completion. The fix is a hand-written stub next to the module:

```python
# src/my_tool/_rust.pyi
def greet(name: str, times: int = 1) -> list[str]: ...
```

Nothing generates this file and nothing verifies it matches the Rust. Update it
in the same commit as the `#[pyfunction]` signature, and keep a test that calls
through the binding so a mismatch fails somewhere.

## abi3

```toml
pyo3 = { version = "0.23", features = ["extension-module", "abi3-py311"] }
```

`abi3-py311` builds one wheel that works on 3.11 and every later version,
instead of a wheel per interpreter version. It restricts you to the stable C
API, which is almost always fine and cuts your release matrix by a factor of
four.

## Testing and releasing

Test the binding against the Python implementation — property-based if you can,
so the two cannot silently diverge:

```python
def test_rust_matches_python():
    assert _rust.greet("ada", 2) == core.greet("ada", 2)
```

For wheels, `PyO3/maturin-action` builds the matrix (Linux via manylinux
containers, macOS, Windows) and publishes on tag. Contributors now need a Rust
toolchain to install from source — say so in the README's getting-started, or
the first bug report will be a failed `uv sync`.
