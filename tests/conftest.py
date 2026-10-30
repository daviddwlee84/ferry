import json
from pathlib import Path

import pytest

from ferry.job import job_from_dict


@pytest.fixture
def make_job(tmp_path):
    """Build a local-adapter job over a synthetic per-day source tree."""

    def build(days=("20200102", "20200103", "20200106"), *, part_size="64KiB",
              delete_source="never", drop_empty=True, extra_remote=None, receiver=None):
        src = tmp_path / "src"
        for i, day in enumerate(days):
            for kind in ("order", "trans"):
                d = src / kind / "ours" / day
                d.mkdir(parents=True, exist_ok=True)
                for sym in range(3):
                    (d / f"{sym:06d}.SZ.feather").write_bytes(
                        bytes([i, sym]) * (20_000 + 997 * sym))
            (src / "_manifests").mkdir(exist_ok=True)
            (src / "_manifests" / f"{day}.json").write_text(json.dumps({"date": day}))
        relay = tmp_path / "relay"
        relay.mkdir(exist_ok=True)
        data = {
            "name": "test",
            "state_dir": str(tmp_path / "state"),
            "units": {"root": str(src), "keys_from": "_manifests/*.json",
                      "members": ["order/ours/{key}", "trans/ours/{key}", "snapshot/ours/{key}",
                                  "_manifests/{key}.json"]},
            "sender": {"stage_dir": str(tmp_path / "stage"), "delete_source": delete_source},
            "receiver": receiver or {"dest": str(tmp_path / "dest" / "{key[0:4]}")},
            "remote": {"adapter": "local", "prefix": "/ferry/test", "part_size": part_size,
                       "options": {"root": str(relay), "drop_empty": drop_empty,
                                   **(extra_remote or {})}},
        }
        return job_from_dict(data), src, tmp_path

    return build


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}
