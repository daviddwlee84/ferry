# Logging and configuration

Load this when wiring loguru, adding a setting, or debugging "why is this
logging twice / why did my config not apply".

## The layering rule

**Applications configure logging. Libraries only emit.**

```python
# src/my_tool/core.py  - library code
import logging
logger = logging.getLogger(__name__)
logger.debug("greeting %s", name)      # silent until someone configures a handler
```

```python
# src/my_tool/_log.py  - application code, called by entry points only
from loguru import logger

def configure(level: str = "INFO", *, json: bool = False) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, serialize=json, backtrace=False, diagnose=False)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
```

Why this matters, concretely: a library that calls `loguru.logger.add()` at
import time installs a sink into the importing *program's* logger. Every app
that depends on you now has your format, your level, and your file path,
decided by an import it did not know it was making. The symptom is
"my app's logs changed format after I added a dependency", and it is
miserable to trace.

The same rule in one line: nothing under `src/` calls `logger.add()` or
`logger.remove()` except `_log.py`, and nothing calls `_log.configure()` except
`main()` and the ASGI lifespan.

## The three loguru facts that bite

1. **loguru installs a default stderr sink at import.** `logger.add(...)`
   without a preceding `logger.remove()` gives you every line twice. This is
   the single most common loguru bug.
2. **`diagnose=True` prints local variables** in tracebacks. Wonderful in
   development, a credential leak in production logs. The template sets it
   `False`; turn it on deliberately and never in a deployed environment.
3. **stdlib records do not reach loguru on their own.** Your dependencies
   (httpx, uvicorn, urllib3) use `logging`. Without an `InterceptHandler`
   bridge you get two differently-formatted streams interleaved on stderr. The
   bridge walks back up the frame stack so the reported source line is the
   caller's, not `logging/__init__.py`.

For machine-readable logs, `serialize=True` emits one JSON object per line —
which is what a log aggregator wants and what an agent parsing your output
wants.

## Configuration: defaults < .env < environment

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MY_TOOL_", env_file=".env", extra="ignore"
    )
    log_level: str = "INFO"
    log_json: bool = False
```

- **Prefix everything.** `MY_TOOL_LOG_LEVEL`, not `LOG_LEVEL`. Unprefixed names
  collide with other tools in the same shell, and the failure looks like your
  program ignoring its own config.
- **`extra="ignore"`**, so an unrelated variable in the environment is not a
  startup crash.
- Every field must be printable. `my-tool info` dumps the resolved settings as
  JSON — this is the `--print-config` surface from `verifiable-surfaces`, and
  it is how you settle "the flag isn't working" in one command instead of ten.
- When you add a setting, add it to `.env.example` in the same commit.
  `.env.example` is the tracked contract; `.env` is gitignored and must never
  hold anything you would not paste in a ticket.

## direnv

```bash
export VIRTUAL_ENV="$PWD/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
dotenv_if_exists
```

Two things people trip on:

- **direnv cannot change `PS1`.** You get no `(.venv)` in your prompt even
  though the environment is active. Check with `which python`, not the prompt —
  people re-activate over and over chasing a prompt that is never coming.
- `.envrc` needs `direnv allow` after every edit, including the first.

direnv is a convenience, never a requirement. `uv run <cmd>` works with no
activation at all, and that is the form to put in READMEs and CI.
