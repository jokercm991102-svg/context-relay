# Context Relay V2：語意目標交接與 A/B 驗證

日期：2026-07-13（2026-07-14 修訂確認事件語意）

## 目的

V1 已證明能快速偵測上下文壓力並安全交接 Git 狀態，但全新任務仍不知道使用者目前想完成什麼。V2 要在不使用雲端 API、不修改目標專案、不中斷一般對話的前提下，從本機對話與專案文件產生「有來源、可判斷可信度的目前目標候選」。

核心驗證問題：使用固定的 session 快照與固定 Git target 時，即使原始目標後面出現澄清問題與「核准 V2 規格」，全新 Codex 是否仍能從 V2 交接包說出原始目標、知道規格已核准並採取下一個安全動作，而 V1 仍必須詢問使用者。

## 已比較方案

### 全自動推斷

直接把模型或規則認為的目標寫入 checkpoint。摩擦最低，但「第二種」「核准」或文件衝突時容易把錯誤推測當成事實。

### 純人工輸入

每次要求 `--objective`。最可靠，但使用者已經在對話裡說過目標時仍要重複輸入，沒有解決普通使用者的負擔。

### 引導式候選（採用）

工具先找最新明確提示與結構化 MD，附來源、信心度和衝突原因。證據明確時直接生成可用候選；含糊、指涉或衝突時標示必須確認。使用者仍可用 `--objective` 覆寫並明確確認。

## 範圍

### V2 包含

- `--include-text` 明確啟用本機提示詞分析。
- 從 Codex JSONL 讀取 user／agent 對話事件，但只保留有限的近期文字視窗。
- 辨識明確目標、澄清／修正、確認、控制動作、依賴前文的選擇／指涉，以及一般／狀態訊息。
- 讀取已知 MD 的特定段落，並檢查其 Git HEAD 可信度。
- 以固定優先序選出目標候選並記錄來源雜湊、信心度、衝突與是否需要確認。
- `--objective <text>` 作為使用者明確確認／覆寫。
- 將目標候選寫入本機 `CHECKPOINT.md` 與結構化 `assessment.json`。
- 固定輸入的 V1/V2 A/B 測試、合成回歸測試與隔離 Codex 接手測試。

### V2 不包含

- 自動修改目標專案的 README、PROJECT_STATUS 或其他 MD。
- 背景 Hook、常駐提醒或平台壓縮控制。
- OpenAI API 或任何網路語意服務。
- 嘗試理解所有自然語言指涉。
- 從普通 README 自動宣告目前任務。
- 自動推斷未記錄的產品決策。

## 輸入與隱私

- 預設仍是 metadata-only；沒有 `--include-text` 時不得讀取或輸出提示內容。
- `--include-text` 表示使用者同意在本機解析提示，並允許將蒸餾後的目標候選寫入本機交接包。
- 原始完整對話、assistant 回答及大段 MD 不得寫入 bundle。
- `assessment.json` 只保存候選文字、來源類型、來源 SHA-256、信心度與理由；不保存完整對話視窗。
- `report.md` 只顯示是否找到目標、信心度與衝突，不複製目標原文。
- `CHECKPOINT.md` 保存目標候選，因為新任務必須能讀到它。
- 所有機器絕對路徑繼續遮蔽。

## 對話解析

新增 bounded dialogue collector，逐行讀取 JSONL 並只保留最後 40 個 user／agent 文字事件，每個事件最多 4,000 字元。

User message 正規化規則：

1. 若包含 `## My request for Codex:`，只取該標題後方內容。
2. 移除單獨的 `<environment_context>`、權限資訊及 response-annotation 包裝。
3. 合併多餘空白但保留原語言。
4. 空訊息不進入候選。

事件分類規則：

- 目標：含有可單獨理解、可執行成果的 user 請求，例如「建立下版功能並實測差距」。
- 澄清／修正：針對既有目標、設計或規則提出問題、異議或限制，例如「為什麼要忽略好的與核准？」。單純疑問不取代目前目標；含「請修改／改成／不要」等明確行動要求時，記為目前目標的 amendment，而不是失去上層目標的新專案。
- 確認：`核准`、`確認`、`approved`、`好`、`好的`、`可以`、`ok`、`yes` 等回應前文的訊息。確認事件必須保留並嘗試連結標的，不得直接忽略，也不得把確認詞本身當作目標。
- 控制動作：`開始`、`繼續`、`開始實測` 等要求推進既有目標的訊息。它可與確認同時存在，但不是獨立產品目標。
- 選擇／指涉：`第一種`、`第二種`、`用這個`、`照這個`、`那就這樣` 等無法單獨理解的訊息。
- 一般／狀態：只提供進度或聊天資訊、沒有要求改變成果的訊息。

確認解析規則：

1. `核准 <標的>`、`確認 <標的>` 或 `approved <target>` 是強確認；標的唯一時記為 `approved`。
2. 只有 `核准` 時，僅在近期事件中恰好有一個待確認標的時連結；零個或多個標的一律標示歧義並要求使用者確認。
3. `好`、`好的` 與 `ok` 是 acknowledgement。它們可表示「已收到／可繼續一般流程」，但不得單獨升級成 `approved`，也不得作為破壞性操作、對外操作或擴大範圍的授權。
4. `可以`、`yes` 只有在緊接一個可唯一解析的二元確認問題時才確認該標的；否則視為 acknowledgement 並記錄限制。
5. `開始`、`繼續` 只更新 `requested_action`；必須先有唯一 active objective。`核准並開始實測` 同時產生 approval 與 `start` 控制動作。
6. 確認標的解析優先使用 user 訊息內明確名稱，其次才使用近期唯一待確認標的。Assistant 文字只可用來辨識「是否提出一個待確認項目」及其雜湊，不得成為使用者目標文字來源。

目標選擇規則：

- 最新可單獨執行的 user 目標是第一候選；後續澄清、確認、控制或一般訊息不取代它。
- 明確修正以 amendment 附加在 active objective；若訊息明確取消或改成另一個成果，才建立新的 active objective。
- 確認可更新候選的 `confirmation_status` 與下一動作，但不降低已明確目標的信心度。
- 最新訊息若為無法解析的選擇／指涉，候選標示 `requires_confirmation=true`，不得猜測 assistant 選項內容。
- 有多個可能確認標的時，保留 active objective，但確認狀態標示歧義，不得宣告已核准。

## MD 解析與角色

只讀取目標 Git root 內、大小不超過 1 MiB 的已知文件：

- `AGENTS.md`：長期規則，不作目前目標。
- `README.md`：產品方向，不作目前目標。
- `PROJECT_STATUS.md`：只讀取 `Current objective`、`目前目標`、`目前任務` 或 `Active goal` 段落。
- `NEXT_STEPS.md`：最多讀取前五個未完成項目，作下一步證據。
- `DECISIONS.md`：只作已記錄決策證據，不由 V2 重寫。

若文件有 HEAD marker：

- 與目前 HEAD 相符：可作中等以上可信來源。
- 與目前 HEAD 不符：不得成為目前目標，只列為衝突。
- 沒有 marker：可作低可信候選，必須顯示限制。

Symlink 若解析到 Git root 外不得讀取。

## 來源優先序與衝突

由高至低：

1. `--objective` 使用者明確覆寫：`confirmed / high`。
2. 最新明確可執行 user 目標：`inferred / high`；後續可解析的確認事件只更新狀態，不降低信心度。
3. HEAD 相符的 PROJECT_STATUS 結構化目標：`documented / medium`。
4. 無 HEAD marker 的結構化目標：`documented / low`。

README 永遠不能提升成目前目標。

Prompt 與 MD 不同時，prompt 成為候選，但 `requires_confirmation=true` 並列出 MD 衝突。HEAD 不符的文件只能列為 stale evidence。確認事件只更新其唯一標的的狀態，不參與上述候選優先序。

## 資料模型

新增：

```text
DialogueEvent
  role
  normalized_text
  source_hash
  sequence
  event_kind

ConfirmationEvent
  source_hash
  kind
  target_label
  target_hash
  status
  requested_action
  requires_confirmation
  reasons[]

DocumentSection
  document
  heading
  text
  recorded_head
  head_matches

ObjectiveCandidate
  text
  source_kind
  source_hash
  status
  confidence
  requires_confirmation
  amendments[]
  confirmation_status
  reasons[]
  conflicts[]

SemanticEvidence
  objective
  confirmation
  next_steps[]
  dialogue_events_examined
  documents_examined[]
```

沒有 `--include-text` 且沒有 `--objective` 時，objective 必須維持 `None`，與 V1 隱私行為相同。

## CLI 與輸出

沿用：

```text
context-relay scan --project <path> --session <jsonl>
```

新增／擴充：

```text
--include-text
--objective <confirmed objective>
```

`--objective` 不要求 `--include-text`，因為內容由使用者直接提供。

輸出仍維持五個檔案：

- `assessment.json`：加入 `semantic`，包含 candidate、confirmation 與來源證據。
- `report.md`：加入目標偵測狀態、信心度及是否需要確認，不顯示原文。
- `CHECKPOINT.md`：`Current objective` 顯示候選、amendments、確認狀態、信心度與來源雜湊；確認詞本身不得出現在目標欄位。
- `HANDOFF.md`：候選無衝突時允許新任務依目標繼續；需要確認時先詢問使用者。
- `manifest.json`：加入 text-analysis、objective-status 與 semantic-input hash。

## 錯誤處理

- Session 缺失或損壞：退化到結構化 MD；降低信心度。
- 對話文字超過上限：截斷單一事件並記錄限制，不保留整段。
- 找不到明確 prompt：使用可信 PROJECT_STATUS；否則維持未知。
- 最新訊息為指涉：不得猜選項內容。
- 確認找不到唯一標的：保留目前目標，但將確認狀態列為 ambiguous 並要求確認。
- Prompt／MD 衝突：prompt 候選必須確認。
- 文件在掃描中改變：沿用 repo stale 規則，交接包不可直接使用。
- 目標包含機器路徑：延續 `$PROJECT`／`$HOME` 遮蔽。

## 自動化測試

至少涵蓋：

1. 最新明確可執行請求可成為 high-confidence 候選。
2. 後續「為什麼……？」澄清不會取代 active objective。
3. `核准 V2 規格` 連結唯一標的並設為 approved，不把確認詞當目標。
4. 裸 `核准` 在唯一待確認標的時可連結；多個標的時必須標示歧義。
5. `好的` 只記為 acknowledgement，不會自行授權破壞性、對外或擴大範圍操作。
6. `核准並開始實測` 同時保留 approval 與 start 動作。
7. `第二種` 標記需要確認，不猜測內容。
8. Response annotations 只保留 `My request for Codex` 後方請求。
9. 沒有 prompt 時，HEAD 相符 PROJECT_STATUS 可作候選。
10. Stale PROJECT_STATUS 不可成為候選。
11. Prompt 與 MD 衝突時 prompt 勝出但需要確認。
12. README 內容不會成為目前目標。
13. 未啟用文字分析時 bundle 不含 prompt 文字。
14. `--objective` 產生 confirmed/high 候選。
15. 外部 symlink 不被讀取。
16. 既有 15 個 V1 測試全部繼續通過。

## 固定輸入 A/B 驗證

為避免輸入變動造成假差距：

1. 在實作前、收到本規格核准後，把目前 Codex session 複製到 `/tmp`，記錄 SHA-256，不提交原文。快照必須包含原始目標、後續澄清與 `核准 V2 規格`。
2. 建立固定在 V1 commit `b09e5f5` 的 detached Git target。
3. V1 與 V2 都掃描同一個 target、同一份 session snapshot、輸出到不同 `/tmp` 目錄。
4. Scanner 各跑三次，報告 median wall time。
5. 對兩份 bundle 各啟動一次相同模型、相同 schema、唯讀 ephemeral Codex。

已知 ground truth 的 active objective 來自本輪明確 user prompt：

```text
建立下版功能並核准實測，最好能測出優化的差距
```

快照末端的 `核准 V2 規格` 是對 V2 規格的 approval，不是取代上述 active objective 的新目標。預期 confirmation status 為 `approved`；中間的澄清問題也不得取代 active objective。

比較指標：

- 目標召回：V1 應為 unknown；V2 應符合 ground truth，並將 V2 規格標示為 approved。
- 是否需要詢問「目前目標是什麼」。
- 新任務能否直接說出下一個安全動作。
- Objective completeness rubric：目標、驗證要求、差距比較三個意圖元素，各一分。
- Scanner median wall time 與 V2 額外成本。
- Bundle byte size。
- 隔離 Codex wall time、token usage、tool-call 數；單次模型時間只列觀察值，不宣稱統計顯著。
- Git target 前後 branch、HEAD、status 是否完全相同。

## 通過標準

- V2 自動候選包含 ground truth 三個意圖元素，分數 3/3。
- V2 不會把後續澄清問題、`好的` 或 `核准 V2 規格` 當成 active objective，且能保留正確的 confirmation status。
- V2 隔離接手者不再要求使用者重新說明目前目標。
- V1 隔離接手者仍維持 unknown，形成可觀察差距。
- V2 對固定 session 的 scanner median 仍低於 1 秒，且相對 V1 額外成本低於 100%。
- 目標 Git target 前後完全不變。
- 所有自動化測試通過。
- 若準確度提高但時間或隱私失敗，V2 不算通過。

## 完成定義

- V2 程式與回歸測試。
- 固定 session hash 與固定 target commit 的 A/B 報告。
- V1/V2 實際 bundle。
- 兩次隔離接手的結構化輸出。
- 誠實列出 PASS、FAIL、UNVERIFIED 與下一個限制。
