# ferry：Config wizard、服務安裝引導與 pueue 文件

## Context

使用者希望不用研究 TOML 就能建立設定，了解如何背景／定期執行，並在 docs 中取得 pueue 的推薦用法；已選擇「Wizard＋服務安裝＋文件」的變更範圍。

目前實作已確認：
- **沒有 wizard**：`src/ferry/cli.py:71` 在命令分派前直接載入既有 job TOML。
- **已有服務安裝**：`ferry service` 支援 systemd user service／cron 的預覽、`--install`、`--uninstall`（`src/ferry/cli.py:49–56`、`src/ferry/service.py`）。不需要重做 installer。
- **沒有 ferry daemon**：`--follow` 是前景迴圈，由 supervisor 管理；systemd 執行 follow 模式，cron 執行非 follow sender／`receive --once`。
- docs 已簡述 unattended operation，但缺少 pueue 實際操作指南。

目標是補齊 onboarding，串接既有能力；維持 stdlib-only Python >=3.11、英文 Zensical 文件，以及現有 manifest／ledger 相容性。

## 1. 新增離線互動精靈

### CLI 與問答流程

新增：

```sh
ferry init                        # 預設建立 job.toml
ferry init --output /path/job.toml
ferry init --stdout               # 互動問答後，僅將 TOML 輸出至 stdout
```

在 `src/ferry/cli.py` 中將 `init` 與既有 job commands 分開註冊，並在 `load_job()` 前分派。新增 `src/ferry/wizard.py`，集中處理 prompts、設定建構、TOML rendering 與儲存；輸入／輸出可注入以便測試。不新增第三方問答套件或 TOML writer。

以選單、範例及合理預設引導：
1. Job 名稱、來源根目錄、持久化 state／staging 路徑。
2. 每個檔案／資料夾一個 unit，或進階的 `keys_from + members` 分組；tar 或單一原始檔。
3. Receiver 目的地、是否解壓；說明解壓會替換目的地中的相應 unit 路徑。
4. Backend 與必要非機密參數：
   - `local`：relay root。
   - `baidupcs`：CLI executable；登入另行完成。
   - `rclone`：已設定的 remote 名稱。
   - `ghrelease`：repository、draft release tag。
   - `rsync`：host、landing root；提供文件中既有的 local receiver override。
5. Job 專用 remote prefix。
6. 本機執行 sender／receiver，以及手動、systemd、cron 或 pueue；預設手動。
7. 預覽設定與後續指令，明確確認才儲存。

第一版處理常見配置，不把所有調校選項複製成第二份 schema；進階容量、時窗與刪除政策連至 configuration 文件。明寫安全預設：`delete_source = "never"`、`egress = "direct"`、`extract = false`。跨主機路徑必須標明屬於哪一端；不要求遠端目錄在目前機器存在，也不將遠端 `~` 誤展開成本機 home。

### 安全與驗證邊界

- 重用 `src/ferry/job.py` 的 `job_from_dict()` 及現有 size/window parsers（有提示對應欄位時），不另造一般設定解析器。
- 固定 schema TOML renderer 正確處理字串 escaping、Unicode、bool、arrays 與 nested tables；先經 `tomllib.loads()` round-trip，再呼叫 `job_from_dict()`。
- Backend 選項只做本地必要欄位檢查。**不呼叫 `make_adapter()`、doctor、登入或傳輸**；rsync adapter 建構本身可能執行 SSH 查詢。
- 不讀取、輸出或收集 backend credentials／session 檔案。
- 需要互動 stdin；非 TTY 回傳 2 並指向範例，不掛住等待輸入。`--help` 不受限制。
- Prompts／診斷送 stderr；`--stdout` 僅輸出完成的 TOML、不寫檔，後續指令需註明先儲存至所選路徑。
- 既有目標（含 symlink）一律拒絕覆寫，第一版無 `--force`；要求 parent 已存在，確認後 exclusive create。寫入失败只清理由本次建立的未完成檔案。
- EOF 回傳 2、Ctrl-C 回傳 130，無 traceback；確認前取消不產生檔案。
- 修正 CLI「每個 command 都有 JSON」的過度概括；wizard 不新增 JSON 介面。

## 2. 串接既有 systemd／cron 功能

重用 `src/ferry/service.py` 的 `systemd_unit()`、`cron_line()` 與 `command()`：
- Systemd 選項顯示既有 follow-mode unit 與正確 shell-quoted 的安裝命令。
- Cron 選項詢問 schedule，顯示 cron entry 與安裝命令；拒絕換行等會產生多筆設定的输入。
- Pueue 選項提供使用指南與適合所選 side 的提示，不啟動 daemon 或提交工作。
- **Wizard 不安裝、不 enable、不啟動任何服務。** 實際安裝仍是使用者明確執行既有 `ferry service … --install`，移除使用 `--uninstall`。
- 將 `src/ferry/cli.py` 的 `linger_enabled()` 查詢移至 systemd 安裝分支；單純預覽改為靜態 linger 說明，不執行 `loginctl`。
- 不自動啟用 linger、不加 sudo、不加入新的直接 subprocess 呼叫；不擴大為既有 service subprocess plumbing 的全面重構。

文件清楚列出現有指令，例如：

```sh
ferry service /absolute/job.toml --side receiver
ferry service /absolute/job.toml --side receiver --install
ferry service /absolute/job.toml --side sender --kind cron --schedule "0 2 * * *"
ferry service /absolute/job.toml --side sender --kind cron --schedule "0 2 * * *" --install
```

## 3. 英文文件與 pueue 推薦流程

修改：
- `docs/getting-started.md`：新增 wizard walkthrough，保留可重現的手寫 local demo。
- `docs/configuration.md`：說明 wizard 覆蓋範圍、安全預設、外部認證及 host-specific 路徑。
- `docs/operations.md`：補完整預覽／安裝／移除、backend PATH、非互動登入、systemd linger／logs、cron 重疊回傳 2，以及安裝可能立即開始傳輸的說明。
- 新增 `docs/pueue.md`；從 `docs/index.md`、operations 及 `README.md` 連入。保留 `zensical.toml` 的自動導覽和英文設定，不改 CI／部署設定。

### pueue 指南必須涵蓋

1. **主要推薦**：receiver 由 service manager 執行 `receive --follow`；sender 的非 follow 批次由 pueue 排隊。Sender 即使非 follow，仍可能因重試、讀取時窗、容量或 ACK 等待而長時間不結束。
2. 使用公開 `pueue` CLI：建立 dedicated group、設定 parallelism、`--label`、`--escape`、`--print-task-id`，以及 status／log／restart。無需安裝 repo 的 agent skill scripts。
3. ACK／relay 背壓情境下，**不可將 receiver 設為 sender 的 `--after`，也不可讓兩端共用只有一個 slot 的 queue**。同一 job/side 不要同時交由 pueue 與 service 管理；鎖是本機限制，不是跨主機分散式鎖。
4. 成功後串行 receive 的示範僅用於可完整放進 relay、保留來源的小型靜態批次；不要當一般推薦流程。
5. `receive --once` 只掃一輪；缺 manifest 或空間不足可能跳過工作。一般 receive 也可能被先前批次留下的 done marker 提前結束；重複批次優先持續 receiver。
6. `pueue wait` 完成／程序 exit 0 不保證資料全部交付；查看 Ferry receiver status／ledger，必要時比對預期資料。說明 SIGTERM 目前可能在未完成時回傳 0。
7. 重試保留 job、state、staging、relay；`restart` 預設新增 task，`--in-place` 會覆寫舊 log。
8. 在實際執行主機提交（需要時 SSH 進去）；task ID 屬於該 daemon，不能跨主機依賴。
9. 分清背景執行、重開機恢復及週期排程：pueue 持久化 queue 不等於自動續跑中斷程序；cron／timer 管週期，systemd 管常駐服務。不假設舊 Linux 一定可使用 systemd user manager。

將已確認的 stale done marker 與中止 exit status 問題記入 `TODO.md`，此變更僅文件提醒，不更改傳輸生命週期／協定。

## 4. 驗證

新增 `tests/test_wizard.py`，擴充 `tests/test_service.py`；重用 `tests/conftest.py` 的 local 測試模式：
- 五個 backend、兩種 unit selector、packing 選項、rsync receiver override。
- TOML round-trip：引號、反斜線、控制字元、空白、Unicode（含非 BMP）及 `{key}` templates。
- 安全預設、無效輸入重問、非 TTY、help、EOF／Ctrl-C、既有檔案／symlink／寫入失敗。
- `--stdout` 可直接解析成 TOML，無提示污染且不建立檔案。
- Mock 證明 wizard／service 預覽不呼叫 backend、installer、subprocess 或 linger probe；安裝測試全用 mocks／暫存路徑。
- Preview 與既有 service renderer 一致，並確認原本命令與安裝／移除介面沒有 regression。
- Bounded local smoke test：以 wizard 產生 config，傳送一個小型非空檔案、接收、比對 bytes，確認來源保留；不用 cloud 或實際 supervisor。
- 執行 `just check`、`just docs-build`（strict build＋HTML／Markdown／LLM exports checker）。本機預覽檢查新頁導覽與連結；不發布。
- pueue 範例對照本機已核對的 4.0.2 `--help`；不觸碰使用者佇列、不啟動實際 daemon。

## 不在本次範圍

不新增 ferry daemon、launchd installer、systemd timer installer、pueue integration dependency、雲端登入精靈或自動傳輸。不改 manifest／ledger 格式、backend checksum 信任策略或既有 SHA-256 驗證。不 commit／push，也不替使用者安裝服務。
