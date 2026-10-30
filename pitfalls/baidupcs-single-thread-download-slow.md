# Baidu: downloads crawl at ~2.5 MB/s

**Symptom:** `BaiduPCS-Go download` of large files runs at 2–3 MB/s on the receiving host.

**Cause:** the config default `max_parallel 1` (one connection per file).

**Fix:** `-p 4` reached ~9.5 MB/s (7.0 TB in 205 h). ferry passes
`download_threads` (default 4).
