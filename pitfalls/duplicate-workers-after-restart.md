# Relay: units transferred twice / throughput halves after a restart

**Symptom:** two transfers of different units run at once; each gets half the bandwidth.

**Cause:** an earlier `nohup setsid ./worker.sh` loop was still alive; `pgrep -f
path/to/worker.sh` missed it (it was started as `./worker.sh`), and `pkill -f <pattern>`
over ssh killed the ssh shell itself because its own command line contained the pattern.

**Fix:** ferry takes a per-job, per-side `flock` (`<state_dir>/<side>.lock`, holding the
pid) and a second instance exits. Run under a supervisor (`ferry service`) and stop by
unit name / PID, never by `pkill -f`.
