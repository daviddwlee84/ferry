# Baidu: command "succeeds" but nothing happened

**Symptom:** `BaiduPCS-Go meta/ls/rm` on a missing path exits 0.

**Cause:** errors such as `代码: 31066, 消息: 文件或目录不存在` are printed, not signalled.

**Fix:** adapters parse output (`parse_meta_size`, `parse_ls`) and verify sizes after
every transfer. Reported MD5 is labelled "可能不正确" by the tool itself; never use it.
