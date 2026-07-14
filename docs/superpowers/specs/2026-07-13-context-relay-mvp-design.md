# Context Relay MVP 設計規格

日期：2026-07-13

## 目的

驗證一個本機、唯讀的引導式 CLI，是否能在長對話、目標反覆改變、文件落後或多工作樹並存時：

1. 及早指出可能造成思考延遲或交接失真的風險。
2. 說明觸發原因，而不是只顯示不透明分數。
3. 估算不同整理方式的時間與信心度。
4. 產生可審閱、可追溯的 checkpoint 與乾淨任務交接包。
5. 不修改被掃描的專案。

MVP 的核心驗證問題是：一個沒有原對話上下文的接手者，只讀交接包後，能否辨認目前狀態、已驗證事項、進行中工作、下一個安全動作與未知事項。

## 使用者與使用時機

主要使用者是不會預先訂好最終目標、會在看到結果後持續修改方向的一般使用者。

適用情況：

- 對話逐漸變慢，但使用者不知道是否該壓縮或重開任務。
- 專案包含大量圖片、附件、工具輸出或重複狀態更新。
- 目標、設計或限制在同一任務內多次改變。
- README、PROJECT_STATUS、NEXT_STEPS、DECISIONS 或 AGENTS.md 可能已落後。
- 多個對話、分支或工作樹可能同時處理同一專案。
- 使用者隔一段時間回來，需要可靠地恢復工作。

短對話、單一步驟問題及沒有持久專案狀態的任務不應被頻繁提醒。

## 範圍

### MVP 包含

- Python 3 標準函式庫實作的手動 CLI。
- 唯讀掃描一個目標 Git 專案。
- 選用掃描一個本機 Codex JSONL session。
- 規則式風險分析與證據輸出。
- 規則式時間區間與信心度估算。
- 產生 assessment、report、checkpoint、handoff 與 manifest。
- 合成測試、錯誤輸入測試及 66day 真實樣本測試。
- 執行前後比對目標 repo 狀態，證明沒有寫入。

### MVP 不包含

- 背景常駐服務。
- Codex Hook 或自動壓縮控制。
- OpenAI API、雲端服務或遙測上傳。
- 自動提交或修改目標專案的 MD。
- 宣稱能直接縮短模型推理時間。
- 無人確認就把推測寫成專案事實。
- 圖形介面、多人權限或跨機同步。

## 使用介面

主要命令：

```text
context-relay scan --project <path> [--session <jsonl>] [--include-text]
```

預設只讀 session metadata。`--include-text` 明確啟用本機文字分析；即使啟用，也不會把內容送出本機。

命令結束時輸出本次 run 路徑、風險等級、主要原因、各處理方式 ETA，以及目標 repo 是否維持不變。

## 架構

### Collector

只讀蒐集：

- 專案絕對路徑、Git top level、目前 branch 與 HEAD。
- `git status --porcelain`、近期 commits、worktree 清單與 diff 統計。
- 已知專案文件是否存在、大小、修改時間及是否含可驗證的 HEAD 標記。
- Session 檔案大小、事件數、turn 數、壓縮事件、圖片或附件數量及可取得的時間資訊。
- 啟用文字分析時，蒐集使用者訊息與可辨識的目標改變訊號；原文不寫入可分享報告。

Collector 必須串流讀取大型 JSONL，不得把完整 session 載入記憶體。

### Risk Engine

產生四個相互獨立的維度，每項都包含 `level`、`score`、`confidence`、`evidence` 與 `limitations`：

- `context_pressure`：session 大小、turn、壓縮、圖片、附件及中斷等訊號。
- `state_drift`：文件缺失、文件沒有 HEAD 標記、文件標記與目前 HEAD 不符、未提交變更。
- `goal_drift`：啟用文字分析時，偵測近期使用者訊息中的改向或推翻訊號；只能標為可能性，不能宣告使用者真正意圖。
- `coordination_risk`：多工作樹、不同 branch 及同一文件可能成為共享寫入點的訊號。

總等級採四級：`low`、`moderate`、`high`、`critical`。總等級由最高維度與多維度同時升高的情況決定，不用單一不透明加總掩蓋原因。

### ETA Engine

輸出下列動作的時間區間與信心度：

- `quick_checkpoint`
- `checkpoint_and_compact`
- `clean_handoff`
- `full_reconciliation`

估算只涵蓋工具掃描、草稿與驗證時間。使用者確認、平台控制的壓縮、build 與 test 必須分開顯示。

第一版採保守規則：session 容量、附件數、文件漂移、未提交檔案、目標改向訊號及可選驗證工作增加區間。每次 run 保存實際階段耗時，供後續校準；MVP 不宣稱已個人化學習。

### Checkpoint Builder

只使用可追溯資料產生以下段落：

- Snapshot：時間、專案、branch、HEAD、dirty 狀態。
- Current objective：只有明確來源時才填入，否則標為待確認。
- Verified state：Git、現有狀態文件及工具驗證能支持的事實。
- Work in progress：未提交變更與明確的進行中標記。
- Decisions：只收錄有來源的決定，不把推測升格為決定。
- Next safe actions：最多五項，區分可直接執行與需使用者確認。
- Unknowns and conflicts：缺失、互相矛盾或可信度不足的內容。

### Verifier

在輸出完成前重新讀取目標 repo 的 HEAD 與 status：

- 若掃描期間狀態改變，run 標為 `stale`，不得聲稱交接包可安全使用。
- 確認必要輸出存在、JSON 可解析、checkpoint 含來源與未知事項欄位。
- 比較執行前後 Git 狀態，將唯讀結果寫入 manifest。
- 不以文件長度或固定標記存在作為「文件一定最新」的證明。

### Exporter

每次執行只在工具 repo 內產生：

```text
runs/<timestamp>/
  assessment.json
  report.md
  CHECKPOINT.md
  HANDOFF.md
  manifest.json
```

`HANDOFF.md` 要求新任務先驗證專案路徑與 HEAD，再讀 checkpoint，遇到不一致時停止並重新掃描。

## 資料與隱私

- 無網路請求，無 API key。
- 預設不輸出對話原文。
- 可分享輸出要將 home directory 等機器特定前綴替換成中性路徑標記。
- 原始證據可以記錄 event 類型、計數、雜湊或行號，不複製大型附件。
- 目標 repo 保持唯讀；所有輸出位於工具 repo。
- 解析失敗的 session 內容不得原樣輸出到錯誤訊息，以免洩漏私人文字。

## 錯誤處理

- 專案路徑不存在：以非零狀態結束，不建立可用交接包。
- 不是 Git repo：允許產生有限的檔案報告，但明確標記無法驗證 branch、HEAD 與 dirty 狀態。
- Session 不存在或無法讀取：退化為 repo-only 分析，降低 context 相關信心度。
- JSONL 某些行損壞：記錄損壞行數並繼續串流；損壞比例過高時將 session 分析標為不可靠。
- Git 命令失敗：保留 stderr 摘要與命令名稱，不把缺失資料當成乾淨狀態。
- 掃描期間 repo 改變：輸出標為 stale，建議重新執行。
- 輸出目錄碰撞：使用時間加隨機短碼，不覆寫既有 run。
- 不認得的事件 schema：計入 unknown event，不中止整次掃描。

## 測試策略

### 單元與合成測試

使用臨時目錄建立：

1. 小型乾淨 repo 與短 session，預期不產生高風險提醒。
2. 文件 HEAD 落後、dirty worktree 與多 worktree 訊號，預期 state／coordination 上升。
3. 大量 turn、圖片、compaction 與部分損壞 JSONL，預期 context 壓力上升且解析不中斷。
4. Session 缺失，預期 repo-only 報告與降低信心度。
5. 掃描中 HEAD 改變，預期 stale 交接包。
6. 含私人絕對路徑的資料，預期分享輸出完成遮蔽。

### 真實樣本測試

目標專案：`$PROJECT`

步驟：

1. 記錄執行前 branch、HEAD 與 porcelain status。
2. 掃描 repo 與一個既有大型 Codex session。
3. 記錄 Collector、分析、輸出及驗證各階段耗時。
4. 檢查每個高風險結論是否有證據。
5. 比對執行後 branch、HEAD 與 status。
6. 啟動一個隔離的新 Codex 執行，只提供 HANDOFF 與 CHECKPOINT，不提供本對話紀錄，檢查其回答品質。若環境無法建立隔離執行，該項必須標記為「尚未驗證」，不得以本對話中的人工回答代替。

### 通過標準

- 不修改 66day；執行前後 branch、HEAD 與 porcelain status 完全一致。
- 對大型本機 session 的掃描在 30 秒內完成。
- 每項風險判斷都包含可查證 evidence 與 limitations。
- 缺失或損壞 session 時仍能完成 repo-only 報告。
- 不把 build、test、使用者確認或平台壓縮時間混入整理 ETA。
- CHECKPOINT 不包含無來源的完成宣稱。
- 新接手者能從交接包回答：目前狀態、已驗證事項、進行中工作、下一個安全動作及未知事項。

若真實樣本只證明「能掃描」但不能讓新接手者正確繼續，MVP 判定失敗，不進入自動 Hook 階段。

## 預期限制

- Codex session schema 可能改變，因此解析器必須容忍未知事件。
- Metadata 能指出壓力，不能直接證明模型延遲的唯一原因。
- 不使用語言模型時，目標與決定的語意整理能力有限；MVP 應偏向標記未知，而不是自信猜測。
- 真實延遲受模型、網路與平台負載影響；MVP 驗證的是整理與恢復成本，不宣稱建立嚴格因果關係。
- 自動提醒、文件寫回及壓縮控制留待 MVP 通過後另行設計。

## 完成定義

MVP 完成需同時具備：

- 可從命令列執行的 scanner。
- 自動化測試通過。
- 66day 真實樣本測試報告。
- 一份實際 run 的五個輸出檔。
- 執行前後唯讀證明。
- 清楚列出通過、失敗與尚未證明的項目。
