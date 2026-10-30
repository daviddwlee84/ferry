# Notebooks and widgets

Load this when adding a `notebooks/` directory, turning a notebook into a
runnable job, or packaging a custom visualization.

## marimo, not .ipynb

marimo notebooks are plain `.py` files. That single property is what makes them
compatible with everything else in this skill: they diff in review, they lint
with ruff, they import from your package, they run under pytest, and `git
blame` works. A `.ipynb` is a JSON blob with embedded output — it merges badly
and hides state.

Use the vendored `marimo-notebook` skill for the file format and cell rules
(top-level imports, no hidden state, reactivity).

## Where notebooks live

```
notebooks/          exploratory work. Imports the package; nothing imports it.
src/my_tool/        the package. Never imports from notebooks/.
```

The dependency arrow points one way. A notebook that other code imports is a
module wearing a costume — move it into `src/`.

Notebooks are also the honest form of usage documentation: an example that is
executed is an example that cannot rot. Point at them from the README.

## Dual mode: one file, two interfaces

Branch on the run mode exactly once, then let every cell downstream consume
`params` without knowing where it came from:

```python
is_script_mode = mo.app_meta().mode == "script"

if is_script_mode:
    params = tyro.cli(Params)          # uv run notebooks/train.py --epochs 50
else:
    mo.stop(form.value is None, mo.md("*Submit the form to run.*"))
    params = Params(**form.value)      # marimo edit notebooks/train.py
```

The UI and the batch job stay in step because there is only one file. For the
full treatment — sweeps, MLflow tracking, live training charts, launching on
remote jobs — use the `marimo-batch-mlflow` skill; do not re-derive it here.

## The two things that break

- **ruff will fight your notebook.** Cells are functions with deliberately
  unused names, and imports that cannot be hoisted. Without
  `per-file-ignores` for `notebooks/*` (`E402`, `F401`, `B018`), lint fails and
  the formatter can reorder cell contents. This is configured in the template.
- **A notebook with a PEP 723 header runs in its own environment.** That is
  what makes `uv run notebooks/example.py` work in a fresh clone — but it means
  the notebook cannot `import my_tool` unless your package is listed in that
  header's `dependencies`. Pick one: PEP 723 for standalone demos, or the
  project environment (`uv run marimo edit ...`) for notebooks that use your
  code.

## Widgets

A visualization that you reach for twice belongs in the package, not pasted
into a notebook:

```
src/my_tool/widgets/embedding_map.py    # an anywidget subclass
```

Then `mo.ui.anywidget(EmbeddingMap(...))` in any notebook, and the widget ships
with the package for downstream users. Use the vendored `anywidget` skill for
the `_esm` / `_css` mechanics — including making it legible in both light and
dark themes, which is the part people skip.
