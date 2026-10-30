# Baidu: an uploaded marker file never appears on the remote

**Symptom:** a 0-byte `READY` marker uploaded with BaiduPCS-Go is missing from `ls`; the
receiver waits forever. BaiduPCS-Go printed no error and exited 0.

**Cause:** Baidu Pan does not store empty files.

**Fix:** every ferry remote object is non-empty (the manifest is the READY signal), and
each upload is followed by a `stat` size check.
