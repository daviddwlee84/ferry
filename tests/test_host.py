"""Host-wide transfer budget: shared slots bound the total of all ferry transfers."""
import threading
import time

import pytest

from ferry.adapters.local import LocalAdapter
from ferry.core import adapter_for
from ferry.host import HostBudget, load_host_budget
from ferry.job import job_from_dict


def _write(tmp_path, text):
    path = tmp_path / "host.toml"
    path.write_text(text)
    return path


def test_missing_config_means_no_budget(tmp_path):
    host = load_host_budget(tmp_path / "absent.toml")
    assert not host.active and host.effective("upload", 123) == 123


def test_rate_without_slots_defaults_to_one_transfer(tmp_path):
    host = load_host_budget(_write(tmp_path, 'upload_rate = "16Mbps"\n'))
    assert host.max_transfers == 1 and host.per_transfer("upload") == 2_000_000
    assert host.per_transfer("download") is None


def test_budget_is_split_across_slots_and_job_cap_still_wins(tmp_path):
    host = load_host_budget(_write(tmp_path, 'upload_rate = "16Mbps"\nmax_transfers = 2\n'))
    assert host.per_transfer("upload") == 1_000_000
    assert host.effective("upload", None) == 1_000_000
    assert host.effective("upload", 500_000) == 500_000  # stricter job cap
    assert host.effective("upload", 5_000_000) == 1_000_000  # host share


@pytest.mark.parametrize("text", ['max_transfers = 0\n', 'max_transfers = "2"\n', 'speed = 1\n'])
def test_invalid_host_config_is_rejected(tmp_path, text):
    with pytest.raises(ValueError):
        load_host_budget(_write(tmp_path, text))


def test_single_slot_serialises_transfers(tmp_path):
    host = HostBudget(max_transfers=1, slot_dir=tmp_path / "slots")
    active, peak, lock = [0], [0], threading.Lock()

    def transfer():
        with host.slot("upload", poll=0.01):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=transfer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_directions_have_separate_slots(tmp_path):
    host = HostBudget(max_transfers=1, slot_dir=tmp_path / "slots")
    with host.slot("upload"):
        got = []
        t = threading.Thread(target=lambda: got.append(host.slot("download").__enter__()))
        t.start()
        t.join(timeout=2)
        assert got == [0]  # download not blocked by an upload in progress


def test_adapter_for_applies_budget_and_holds_slot(tmp_path, monkeypatch):
    (tmp_path / "relay").mkdir()
    monkeypatch.setenv("FERRY_HOST_CONFIG", str(_write(tmp_path, 'upload_rate = "8Mbps"\n')))
    job = job_from_dict({"name": "h", "state_dir": str(tmp_path / "state"),
                         "units": {"root": str(tmp_path), "glob": "*"},
                         "remote": {"adapter": "local", "prefix": "/p", "upload_rate": "16Mbps",
                                    "options": {"root": str(tmp_path / "relay")}}})
    adapter = adapter_for(job, "sender")
    assert adapter.upload_rate == 1_000_000  # host 8 Mbps beats the job's 16 Mbps
    held = []
    real_put = LocalAdapter.put

    def spy(self, local, remote):
        slot = load_host_budget().slot_dir / "upload" / "slot-0.lock"
        held.append(slot.read_text().strip())
        return real_put(self, local, remote)

    monkeypatch.setattr(LocalAdapter, "put", spy)
    adapter = adapter_for(job, "sender")  # re-wrap with the spied method
    src = tmp_path / "f"
    src.write_bytes(b"x")
    adapter.put(src, "/p/f")
    assert held and held[0].isdigit()  # slot file carried our pid while transferring
