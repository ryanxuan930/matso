# **構建軍事人工智慧指揮參謀：多智能體混合架構、動態仲裁機制與深度領域適應之戰略剖析**

在當前人工智慧與軍事決策科學的交匯點上，單一大型語言模型（Large Language Models, LLMs）的應用正迅速演進為由多個特化模型協同運作的複雜系統。針對將多個配備不同領域知識（透過檢索增強生成，RAG）的小型模型組合成「智庫」，並指定單一模型擔任「仲裁統合」角色的構想，學術界與產業界的前沿研究已證實此架構不僅具備極高的工程可行性，更代表了次世代決策支援系統的核心範式。本報告將深入探討此一被稱為「智能體混合架構（Mixture-of-Agents, MoA）」的底層機制，廣泛梳理軍事與跨領域的先驅應用案例，並系統性地論證除了常規的RAG技術外，如何透過連續預訓練（CPT）、監督式微調（SFT）以及檢索增強微調（RAFT）等先進領域適應（Domain Adaptation）方法，從根本上強化人工智慧對特定戰略與戰術領域的深度理解。

## **一、 多智能體混合架構與動態仲裁機制之理論與實踐**

將多個專業化小型模型結合並由核心模型進行仲裁的設計，在計算機科學文獻中被正式定義為智能體混合架構（Mixture-of-Agents, MoA）。此架構從根本上不同於傳統單一模型內部的專家混合（Mixture-of-Experts, MoE）機制。MoE 是在單一模型的單次前向傳播（Forward pass）過程中，將神經網路內部的權重代幣（Tokens）路由至特定的子網路；相對地，MoA 則是一個多模型推論系統，由多個獨立且完整的語言模型組成，彼此透過自然語言的上下文進行互動與資訊融合1。

### **智能體混合架構的運作邏輯與協作性現象**

MoA 架構的設計靈感源自於大型語言模型所展現的「協作性（Collaborativeness）」現象。實證研究指出，當語言模型在生成回應時，若能獲得其他模型先前的輸出作為輔助參考資訊，其最終生成的品質往往會顯著提升，即使這些參考輸出的品質參差不齊或存在部分錯誤3。基於此特性，標準的 MoA 架構通常被設計為分層結構，並區分為兩種核心角色。第一種角色為「提案者（Proposers）」，相當於參謀系統中的各領域專業智庫。在同一層級中，多個提案者模型會同時接收使用者的原始指令，並結合各自配置的專業 RAG 資料庫（如情報檢索、後勤數據、氣象資料），獨立並行地生成候選方案或評估報告2。
第二種角色為「聚合者（Aggregators）」或「仲裁者（Arbiter/Judge）」。聚合者負責接收所有提案者的輸出，將其作為擴展的上下文脈絡進行綜合分析。仲裁模型的工作並非簡單的多數決投票，而是進行深度的批判性綜合，識別各提案者報告中的最強論點，利用第一性原理（First principles）解決事實衝突，最終生成一份連貫且高品質的決策建議1。在多項客觀評測中，例如 AlpacaEval 2.0，採用多個開源小型模型作為提案者並以單一模型聚合的 MoA 系統，其勝率高達 65.1%，顯著超越了 GPT-4 Omni 等頂尖單一模型的表現，證明了群體智慧在模型推理上的有效性1。

### **克服身份偏見與導入數學化動態辯論機制**

儘管 MoA 架構效能卓越，但在軍事戰略等高風險情境中，簡單的單次聚合可能不足以消除所有邏輯謬誤。文獻指出，語言模型存在「身份偏見（Identity bias）」，即模型傾向於同意自己先前的輸出或同系列模型的觀點，這會導致多智能體系統產生高度的內部相關性，從而削弱了古典「孔多塞陪審團定理（Condorcet Jury Theorem）」中關於多數決能收斂於正確答案的獨立性假設9。
為了解決此問題並優化運算成本，先進的架構引入了多智能體辯論（Multi-Agent Debate）與統計學上的動態停止規則。具體而言，系統會指派特定模型扮演「挑戰者（Challenger）」角色，針對提案者的方案進行壓力測試與對抗性審查，尋找戰術盲點或虛假關聯10。在多輪辯論的過程中，仲裁模型（Judge）會在每一輪結束後，針對當前各方立場的收斂程度給出一個介於 0 到 1 之間的共識分數。此分數的動態變化可被建模為隨時間變化的 Beta-Binomial 混合分佈，並透過計算對數概似比（Log-likelihood ratio）累積值來驅動沃爾德順序機率比檢定（Wald's Sequential Probability Ratio Test, SPRT）11。當系統透過柯爾莫哥洛夫-斯米爾諾夫檢定（Kolmogorov-Smirnov test）偵測到意見分佈已趨於穩定，或對數概似比突破預設的決策邊界時，仲裁機制便會自動終止辯論並輸出結論。這種結合嚴謹統計機制的動態框架，既能確保決策的正確性被有效放大，又能避免在簡單問題上浪費過多的推理算力11。

| 系統架構特性 | 單一大型語言模型 \+ 基礎 RAG | 智能體混合架構 (MoA) \+ 專業化多域 RAG |
| :---- | :---- | :---- |
| **知識來源廣度** | 受限於單一檢索管道的文本排序 | 由多個特化提案者平行檢索多域資料，涵蓋面廣3 |
| **視角多樣性與除錯** | 依賴單一模型的內部權重，易受固有偏見影響 | 提案者提供異質視角，仲裁者負責交叉比對與衝突解析2 |
| **複雜推理效能** | 處理多層次邏輯時容易產生幻覺或上下文遺忘 | 透過層疊架構與同行評審（Peer-review）辯論機制，推理深度顯著提升10 |
| **推論延遲與運算成本** | 較低，僅需單次前向傳播與生成過程 | 較高，延遲取決於最慢的提案者，且需額外計算仲裁者的聚合步驟1 |

## **二、 軍事決策與跨領域智能參謀之先驅應用案例**

使用者詢問是否已有類似的作法或案例。事實上，將多智能體協同與特定領域資料庫相結合的概念，不僅在國防軍事領域已有深度的實驗與部署，在金融、智慧城市與物流管理等高複雜度領域亦取得了豐碩的成果。這些案例為打造 AI 指揮參謀提供了極具價值的參考藍圖。

### **宏觀戰略與戰役層次：sdLM 雙模型架構與 IBM 國防模型**

在宏觀軍事戰略規劃方面，美國的研究團隊構建了「戰略準則語言模型（Strategic Doctrine Language Models, sdLM）」，這是一個專門設計用來處理資訊密集型軍事任務的特化系統。有別於依賴單一全能模型，sdLM 採用了分工明確的雙模型架構。其核心包含一個 700 億參數的「大戰略模型（GIPFEL-I）」，具備高達 32,768 個代幣的上下文視窗，能夠同時處理並交叉分析長達 200 頁的多國軍事準則與地緣政治情報；另一個則是 300 億參數的「兵棋推演模型（SANDKASTEN-I）」，專門負責高頻率的動態場景生成與紅藍軍對抗裁決14。在針對 127 個歷史與合成戰略場景的專家雙盲評估中，該系統獲得了 8.42/10 的高分，其地緣政治預測準確率更達到 73.2%，並能維持高達 91% 的軍事準則一致性14。
與此同時，商業界亦積極投入國防 AI 的開發。IBM 近期發布了專為國防與國家安全應用微調的「IBM Defense Model」。該模型為了避免攝入網際網路上錯誤或具誤導性的軍事資訊，特別選擇了開源情報機構 Janes 所提供、經過人類專家嚴格審查的結構化資料進行訓練15。這種模型具備對軍備規格、戰術術語與任務脈絡的深刻理解，旨在透過 API 整合至美國國防部的「聯合全領域指揮與管制（CJADC2）」架構中，作為情報官與指揮官的決策支援中樞15。

### **戰術空間感知與跨域協同：Geo-Commander 與 DoDAF 整合框架**

在戰術層次，傳統語言模型常因缺乏實體空間的幾何感知而無法給出具體的部隊調度建議。為了克服此限制，學界提出了「Geo-Commander」多任務指揮官智能體框架。該系統將真實的戰場地理環境編碼為六角形網格地圖（Hexagonal grid map），這種結構具備均勻的相鄰關係與連續的方向性，極適合用於戰鬥模擬與空間推理16。Geo-Commander 結合了視覺語言模型（VLLM）的影像理解能力，以及「推理-行動（ReAct）」機制，使 AI 能夠在初步篩選空間狀態後，將地理限制直接納入戰術規劃的推理鏈中，從而在掩蔽點、狙擊點與突擊點的選擇上，展現出超越傳統規則驅動系統的勝率與作戰效能16。
此外，針對多軍種聯合行動，有研究提出了一種基於美國國防部架構框架（DoDAF）的 LLM 驅動決策支援系統。該系統利用 RAG 技術，將指揮官的自然語言指令與 DoDAF 標準化的視圖模型（如 CV-2 能力分類模型）進行語義對齊，並引入了皮特里網（Petri net）進行靜態邏輯驗證，確保生成的行動方案在結構上沒有衝突；隨後再利用蒙地卡羅（Monte Carlo）模擬進行動態效能評估18。此種結合語義理解與嚴格數學邏輯驗證的混合方法，大幅降低了 AI 在生成戰術指令時產生幻覺的風險18。

### **跨領域複雜系統管理：智慧倉儲、城市治理與金融標註**

除了純軍事應用，多智能體仲裁架構在其他高複雜度領域亦展現出卓越的適應性：

1. **多智能體智慧倉儲（MAIW）：** NVIDIA 推出的 MAIW 藍圖利用多個特化智能體分別監控設備運作、勞動力協調、安全合規與需求預測，並由一個核心規劃智能體（Planner Agent）進行路徑分配與仲裁。該系統結合了混合 RAG 技術，將物聯網（IoT）遙測數據與標準作業程序（SOP）文件轉化為即時、可解釋的營運情報19。
2. **智慧城市管理：** 在都市規劃領域，整合 LLM 智能體與現有城市資訊系統的多智能體架構，在處理複雜都市查詢的任務路由準確率達到 94-99%。其分散式解決問題的能力，顯著優於單一語言模型，將原本需要數天的規劃評估時間縮短至數小時內20。
3. **金融與企業級資料標註（MAFA）：** MAFA 是一個針對企業級標註工作流程的生產部署系統。面對金融服務中海量且具備模糊語義的客戶話語，該系統利用多個專業的排名智能體（Ranker agents）平行運作產生候選標註，最後交由一個「法官智能體（Judge agent）」進行共識裁決。此機制使其在內部意圖分類資料集上的 F1 分數較傳統單智能體方法提升了 16.9%4。

| 應用領域 | 代表性框架 / 系統名稱 | 多智能體角色設計與運作機制 | 系統成效與具體優勢 |
| :---- | :---- | :---- | :---- |
| **軍事戰略規劃** | sdLM 戰略準則模型14 | 採用 70B 戰略模型與 30B 兵推模型協同，處理長達 32,768 代幣的跨國準則。 | 戰略品質達 8.42/10，地緣政治預測準確率 73.2%，確保高度準則一致性。 |
| **戰術兵力調度** | Geo-Commander 框架16 | 視覺模型解讀六角形戰場網格，結合 ReAct 機制進行空間特徵與戰術的對抗性推理。 | 解決 LLM 缺乏幾何感知的缺陷，顯著提升接戰勝率與選點品質。 |
| **金融情報分析** | MimirRAG 系統21 | 規劃智能體分解複雜查詢，經混合檢索後交由驗證與數值推理智能體進行統合。 | 克服混合文件解析難題，增強數據溯源性與財務事實查核的準確度。 |
| **物流與倉儲營運** | MAIW 智慧倉儲藍圖19 | 設備、人力、安全等特化智能體平行監控，經由 LLM 仲裁者評估並輸出最優營運決策。 | 減少停機時間，將零散的物聯網與 ERP 數據轉化為統一、可解釋的 AI 指揮層。 |

## **三、 軍事環境下部署 AI 的系統性脆弱點：WARBENCH 基準測試的啟示**

在積極建構 AI 指揮參謀的同時，必須深刻認知到現有模型在極端壓力下的侷限性。為了檢驗語言模型在軍事決策中的真實能耐，研究人員開發了 WARBENCH 基準測試。該測試嚴格摒棄了過於簡化的合成查詢，轉而採用 136 個源自二戰後真實衝突的高保真歷史場景，以此對 9 款領先的語言模型進行了多維度的壓力測試22。測試結果揭露了幾個在設計軍事 AI 時必須克服的結構性缺陷：
首先是**法律與倫理約束的崩潰**。當系統被置於涉及國際人道法（IHL）或武裝衝突法兩難的情境中（例如：軍事目標與受保護的文化遺產相鄰，或敵方利用平民作為人肉盾牌）時，儘管大型的雲端閉源模型能維持基本的法律合規性，但為了滿足邊緣運算（Edge Computing）硬體限制而進行優化與大幅量化（Quantization，如 4-bit 量化）的小型開源模型，其決策違規率卻急遽攀升至將近 70%22。這意味著若要在前線部署輕量化的智能體，必須實施極為嚴格的對齊工程。
其次是對抗戰爭迷霧（Fog of War）的韌性不足。真實戰場的情報往往是碎片化且相互矛盾的。測試顯示，當場景中被抽離 20% 到 80% 的戰術元素（缺失情報），或被惡意注入相互衝突的虛假情報時，模型的基線戰術推理能力會發生系統性的崩潰，無法準確計算部隊的不對稱性或適應複雜地形22。然而，研究同時發現一項重要的緩解機制：若強制系統在給出最終建議前，必須先生成明確的「思維鏈（Chain-of-Thought, CoT）」推理步驟，則能有效發揮結構性防護網的作用，大幅降低無意間的違規行為並穩定決策品質22。

## **四、 超越常規 RAG：深度領域理解與自適應訓練策略**

使用者進一步詢問，除了檢索增強生成（RAG）之外，還有哪些方法可以訓練或加強 AI 對特定軍事領域的理解。RAG 的本質是「開卷考試」，雖然能有效導入外部最新資料並降低幻覺，但它並不會改變模型底層的參數化記憶（Parametric memory），也無法賦予模型對該領域深層次邏輯的直覺性理解21。要使 AI 真正具備軍事指揮官的思維，必須實施更深層的領域適應（Domain Adaptation）訓練策略。

### **連續預訓練（CPT）與監督式微調（SFT）的協同資源配置**

語言模型的訓練可分為預訓練與微調兩個主要階段。當基礎模型需要適應全新的高專業領域時，連續預訓練（Continual Pre-Training, CPT）是首要步驟。CPT 是讓模型在大量未標註的領域專屬語料（如軍事手冊、歷史戰報、裝備操作手冊）上進行無監督學習24。這個過程能將該領域的特殊術語、縮寫與底層知識結構深深烙印在模型的權重矩陣中，為其建立強大的先驗知識庫24。然而，僅依賴 CPT 可能會導致「災難性遺忘（Catastrophic forgetting）」，使模型喪失原本的通用指令遵循能力24。
因此，必須搭配**監督式微調（Supervised Fine-Tuning, SFT）**。SFT 透過提供高品質的「問題-解答」人工標註配對，教導模型如何提取其在 CPT 階段學習到的知識，並以符合專業參謀報告格式的特定語氣進行輸出24。
在實際的工程決策中，如何在有限的運算資源（代幣預算）下分配 CPT 與 SFT 的比例是一大挑戰。根據最新的研究與 D-CPT 定律（D-CPT Law），領域適應效能與 CPT:SFT 比例之間存在一個呈現高斯峰值與倒數對齊懸崖（Reciprocal alignment cliff）的數學關係27。在一項限制總代幣為 300 億的實驗中，研究證實無論針對何種專業領域，最佳的下游表現皆落在 **CPT 佔比約為 0.99992 至 0.99994** 的極端區間27。換言之，最有效的策略是將將近 299.98 億的代幣投入深度的連續預訓練以積累領域知識，而僅需保留極微量（約 180 萬至 240 萬代幣）的高品質 SFT 資料，即可完美喚醒並引導模型輸出專業解答。這種資源優化策略能使模型的領域適應效率達到最高境界27。

### **檢索增強微調（RAFT）：專為特定領域打造的混合技術**

為了解決傳統 RAG 無法優化模型內部理解，而傳統微調又缺乏對外部即時動態資料適應力的問題，柏克萊大學的研究團隊提出了一種顛覆性的訓練配方：**檢索增強微調（Retrieval-Augmented Fine-Tuning, RAFT）**26。RAFT 完美結合了 RAG 與 SFT 的優勢，專門為領域特定的開卷考試場景量身打造。
RAFT 的訓練機制極具巧思。在準備訓練資料集時，針對每一個專業問題，系統不僅會提供包含正確答案的「黃金文件（Oracle/Golden documents）」，還會刻意混入不相關或具有誤導性的「干擾文件（Distractor documents）」26。在微調過程中，模型被嚴格訓練必須執行兩項關鍵任務：

1. **過濾與鑑別：** 學會辨識並主動忽略那些無助於回答問題的干擾文件，這大幅增強了模型在充斥雜訊的戰場情報環境中的穩健性26。
2. **強制精確引用與思維鏈：** 模型被要求必須從相關文件中「逐字引用（Citing verbatim）」關鍵的文字序列，並基於這些引用發展出嚴謹的思維鏈（Chain-of-Thought）推理邏輯來得出最終答案26。

透過 RAFT 訓練出的語言模型，不僅內化了領域知識，更學會了「如何正確地閱讀並運用檢索到的情報」。在包括 PubMed（生物醫學）、HotpotQA（多跳推理）與 Gorilla（API 調用）等多項基準測試中，RAFT 的表現均穩定超越了傳統的「超級微調加上 RAG」方法，為建構高可信度的 AI 參謀系統提供了一條明確的技術路徑30。

| 領域適應訓練技術 | 運作機制與資源需求 | 主要優勢與能力提升 | 潛在限制與挑戰 |
| :---- | :---- | :---- | :---- |
| **連續預訓練 (CPT)** | 在基礎模型上，使用海量未標註的領域專屬文本繼續進行無監督學習24。 | 深度內化專業術語與底層知識結構，賦予模型該領域的「直覺」28。 | 運算成本極高；若未妥善控制，易引發對通用知識的災難性遺忘24。 |
| **監督式微調 (SFT)** | 使用高品質、具備明確「輸入-輸出」配對的標註資料進行監督式學習24。 | 精準調整模型的輸出格式與行為模式，使其遵循專業的報告規範與邏輯24。 | 極度依賴人工標註的資料品質；無法動態引入訓練資料集以外的最新資訊24。 |
| **檢索增強微調 (RAFT)** | 結合 RAG 與 SFT，使用包含正確與干擾文件的合成資料進行微調，強制要求思維鏈生成26。 | 培養模型在海量雜訊中過濾無用情報的能力，並確保決策溯源至可靠文件，大幅減少幻覺26。 | 需要複雜的資料準備流程以建構黃金與干擾文件集；訓練與推論的運算開銷均較高26。 |

### **應對資料稀缺：合成資料生成與自動化適應框架**

在軍事等高度機密的領域，獲取足夠的 SFT 或 RAFT 訓練資料往往面臨極大挑戰。此時，可利用先進的「合成資料生成（Synthetic Data Generation）」技術。例如，微軟提出的 RAFT 蒸餾配方（RAFT Distillation Recipe）利用了 Meta 最新發布、具備 4050 億參數的 Llama 3.1 405B 作為教師模型，能夠自動攝取企業內部的專屬 PDF 文件，並從中自主生成包含問題、解答與深層推理鏈的「自我指令（Self-instruct）」資料集36。此外，如 Toxicraft 框架更可用於利用少量種子資料，大量合成具備對抗性或有害資訊的測試集，藉此強化系統應對惡意資訊戰的防護力38。
最後，在系統建構的自動化方面，類似微軟 AutoAdapt 等開源框架提供了一種預算感知的自動化決策流程。面對繁雜的領域適應設計空間，該系統能根據使用者的延遲容忍度、硬體算力與隱私限制，自動在 RAG、SFT 與參數高效微調（如 LoRA）之間進行最佳策略的選擇與排序，將原本需要數週的手動試錯過程轉化為可重複執行的標準化管道39。在台灣在地化的實踐中，TAIDE（Trustworthy AI Dialogue Engine）計畫亦充分利用了開源模型架構，結合台灣專屬的法規、公文與文化語料進行領域自適應，並提供了一整套涵蓋前端設計、API 代理與模型微調評估的整合平台，展示了如何將通用語言模型在地化為具備高度領域專精能力的引擎40。

## **五、 結論與戰略建言**

綜上所述，建構一個由多個特化小型模型組成智庫，並由單一核心模型擔任仲裁者的「AI 指揮參謀」，不僅在軟體架構上具有強大的理論支撐與實踐基礎，更是克服單一大型語言模型在複雜決策環境中固有盲點的最佳解方。為了確保此系統能夠在真實、充滿摩擦與不確定性的戰略環境中發揮預期效能，建議在系統工程上採納以下戰略方針：

1. **落實異質化多智能體架構：** 確立「情報」、「後勤」、「作戰規劃」等提案者智能體的專屬 RAG 管道，並部署經過深度倫理對齊的大型模型作為仲裁者。導入基於 SPRT 統計機制的動態辯論流程，以在決策速度與決策品質間取得數學上的最佳平衡。
2. **深化空間推理與物理語義整合：** 借鑑 Geo-Commander 框架與 DoDAF 整合經驗，將戰場環境轉化為機器可讀的網格結構，並引入邏輯網與蒙地卡羅模擬作為外掛驗證層，補足純文本模型在實體空間幾何與動態機率評估上的先天不足。
3. **推動全光譜領域適應訓練：** 突破僅依賴 RAG 的限制，遵循 D-CPT 資源分配定律，實施極大比例的連續預訓練（CPT）以灌輸底層軍事準則；同時，積極導入檢索增強微調（RAFT）與合成資料生成技術，鍛鍊系統在戰爭迷霧與情報雜訊中進行精確資訊過濾與思維鏈溯源的能力。
4. **建立極端情境的護欄機制：** 針對 WARBENCH 所揭示的邊緣設備量化缺陷與法律違規風險，必須強制要求所有仲裁輸出皆包含完整的推理鏈（CoT），並設立獨立的合規審查模組，確保 AI 指揮參謀的所有戰術建議均嚴格恪守國際人道法與既定之交戰規則。

#### **Works cited**

1. Mixture of Agents (MoA) on GPU Cloud: Deploy Multi-LLM Voting Architectures (2026 Guide) | Spheron Blog, [https://www.spheron.network/blog/mixture-of-agents-gpu-cloud/](https://www.spheron.network/blog/mixture-of-agents-gpu-cloud/)
2. Mixture-of-Agents (MoA): How Collective Intelligence Elevates LLM Performance \- Zilliz blog, [https://zilliz.com/blog/mixture-of-agents-how-collective-intelligence-elevates-llm-performance](https://zilliz.com/blog/mixture-of-agents-how-collective-intelligence-elevates-llm-performance)
3. Mixture-of-Agents Enhances Large Language Model Capabilities \- arXiv, [https://arxiv.org/html/2406.04692v1](https://arxiv.org/html/2406.04692v1)
4. MAFA: A Multi-Agent Framework for Enterprise-Scale Annotation with Configurable Task Adaptation \- AAAI Publications, [https://ojs.aaai.org/index.php/AAAI/article/view/41431/45392](https://ojs.aaai.org/index.php/AAAI/article/view/41431/45392)
5. Mixture of Agents capability (multi-model synthesis) · Issue \#119 \- GitHub, [https://github.com/pydantic/pydantic-ai-harness/issues/119](https://github.com/pydantic/pydantic-ai-harness/issues/119)
6. Mixture-of-Agents (MoA): Improving LLM Quality through Multi-Agent Collaboration, [https://a-nikishaev.medium.com/mixture-of-agents-moa-improving-llm-quality-through-multi-agent-collaboration-eb0bcbbdbe9f](https://a-nikishaev.medium.com/mixture-of-agents-moa-improving-llm-quality-through-multi-agent-collaboration-eb0bcbbdbe9f)
7. ICML Poster KABB: Knowledge-Aware Bayesian Bandits for Dynamic Expert Coordination in Multi-Agent Systems, [https://icml.cc/virtual/2025/poster/46178](https://icml.cc/virtual/2025/poster/46178)
8. Together mixture of agents (MoA), [https://docs.together.ai/docs/mixture-of-agents](https://docs.together.ai/docs/mixture-of-agents)
9. When Agents Disagree: The Selection Bottleneck in Multi-Agent LLM Pipelines \- MDPI, [https://www.mdpi.com/2076-3417/16/10/4914](https://www.mdpi.com/2076-3417/16/10/4914)
10. LLM-FS-Agent: A Deliberative Role-based Large Language Model Architecture for Transparent Feature Selection \- arXiv, [https://arxiv.org/html/2510.05935v1](https://arxiv.org/html/2510.05935v1)
11. Sequential Consensus for Multi-Agent LLM Debates: A Wald-SPRT compute governor with calibration-based failure detection \- arXiv, [https://arxiv.org/html/2605.19193v1](https://arxiv.org/html/2605.19193v1)
12. NeurIPS Poster Multi-Agent Debate for LLM Judges with Adaptive Stability Detection, [https://neurips.cc/virtual/2025/poster/117644](https://neurips.cc/virtual/2025/poster/117644)
13. Multi-Agent Debate for LLM Judges with Adaptive Stability Detection \- arXiv, [https://arxiv.org/html/2510.12697v1](https://arxiv.org/html/2510.12697v1)
14. Strategic Doctrine Language Models (sdLM): A Comprehensive Framework for Automated Military Planning and Doctrinal Analysis \- arXiv, [https://arxiv.org/html/2601.14862](https://arxiv.org/html/2601.14862)
15. A first look at IBM's new large language model that's fine-tuned for defense applications, [https://defensescoop.com/2025/10/29/ibm-new-large-language-model-defense-applications-janes/](https://defensescoop.com/2025/10/29/ibm-new-large-language-model-defense-applications-janes/)
16. A framework of large language model commander agent for spatial reasoning in combat simulation \- PMC, [https://pmc.ncbi.nlm.nih.gov/articles/PMC13111702/](https://pmc.ncbi.nlm.nih.gov/articles/PMC13111702/)
17. Rectangular and hexagonal grids used for observation, experiment and simulation in ecology | Request PDF \- ResearchGate, [https://www.researchgate.net/publication/222692566\_Rectangular\_and\_hexagonal\_grids\_used\_for\_observation\_experiment\_and\_simulation\_in\_ecology](https://www.researchgate.net/publication/222692566_Rectangular_and_hexagonal_grids_used_for_observation_experiment_and_simulation_in_ecology)
18. LLM-Driven Modeling and Decision Support Methods for Cross-Domain Collaborative Mission Systems \- MDPI, [https://www.mdpi.com/2571-5577/9/4/80](https://www.mdpi.com/2571-5577/9/4/80)
19. Multi-Agent Warehouse AI Command Layer Enables Operational Excellence and Supply Chain Intelligence | NVIDIA Technical Blog, [https://developer.nvidia.com/blog/multi-agent-warehouse-ai-command-layer-enables-operational-excellence-and-supply-chain-intelligence/](https://developer.nvidia.com/blog/multi-agent-warehouse-ai-command-layer-enables-operational-excellence-and-supply-chain-intelligence/)
20. LLM Agents for Smart City Management: Enhancing Decision Support Through Multi-Agent AI Systems \- Semantic Scholar, [https://pdfs.semanticscholar.org/2b9f/d4552e6a9a0be834341aaab1172295de9f79.pdf](https://pdfs.semanticscholar.org/2b9f/d4552e6a9a0be834341aaab1172295de9f79.pdf)
21. MimirRAG: A Multi-Agent RAG Framework for Financial Data Retrieval with Metadata Integration \- arXiv, [https://arxiv.org/html/2605.25030v1](https://arxiv.org/html/2605.25030v1)
22. WARBENCH: A Comprehensive Benchmark for Evaluating LLMs in Military Decision-Making \- arXiv, [https://arxiv.org/html/2603.21280v1](https://arxiv.org/html/2603.21280v1)
23. \[2603.21280\] WARBENCH: A Comprehensive Benchmark for Evaluating LLMs in Military Decision-Making \- arXiv, [https://arxiv.org/abs/2603.21280](https://arxiv.org/abs/2603.21280)
24. Unsupervised Pre-training vs. Supervised Fine-tuning for LLMs, [https://llmmodels.org/blog/unsupervised-pre-training-vs-supervised-fine-tuning-for-llms/](https://llmmodels.org/blog/unsupervised-pre-training-vs-supervised-fine-tuning-for-llms/)
25. GraphRAG-Enhanced Dialogue Engine for Domain-Specific Question Answering: A Case Study on the Civil IoT Taiwan Platform \- MDPI, [https://www.mdpi.com/1999-5903/17/9/414](https://www.mdpi.com/1999-5903/17/9/414)
26. What is RAFT? RAG \+ Fine-Tuning \- Innodata, [https://innodata.com/what-is-raft-rag-fine-tuning/](https://innodata.com/what-is-raft-rag-fine-tuning/)
27. Modelling Optimal Trade-Off Between Continued Pre-Training and Supervised Fine-Tuning for LLM Domain Adaptation | OpenReview, [https://openreview.net/forum?id=guUUlHPXRw](https://openreview.net/forum?id=guUUlHPXRw)
28. MODELLING OPTIMAL TRADE-OFF BETWEEN CON- TINUED PRE-TRAINING AND SUPERVISED FINE- TUNING FOR LLM DOMAIN ADAPTATION \- OpenReview, [https://openreview.net/pdf/3ea0a83c73d87a6a98d2b88890ee861937e1cc3c.pdf](https://openreview.net/pdf/3ea0a83c73d87a6a98d2b88890ee861937e1cc3c.pdf)
29. Amuro & Char: Analyzing the Relationship between Pre-Training and Fine-Tuning of Large Language Models \- ACL Anthology, [https://aclanthology.org/2025.repl4nlp-1.11.pdf](https://aclanthology.org/2025.repl4nlp-1.11.pdf)
30. arXiv:2403.10131v2 \[cs.CL\] 5 Jun 2024, [https://arxiv.org/pdf/2403.10131](https://arxiv.org/pdf/2403.10131)
31. RAFT: Adapting Language Model to Domain Specific RAG \- arXiv, [https://arxiv.org/html/2403.10131v2](https://arxiv.org/html/2403.10131v2)
32. RAFT: Adapting LLM for Domain-Specific RAG Excellence \- Galileo AI, [https://galileo.ai/blog/raft-adapting-llm](https://galileo.ai/blog/raft-adapting-llm)
33. \[2403.10131\] RAFT: Adapting Language Model to Domain Specific RAG \- arXiv, [https://arxiv.org/abs/2403.10131](https://arxiv.org/abs/2403.10131)
34. Combine the benefits of Retrieval-Augmented Generation and Fine-Tuning for better domain adaptation with Curator \- Bespoke Labs, [https://www.bespokelabs.ai/blog/curator-raft-boosting-rag-with-synthetic-data](https://www.bespokelabs.ai/blog/curator-raft-boosting-rag-with-synthetic-data)
35. RAFT: Adapting Language Model to Domain Specific RAG \- arXiv, [https://arxiv.org/html/2403.10131v1](https://arxiv.org/html/2403.10131v1)
36. The Future of AI: Distillation just got easier \- Synthetic Data Gen Using Llama 3.1 405B & RAFT \- Microsoft Community Hub, [https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/the-future-of-ai-synthetic-data-gen-with-llama-3-1-405b--raft/4236077](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/the-future-of-ai-synthetic-data-gen-with-llama-3-1-405b--raft/4236077)
37. Responsible Synthetic Data Creation for Fine-Tuning with RAFT Distillation, [https://techcommunity.microsoft.com/blog/educatordeveloperblog/responsible-synthetic-data-creation-for-fine-tuning-with-raft-distillation/4259367](https://techcommunity.microsoft.com/blog/educatordeveloperblog/responsible-synthetic-data-creation-for-fine-tuning-with-raft-distillation/4259367)
38. ToxiCraft: A Novel Framework for Synthetic Generation of Harmful Information, [https://aclanthology.org/2024.findings-emnlp.970/](https://aclanthology.org/2024.findings-emnlp.970/)
39. AutoAdapt: Automated domain adaptation for large language models \- Microsoft Research, [https://www.microsoft.com/en-us/research/blog/autoadapt-automated-domain-adaptation-for-large-language-models/](https://www.microsoft.com/en-us/research/blog/autoadapt-automated-domain-adaptation-for-large-language-models/)
40. TAIDE Taiwanese Native Large Language Model, [https://taide.tw/en](https://taide.tw/en)
41. TAIDE Taiwanese Native Large Language Model, [https://taide.tw/public/download-model\_En](https://taide.tw/public/download-model_En)
42. NCHC TAIWAN AI RAP Generative AI Application Development Service, [https://llm-taskforce.pages.td.nchc.org.tw/docs/rhap-docs/en/docs/service\_intro/home](https://llm-taskforce.pages.td.nchc.org.tw/docs/rhap-docs/en/docs/service_intro/home)
