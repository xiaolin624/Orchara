# F12/T098 鎵嬫満瀹屾垚瀵硅瘽銆佸鎵瑰拰涓汉鏁版嵁鎺у埗锛圙itHub #99锛?
- 鐘舵€侊細**`passed`**锛堜氦浠樺彲閲嶆斁鐨勯〉闈?鐪熷疄鏍堝垽瀹氭墜娈点€佺湡瀹炶繍琛岃瘉鎹笌璇氬疄瑁佸喅锛涙湰绁ㄤ笉鍋囪瀹屾垚灏忓睆鐢诲竷缂栬緫锛?- 鍒嗘敮锛歚t/t098-mobile-personal-data`锛寃orktree `E:\Orchara-wt\t098`
- 鍩虹嚎锛歚integration/f12` @ `1ba9d01`锛圱095 鍚堝苟鍚庣殑 tip锛沗git merge-base --is-ancestor` 閫€鍑虹爜 0锛?- 浠ｇ爜+娴嬭瘯 HEAD锛歚b2d3089`锛坄bb3b10e feat(f12)` 鈫?`b2d3089 test(f12)`锛夛紱鏂囨。鎻愪氦绱ч殢鍏跺悗锛屾渶缁?HEAD 瑙?  `E:\Orchara\.scratch\f12\T098-REPORT.md`
- 鍚堝苟锛歚git -C E:\Orchara-wt\t098 merge integration/f12` 鈫?**Already up to date**锛堥泦鎴愬垎鏀粛涓?`1ba9d01`锛?
## 0. 鎵嬫澹版槑锛堢‖鎬э紝鍏堣锛?
- **涓嶆槸 Playwright / 涓嶆槸鐪熷疄娴忚鍣?DOM銆?* 椤甸潰璇佹嵁鏄?Node 鎵ц浠撳簱閲?*宸叉彁浜ょ殑鐪熷疄瀛楄妭**
  锛坄assets/approvals.js`銆乣chat.js`銆乣memories.js`銆乣personal-data.js`銆乣knowledge.js`銆乣knowledge.html`
  鐨勫唴鑱旇剼鏈級+ 鏈€灏?DOM/fetch shim銆傚畠璇佹槑锛氱湡瀹炶剼鏈瓧鑺傘€佺湡瀹炶姹傚舰鐘躲€佺湡瀹炴覆鏌撴枃鏈€佺姸鎬?鎸夐挳琛屼负锛?  瀹?*涓嶈瘉鏄?* CSS 鍍忕礌甯冨眬銆佸獟浣撴煡璇㈠湪鐪熷疄瑙嗗彛涓殑姹傚€笺€佽Е鎺ф墜鍔裤€乣safe-area`銆佹粴鍔ㄦ儻鎬с€佸睆骞曢槄璇诲櫒銆?  CSS 鏂偣鐨?*鐪熷疄娓叉煋涓?`not_run`**锛涘彧瀵规彁浜ょ殑 CSS 瑙勫垯銆佺被鍚嶅拰椤甸潰 DOM 缁撴瀯鍋氶潤鎬佹柇瑷€銆?- **鐪熷疄鏍?*锛氱嫭绔?PostgreSQL 16.4 鏁版嵁搴擄紙`127.0.0.1:55432`锛屾瘡鏍堣嚜寤哄簱锛? 鐪熷疄杩佺Щ/bootstrap +
  缂栬瘧鐨?`cmd/api` + 鐪熷疄 internal mTLS锛坵orker/supervisor/maintenance 璇佷功锛? 鐪熷疄瀵硅薄鐩綍 +
  鐪熷疄 `cmd/maintenance`锛坅rtifact finalize / document_ingest锛夈€傛墍鏈?HTTP 鏂█璧?loopback socket锛?  涓?import handler銆佷笉 mock 鏈嶅姟銆?- **瀹℃壒杩囨湡瀵规瘮鏍?*锛氭湰绁ㄧ殑 API 杩涚▼鐢?`ORCHARA_APPROVAL_SWEEP_INTERVAL=100ms`锛堟祴璇曠獥鍙ｏ級銆?  鐢熶骇榛樿鏄?30s锛坄internal/approvals/expiry.go` + `cmd/api` 瀹氭椂鍣級锛?00ms 鍙槸璁┾€渘ode 瀹℃壒琚悗鍙?  sweeper 鑷姩杩囨湡鈥濆湪娴嬭瘯閲岀‘瀹氬彲瑙傚療锛屼笉鏀瑰彉璇箟銆?- **涓嶆槸鐪熷疄鍘傚晢妯″瀷**锛歚ORCHARA_PROFILE=dev-stub`锛屾棤鍘傚晢鍑嵁/绔偣/GPU锛涙湰绁ㄤ笉璋冪敤妯″瀷銆?- **鐗╃悊鍒犻櫎鏈畬鎴?*锛氬垹闄ょ殑鈥滅珛鍗冲仠姝㈣闂€濇槸鐪熷疄鏂█锛涒€滃紓姝ョ墿鐞嗘竻鐞?澶囦唤鏈熼檺鈥濆彧鏄剧ず鏈嶅姟绔垹闄や换鍔?  鐨勭湡瀹炵姸鎬侊紝涓嶅０绉板瓧鑺傚凡鍒犻櫎锛團07 娓呯悊鍊哄姟锛岃 搂8锛夈€?
## 1. 閫?AC 瀵圭収锛坕ssue #99 鍘熸枃锛?
| # | 绁ㄩ潰 AC | 缁撹 | 璇佹嵁锛堢湡瀹炴墽琛岋級 |
|---|---|---|---|
| 1 | 鍐茬獊/杩囨湡/鏃犵粨鏋滄湁鍙嶉 | **passed** | 鐪熷疄鏍?5 绫伙細鈶?璁板繂 `PATCH` 闄堟棫 `If-Match` 鈫?`412 precondition_failed`锛宍details.current_etag="1"`銆乣current_revision=1`锛屾鏂囨湭鍙橈紱鐢ㄥ綋鍓?ETag 閲嶈瘯 鈫?`200 revision=2`銆傗憽 鍚屼竴 `Idempotency-Key` + 涓嶅悓 body 鈫?`409 idempotency_conflict`锛屽簱閲屼粛鍙湁 1 鏉¤姹傘€傗憿 浜や簰鍒版湡鍚庣瓟澶?鈫?`409 interaction_closed`锛岃姹?`failed(interaction_expired)`锛? Run銆傗懀 鏃犲尮閰嶅簲鐢?鈫?`failed(no_matching_application)`锛? 鍊欓€夈€? Run銆傗懁 绌虹煡璇嗗簱 鈫?妫€绱?`200 items=[]`銆侀棶绛?`200 no_results`锛堜笉鏄敊璇級銆傞〉闈細鐪熷疄 412 鍠傜粰 `memories.js::preconditionPlan` 鈫?鈥滅増鏈啿绐佲€﹂潤榛樿鐩栤€濇枃妗堬紱鐪熷疄 409 鍠傜粰 `chat.js::describeSubmitFailure` 鈫?`reuseKey=false`锛堜笉閲嶆斁鏃ч敭锛夛紱鐪熷疄澶辫触璇锋眰鍠傜粰 `describeRequestFailure` 鈫?鈥滆繃鏈熲€﹂噸鏂版彁浜も€濃€滄湭鎵惧埌鍖归厤鐨勫簲鐢ㄢ€﹀簲鐢ㄧ洰褰曗€濓紱绌烘绱?鈫?鈥滅┖缁撴灉涓嶆槸閿欒 + 涓嬩竴姝モ€濄€傚鎵硅繃鏈熻涓嬩竴鏉?|
| 2 | 鎾ら攢寮曠敤涓嶈兘璇荤紦瀛?| **passed** | 鐪熷疄鍏ㄩ摼璺細涓婁紶鈫抐inalize鈫抜ngest鈫抈ready`鈫掓绱⑩啋`GET /api/v1/knowledge-citations/{chunk_id}` **200 姝ｆ枃**锛沗DELETE /api/v1/knowledge-documents/{id}` 鈫?**202** `{id,state,resource_id}`锛坱ombstone 鍦ㄥ搷搴斿墠鎻愪氦锛夛紱鍚屼竴寮曠敤鍐嶈 鈫?**404 `resource_not_found`**锛屽搷搴旈噷**娌℃湁** `text`/`citation`锛涘悓涓€妫€绱?鈫?绌恒€傞〉闈細鐪熷疄 404 鍠傜粰宸叉彁浜?`knowledge.js::citationOpenFailure` 鈫?`clearBody=true`銆佲€滈〉闈笉浼氬洖閫€姝ゅ墠璇诲埌鐨勬鏂団€﹂噸鏂版绱⑩€濓紱`knowledge.html` 鍐呰仈鑴氭湰鍦?404 鏃舵妸 `#citation-text` 娓呯┖銆乣data-citation-available=false`锛屽苟鏂█**娌℃湁**娈嬬暀涓婁竴娆＄殑姝ｆ枃銆傚彟瑕嗙洊 403 鍒嗘敮銆?|
| 3 | 鎵嬫満瀹屾垚瀵硅瘽/瀹℃壒/缁撴灉鏌ョ湅 | **passed锛堢粨鏋?瑙勫垯鍙獙璇侊紱鐪熷疄鍍忕礌 `not_run`锛?* | 鏂板 `assets/mobile.css`锛?20px/480px 涓ゆ。鏂偣銆?4px 瑙︽帶鐩爣銆佸鑸崲琛屻€乣dl`/`.row` 鍗曞垪銆佽緭鍏?16px 闃?iOS 缂╂斁銆乣.table-scroll` 妯悜婊氬姩銆俙/chat`銆乣/approvals`銆乣/runs`銆乣/personal-data`銆乣/memories`銆乣/knowledge` 鍏ㄩ儴鎸傝浇锛坄<link>` 鍦ㄩ〉闈?`<style>` 涔嬪悗锛夛紱`/runs` 鐨?8 鍒楀彈鎺ц皟鐢ㄨ处鏈寘杩涘彲鑱氱劍鐨?`role=region` 婊氬姩瀹瑰櫒锛沗/chat` 琛ラ綈瀹冧竴鐩寸己澶辩殑椤甸潰绾у竷灞€锛堝師鍏堝彧寮?canvas.css锛宍.topbar/.page/.panel/.row` 鍏ㄦ棤瀹氫箟锛夈€傞〉闈㈣剼鏈敤 Node + shim 鏂█绐勫睆缁撴瀯/绫诲悕/鐘舵€佷笌鎸夐挳琛屼负銆?*鏄庣‘涓嶆壙璇?*灏忓睆鐢诲竷缂栬緫锛歚/chat`銆乣/runs`銆乣/approvals`銆乣/editor`銆乣/debug` 椤甸潰鍧囧甫 `data-mobile-scope="no-full-canvas-editing"` 澹版槑銆傜湡瀹炲獟浣撴煡璇㈡眰鍊笺€佸儚绱犲竷灞€銆佺湡鏈鸿Е鎺?= `not_run`锛埪?锛?|
| 4 | 绔埌绔叆鍙ｅ疄闄呮紨绀猴紱澶辫触銆佹巿鏉冦€侀噸璇?鎭㈠杈圭晫鏈夋柇瑷€ | **passed** | 鍏ュ彛鏄寮?Web/REST锛氫細璇濇彁浜?绛斿銆佸鎵瑰垪琛?璇︽儏/鍐冲畾銆佺煡璇嗗簱涓婁紶/鎽勫彇/妫€绱?寮曠敤/鍒犻櫎銆佽蹇嗗垱寤?ETag 鏇存柊/鍒犻櫎銆佸€欓€?propose/list/decision銆乨eletion job 璇诲彇銆傚け璐ヨ竟鐣岋細412/409/404/409-interaction/绌虹粨鏋?杩囨湡瀹℃壒/404 寮曠敤锛涙巿鏉冭竟鐣岋細鏈煡/浠栦汉鐨勫紩鐢ㄤ笌璁板繂 404锛堢湡瀹?owner-only SQL锛夛紝瀹℃壒鍙湁鎸囧畾瀹℃壒浜哄彲璇?鍐冲畾锛堝鐢?T050 璺緞锛夛紱閲嶈瘯杈圭晫锛?12 鍙湁鏄惧紡鈥滅敤鏈€鏂扮増鏈噸璇曗€濇墠鍐欍€佸箓绛夐敭鍐茬獊涓嶉潤榛橀噸鏀俱€佸鎵硅繃鏈熶笉澶嶆椿銆佸紩鐢?404 涓嶅洖閫€缂撳瓨銆傞〉闈㈢敤鐪熷疄鍝嶅簲 body 椹卞姩宸叉彁浜ゅ瓧鑺傦紙搂3锛?|
| 5 | 璁板綍鐗堟湰/杈撳叆 hash/鍛戒护/鍘熷缁撴灉/profile锛泂tub/real 鍒嗘姤锛涚己鏉′欢 blocked銆佹湭杩愯 not_run | **passed** | 鐗堟湰/hash 瑙?搂6锛屽懡浠や笌鍘熷 tail 瑙?搂5锛泂tub/real 鍒嗘姤锛堟湰绁ㄦ棤妯″瀷璋冪敤锛涢〉闈㈡槸 shim + committed bytes锛汻EST 鏄?real stack锛夛紱`not_run` 閫愰」鍒楀湪 搂7锛涙棤 `blocked`锛堢湡瀹?PG/缂栬瘧鍣?Node/璇佷功鍧囧彲鐢級 |

## 2. 浜や粯鐗╋紙鏈エ瀹為檯鏀瑰姩锛?
| 浜や粯鐐?| 鏂囦欢 | 鍐呭 |
|---|---|---|
| 鍝嶅簲寮忔牱寮?| 鏂板 `product/apps/web/src/assets/mobile.css`锛?45 琛岋級 | 涓ゆ。 `@media`銆?4px 瑙︽帶鐩爣銆佸鑸崲琛屻€佸崟鍒楀畾涔夊垪琛ㄣ€?6px 杈撳叆銆乣.table-scroll`銆乧hat 椤甸潰鍩虹嚎锛?*涓嶆敼** canvas.css/canvas.js |
| 椤甸潰鎺ョ嚎 | `chat.html`銆乣approvals.html`銆乣runs.html`銆乣personal-data.html`銆乣memories.html`銆乣knowledge.html`銆乣editor.html`銆乣debug.html` | `mobile.css` 寮曞叆銆乣body.page-*` 绫汇€佸鑸?aria-label銆乣/runs` 瀹借〃婊氬姩瀹瑰櫒銆佸皬灞忚竟鐣屽０鏄?|
| 瀹℃壒杩囨湡 | `assets/approvals.js` + `approvals.html` | `approvalExpiryNote`锛坈apability 鎯版€?vs node sweeper锛夈€乣describeDecisionFailure`锛坅pproval_expired/not_pending/params_mismatch/conflict/鈥︼級锛涘喅瀹氬け璐ョ鐢ㄦ寜閽€侀噸璇绘湇鍔＄鐘舵€併€佷笉鑷姩閲嶈瘯锛沗#approval-expiry-note` |
| 瀵硅瘽鍐茬獊/杩囨湡 | `assets/chat.js` | `describeSubmitFailure`锛?09 `idempotency_conflict` 涓嶉噸鏀炬棫閿紱缃戠粶澶辫触鎵嶅鐢ㄩ敭锛夈€乣describeReplyFailure`锛坄interaction_closed`/`interaction_expired`锛夛紱淇濈暀鏃㈡湁 `describeRequestFailure` 鐨?no_match/interaction_expired 鏂囨 |
| 璁板繂鍊欓€?| `assets/memories.js` + `memories.html` | 鍊欓€夋樉绀烘潵婧?浜や簰锛圧un锛夊皝瀛?淇濆瓨鑼冨洿锛堝簲鐢級/鐘舵€?杩囨湡锛沗dataset.selection="none"`銆佹棤鍕鹃€夋帶浠躲€佹棤鍏ㄩ€夛紱`decisionFailurePlan`锛堣繃鏈?宸茬‘璁?宸叉嫆缁?骞傜瓑鍐茬獊锛? 鏄惧紡鍒锋柊 |
| 涓汉鏁版嵁 | `assets/personal-data.js` + `personal-data.html` | 鏂囨。 `uploaded/scanning/indexing/ready/failed/deleting/deleted` 鏂囨 + 澶辫触鐮侊紙鐪熷疄 diagnostics锛夛紱鍒犻櫎鈥滅珛鍗冲仠姝㈣闂€濅笌鈥滃紓姝ョ墿鐞嗘竻鐞?澶囦唤鏈熼檺鈥濆垎鏍?+ 浠诲姟鐘舵€?鍓╀綑鑼冨洿锛涜瘖鏂璞?*榛樿涓嶅嬀閫?* |
| 寮曠敤涓嶅彲璇?| `assets/knowledge.js` + `knowledge.html` | `citationOpenSuccess/citationOpenFailure`锛?04/403 娓呮鏂囥€佷笉鍥為€€缂撳瓨銆佷笅涓€姝ワ級锛涚┖妫€绱?鈥滀笉鏄敊璇?+ 涓嬩竴姝モ€?|
| 闈欐€佽矾鐢?| `internal/api/server.go` | `GET /assets/mobile.css`锛坈ompile-time 甯搁噺锛屼笌鍏朵粬闈欐€佽祫婧愬悓妯″紡锛?|
| 娴嬭瘯 | `product/tests/f12/test_t098_mobile_pages.py`锛? 鐢ㄤ緥锛夈€乣test_t098_stack.py`锛? 鐢ㄤ緥锛?| 椤甸潰锛歝ommitted bytes + DOM/fetch shim锛涚湡瀹炴爤锛氱湡瀹?HTTP/PostgreSQL/mTLS/maintenance |

`nav.js`銆乣runstream.js`銆乣runs.js`銆乣runview.js` **闆舵敼鍔?*锛氭湰绁ㄥ鐢?T095 鐨勮鑹插寲瀵艰埅涓?T096 鐨?state/phase 鏄犲皠锛屾病鏈夊鍒剁浜屼唤鐘舵€佽〃銆?
## 3. 鐪熷疄鏍堣娴嬶紙鏈杩愯鐨勫疄闄呮柇瑷€鍊硷級

浠ヤ笅鍊肩敱 `test_t098_stack.py` 鍦ㄦ湰娆＄湡瀹炴爤杩愯涓鍙栧苟鏂█锛堜笉鏄姤鍛婇噷浜嬪悗缂栭€狅級锛?
| 鍦烘櫙 | 瀹為檯瑙傛祴 |
|---|---|
| 412 ETag 鍐茬獊 | `PATCH /api/v1/memories/{id}`锛坄If-Match: "0"` 瀵?revision 1锛夆啋 `412`锛宍error.code=precondition_failed`锛宍details={"current_etag":"\"1\"","current_revision":1}`锛涢殢鍚?`GET` 浠嶆槸 revision 1銆佸師姝ｆ枃锛涚敤 `If-Match: "1"` 閲嶈瘯 鈫?`200`銆乺evision 2 |
| 409 骞傜瓑鍚岄敭寮備綋 | 鍚屼竴 `Idempotency-Key`銆佷笉鍚?body 鐨勭浜屾 `POST /api/v1/requests` 鈫?`409`锛宍error.code=idempotency_conflict`锛沗platform` 涓浼氳瘽浠嶅彧鏈?1 鏉?request锛涢〉闈㈣鍒?`reuseKey=false` |
| 浜や簰杩囨湡 | `route_request` 鍚?`awaiting_input`锛涙妸 15 鍒嗛挓 deadline 鏀瑰埌杩囧幓锛沗POST /requests/{id}/reply` 鈫?`409`锛宍error.code=interaction_closed`锛況equest 鈫?`failed`銆乣error_code=interaction_expired`锛沬nteraction 鈫?`expired`锛? Run |
| 鏃犵粨鏋滐紙璺敱锛?| 鎻愪氦鈥滃啓涓€棣栧叧浜庢槬澶╃殑璇椻€?鈫?`failed`銆乣error_code=no_matching_application`锛? 鍊欓€夈€? Run銆? interaction锛涢〉闈㈡枃妗堝惈鈥滄湭鎵惧埌鍖归厤鐨勫簲鐢ㄢ€濃€滃簲鐢ㄧ洰褰曗€?|
| 鏃犵粨鏋滐紙妫€绱?闂瓟锛?| 绌虹煡璇嗗簱锛歚POST retrieval-queries` 鈫?`200 items=[]`锛沗POST answer` 鈫?`200 outcome=no_results`銆乣citations=[]`锛堜笉鏄?4xx锛?|
| 鎾ら攢寮曠敤 | 寮曠敤 `GET` 鈫?`200` 涓?`text` 涓庢绱㈢墖娈甸€愬瓧鐩哥瓑锛沗DELETE /knowledge-documents/{id}` 鈫?`202 {id,state,resource_id}`锛涘悓涓€寮曠敤 鈫?`404 resource_not_found` 涓斿搷搴旀棤 `text`/`citation`锛涙绱?鈫?`items=[]`锛沗GET /deletions/{job}` 鈫?`200`銆乣remaining_scopes` 闈炵┖锛涙枃妗ｈ鎯?鈫?`404 resource_not_found`锛坱ombstone 鍚庝笉鍐嶆灇涓撅級 |
| 瀹℃壒杩囨湡 A锛坈apability锛?| 鐪熷疄 connector + `invokeCapability` 鍋滃湪 `awaiting_approval`锛沗expires_at` 鏀瑰埌杩囧幓鍚庣瓑寰?0.6s锛堚墺6 娆?100ms sweep锛夆啋 `GET /approvals/{id}` 浠?`state=pending`锛?*娌℃湁琚?sweeper**锛夛紱`POST decision` 鈫?`409 approval_expired`锛涜鍙?`expired` |
| 瀹℃壒杩囨湡 B锛坣ode锛?| 鐪熷疄 supervisor claim + `POST /internal/v1/runs/{id}/node-approvals`锛坄operation_id IS NULL`锛? waiting result + exit锛沝eadline 鏀瑰埌杩囧幓 鈫?鍚庡彴 sweeper 鍦?15s 鍐呮妸 Run 鍙?`failed(error_code=approval_expired)`銆佸鎵瑰彉 `expired(decision_key=sweeper:expired)`锛?*娌℃湁浜哄伐鍐冲畾**锛夛紱杩熷埌鍐冲畾 鈫?`409 approval_expired` |
| 鍒犻櫎绔嬪嵆绂佽 | `DELETE /memories/{id}` 鈫?`202`锛涗笅涓€娆?`GET` 鈫?`404 resource_not_found`锛涘垪琛?`items=[]`锛沗GET /deletions/{job}` 鈫?`200`銆乣remaining_scopes` 闈炵┖ |
| 鍊欓€夐粯璁ゆ湭鍐?| 鐪熷疄 worker `propose` 鈫?`201`锛宐ody 鎭?5 瀛楁锛坄id/application_id/text/state/expires_at`锛夈€乣state=pending`锛涘垪琛ㄦ棤浠讳綍閫夋嫨瀛楁锛涢〉闈㈡姇褰?`defaultDecision="none"`銆佷繚瀛樿寖鍥村惈鐪熷疄 `application_id`锛涙樉寮?`POST decision` 鈫?`200 state=confirmed` + `memory_id`锛岃蹇嗗垪琛ㄥ彧鍑虹幇杩欎竴鏉?|

## 4. 椤甸潰琛屼负鏂█锛圢ode 鎵ц宸叉彁浜ょ湡瀹炲瓧鑺?+ DOM/fetch shim锛?
| # | 鏂█ | 鐢ㄤ緥 |
|---|---|---|
| 4.1 | 6 涓墜鏈烘梾绋嬮〉闈㈤兘鏈?viewport銆乣mobile.css` 鍦ㄩ〉闈?`<style>` 涔嬪悗銆乣body.page-*` 绫伙紱CSS 鏈?720px 鏂偣銆?4px 瑙︽帶鐩爣銆乣overflow-x:auto`銆乣flex-wrap`銆?6px 杈撳叆 | `test_mobile_stylesheet_is_linked_after_each_pages_own_style` |
| 4.2 | `/runs` 8 鍒楄〃鏍煎寘鍦?`.table-scroll`锛坄role=region`銆乣tabindex`銆乣aria-label`锛夛紝涓嶆孩鍑洪〉闈?| `test_the_wide_ledger_table_scrolls_instead_of_overflowing_the_phone` |
| 4.3 | `/chat`銆乣/runs`銆乣/approvals`銆乣/editor`銆乣/debug` 鏄庣ず涓嶆壙璇哄皬灞忓畬鏁寸敾甯冪紪杈?| `test_each_phone_journey_state_that_small_screens_keep_is_named_in_the_page` |
| 4.4 | 瀹℃壒椤佃В閲?*涓ょ**杩囨湡璇箟锛坈apability 鏃犲悗鍙版壂鎻忋€乶ode 鏈?30s sweeper锛夛紝鐪熷疄 409 expiry 鍚庢寜閽鐢ㄣ€佺姸鎬侀噸璇汇€佷笉鑷姩閲嶈瘯銆佷笉澶嶆椿 | `test_the_approval_page_explains_both_expiry_mechanisms_and_never_revives` |
| 4.5 | chat 瀵?409 `idempotency_conflict` 鏄庣‘鈥滄柊浠诲姟/鏃ч敭涓嶈鐩栤€濓紝缃戠粶澶辫触鎵嶅鐢ㄥ悓涓€閿紱`interaction_closed` 鏄庣‘鈥滄棫绛斿涓嶄細澶嶆椿浠诲姟鈥濓紱鏃犲尮閰?杩囨湡鏈変笅涓€姝ワ紱闈炲け璐ヨ姹傞殣钘忔棫澶辫触鍧?| `test_chat_reports_conflict_expiry_and_no_result_with_a_next_step` |
| 4.6 | 璁板繂鍊欓€夋樉绀烘潵婧?浜や簰灏佸瓨/淇濆瓨鑼冨洿/搴旂敤/鐘舵€?杩囨湡锛涙棤鍕鹃€夋帶浠躲€佹棤鍏ㄩ€夈€佺偣鍑诲墠涓嶅彂鍐冲畾锛涚湡瀹?409 杩囨湡鍚庘€滄病鏈変繚瀛樹换浣曡蹇嗏€﹂噸鏂拌繍琛屸€濅笖涓嶉噸璇?| `test_memory_candidates_show_source_and_save_scope_and_default_to_no_choice` |
| 4.7 | 涓汉鏁版嵁锛? 涓枃妗ｇ姸鎬佸叏閮ㄦ湁鏂囨锛涘け璐ユ枃妗ｆ樉绀虹湡瀹?`ingest_empty_content` + 涓枃鍚箟锛沝eleting/deleted 涓嶅啀鎻愪緵鍒犻櫎锛涜瘖鏂璞￠粯璁や笉鍕鹃€変笖 `selectedGrantObjects()==[]`锛涘垹闄?sheet 鍚屾椂灞曠ず鈥滅珛鍗冲仠姝㈣闂€濆拰鈥滃紓姝ユ竻鐞嗕笌澶囦唤鏈熼檺鈥?浠诲姟鐘舵€?鍓╀綑鑼冨洿 | `test_personal_data_shows_ingestion_states_and_never_grant_selection_by_default` |
| 4.8 | 鐭ヨ瘑搴撳紩鐢細绗竴娆?200 鏄剧ず姝ｆ枃锛涘悓涓€寮曠敤 404 鍚庢鏂囨竻绌恒€乣data-citation-available=false`銆佲€滃紩鐢ㄤ笉鍙敤鈥︿笉浼氬洖閫€鈥﹂噸鏂版绱⑩€濓紱绌烘绱㈡湁鈥滀笉鏄敊璇?+ 涓嬩竴姝モ€濓紱403 鍚屾牱瑙ｉ櫎鍙 | `test_a_revoked_citation_is_cleared_and_never_served_from_the_previous_body` |
| 4.9 | 鐪熷疄 HTTP body 椹卞姩椤甸潰瑙勫垯锛?12鈫抈preconditionPlan`銆?09鈫抈describeSubmitFailure`銆乮nteraction/no_match鈫抈describeRequestFailure`銆?04 citation鈫抈citationOpenFailure`銆佺┖妫€绱⑩啋`describeRetrieval`銆佸鎵光啋`approvalExpiryNote`/`describeDecisionFailure`銆乨eletion鈫抈describeDeletionJob`銆佸€欓€夆啋`describeCandidate` | `test_t098_stack.py` 鍚勭敤渚嬬殑 `_page_probe` |

## 5. 闂ㄧ鍛戒护涓庡師濮?tail锛?026-11-06锛屾湰鏈猴紝鏈€缁堟彁浜ゆ爲锛?
鎵€鏈夊懡浠ゅ湪 `E:\Orchara-wt\t098\product` 涓嬫墽琛岋紱鐪熷疄鏍堟湡闂存寔鏈?`E:\Orchara\.scratch\f12\locks\realstack`锛岀粨鏉熸椂鍒犻櫎銆?
### 5.1 鏈エ濂椾欢

```powershell
python -m pytest tests/f12/test_t098_mobile_pages.py tests/f12/test_t098_stack.py -q
```

```
..............                                                           [100%]
14 passed in 27.93s
```

鎷嗗紑锛氶〉闈?8 passed / 鐪熷疄鏍?6 passed锛堟棩蹇?`pytest-t098-final.log`銆乣pytest-t098-all.log`锛夈€?
### 5.2 鍏ㄩ噺 F12锛?94 鏉℃棦鏈?+ 14 鏉℃柊澧烇級

```powershell
python -m pytest tests/f12 -q
```

```
........................................................................ [ 23%]
........................................................................ [ 46%]
........................................................................ [ 70%]
........................................................................ [ 93%]
....................                                                     [100%]
308 passed in 131.09s (0:02:11)
```

锛坄pytest-tests-f12-final.log`锛汿093/T095/T096 鐨?294 鏉″叏閮ㄤ繚鐣欙紝鏃?skip/ignore銆傦級

### 5.3 鍙楀奖鍝嶆棦鏈夊浠讹紙鐪熷疄鏍?Node锛屼覆琛屽崰閿侊級

```powershell
python -m pytest tests/f08/test_postreview_pages.py tests/f04b/test_t083_runview.py tests/f06/test_t050_pages.py -q
# 12 passed in 54.26s

python -m pytest tests/f09 -q
# 69 passed in 357.41s (0:05:57)

python -m pytest tests/f10/test_t055_pages.py tests/f10/test_t064_pages.py -q
# 6 passed in 24.43s

python -m pytest tests/f08/test_t079_pages.py tests/f08/test_t084_pages.py -q
# 4 passed in 17.79s
```

鍘熷灏惧反锛?
```
............                                                             [100%]
12 passed in 54.26s
.....................................................................    [100%]
69 passed in 357.41s (0:05:57)
......                                                                   [100%]
6 passed in 24.43s
....                                                                     [100%]
4 passed in 17.79s
```

### 5.4 Go / 杩佺Щ / 闈欐€?/ 璇硶

```powershell
go build ./...
go vet ./...
go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
node --check apps/web/src/assets/approvals.js
node --check apps/web/src/assets/chat.js
node --check apps/web/src/assets/memories.js
node --check apps/web/src/assets/personal-data.js
node --check apps/web/src/assets/knowledge.js
python E:\Orchara\.scratch\f12\T098\check_inline_scripts.py
```

```
build_exit=0
vet_exit=0
gofmt_exit=0
no drift: spec copy and contract snapshot match the manifest
sync_exit=0
approvals=0
chat=0
memories=0
personal_data=0
knowledge=0
inline blocks checked: 4
all inline scripts parse
```

`go test ./...` 灏鹃儴锛坄test_exit=0`锛夛細

```
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
ok  	orchara/platform/internal/tools	(cached)
ok  	orchara/platform/internal/toolversions	(cached)
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
test_exit=0
```

### 5.5 鍚堝苟

```powershell
git -C E:\Orchara-wt\t098 merge integration/f12
# Already up to date.        锛坕ntegration/f12 浠嶄负 1ba9d01锛?git -C E:\Orchara-wt\t098 rev-parse HEAD
# b2d3089锛堜唬鐮?娴嬭瘯锛涙枃妗ｆ彁浜ょ揣闅忓叾鍚庯級
```

鍚堝苟鍚庡凡澶嶈窇 搂5.1/搂5.2/搂5.4锛堣緭鍑哄嵆涓婅〃鏈€缁堟爲锛夈€?
## 6. 鐗堟湰銆佽緭鍏?hash銆佺幆澧?profile

| 椤?| 鍊?|
|---|---|
| 鍒嗘敮 / worktree | `t/t098-mobile-personal-data` / `E:\Orchara-wt\t098` |
| 鍩虹嚎 | `integration/f12` @ `1ba9d01` |
| 浠ｇ爜+娴嬭瘯 HEAD | `b2d3089` |
| 濂戠害蹇収 | `contracts/openapi.json` sha256 `bf191b9697f99b362bd61c287964724637e1281153232699aacf582157fc3e91`锛?*鏈敼**锛?3 paths / 106 operations锛?|
| 浠诲姟鍥?| `contracts/task-graph.json` sha256 `e58465e9705e6398eb0e87f8529263c61793756376247cd71d8c63e6beaedcf6`锛?*鏈敼**锛?|
| Go / Python / Node / PG | go1.27.3 windows/amd64 / Python 3.12.2锛坧ytest 9.0.3锛? Node v24.13.0 / PostgreSQL 16.4锛堥殧绂婚泦缇?`127.0.0.1:55432`锛?|
| profile | `ORCHARA_PROFILE=dev-stub`锛堟棤鐪熷疄鍘傚晢妯″瀷/鍑嵁/GPU锛?|
| 瀹℃壒 sweep锛堟祴璇曟爤锛?| `ORCHARA_APPROVAL_SWEEP_INTERVAL=100ms`锛堢敓浜ч粯璁?30s锛涗粎鐢ㄤ簬璁?node sweeper 纭畾鍙瀵燂級 |
| 鐪熷疄鏍?shape | 鐙珛搴?`orchara_t098_*`锛岀湡瀹?`cmd/api` + internal mTLS锛坵orker/supervisor/maintenance锛? 瀵硅薄鐩綍 + `cmd/maintenance --once/run`锛涙祴璇曠粨鏉?drop 鏁版嵁搴?|
| 杈撳叆 | 鐪熷疄涓婁紶鐨?`t055.SAMPLE_DOCUMENT`锛堜腑鏂囬攢鍞?閫€娆捐祫鏂欙級锛涚湡瀹?connector loopback锛涚湡瀹?golden draft/minimal draft锛涚湡瀹炲€欓€?璁板繂姝ｆ枃 |

## 7. 鍙獙璇?/ 涓嶅彲楠岃瘉鍒嗘爮

| 鍙獙璇侊紙鏈鐪熷疄鎵ц锛?| 涓嶅彲楠岃瘉锛坄not_run`锛屼笉鍐欐垚閫氳繃锛?|
|---|---|
| 鐪熷疄 HTTP 鐘舵€佺爜/閿欒鐮?details锛?12/409/404/200/202锛?| 鐪熷疄娴忚鍣?DOM銆丳laywright銆丆hromium |
| 鐪熷疄 PostgreSQL 琛岀殑 revision/tombstone/deletion job/鍊欓€夌姸鎬?瀹℃壒鐘舵€?| CSS 濯掍綋鏌ヨ鍦ㄧ湡瀹炶鍙ｄ腑鐨勬眰鍊间笌鍍忕礌甯冨眬 |
| 鐪熷疄 mTLS claim/waiting/exit銆佸悗鍙?100ms sweeper 鐨勮嚜鍔ㄨ繃鏈?| 鐪熸満/妯℃嫙鍣ㄤ笂鐨勮Е鎺х洰鏍囧昂瀵搞€佹墜鍔裤€佹粴鍔ㄦ儻鎬с€乣safe-area`銆佹í绔栧睆 |
| 鐪熷疄 maintenance 鐨?upload finalize + document ingest | 灞忓箷闃呰鍣?鏃犻殰纰嶆爲銆佺湡瀹為敭鐩樼劍鐐归『搴?|
| 宸叉彁浜ら〉闈㈣剼鏈殑璇锋眰褰㈢姸銆佹覆鏌撴枃鏈€佹寜閽鐢?閲嶈瘯瑙勫垯锛圢ode+shim锛?| `mobile.css` 鍦ㄧ湡瀹炴祻瑙堝櫒閲岀殑瑙嗚灞傜骇/鎹㈣鏁堟灉 |
| 鎻愪氦鐨?CSS 瑙勫垯/绫诲悕/DOM 缁撴瀯鍜岄〉闈㈠０鏄庯紙闈欐€佹柇瑷€锛?| 鐪熷疄鎵嬫満缃戠粶/璁惧鎬ц兘銆佹祻瑙堝櫒鍓嶈繘鍚庨€€缂撳瓨 |
| 濂戠害鏈敼銆侀潤鎬佽矾鐢卞彲鏈嶅姟锛坄/assets/mobile.css`锛?| 鐪熷疄鍘傚晢妯″瀷锛坉ev-stub only锛?|
| 鍒犻櫎鍚庘€滅珛鍗崇璇烩€濓紙涓嬩竴娆¤鍙?404銆佹绱㈢┖銆佸紩鐢?404锛?| 鈥滅墿鐞嗗壇鏈凡鍒犻櫎/澶囦唤宸茶繃鏈熲€濃€斺€斿彧鏄剧ず job 鐪熷疄鐘舵€侊紝涓嶅０绉?|
| 鍊欓€夐粯璁ゆ湭鍐筹紙鏃犻€夋嫨鎺т欢銆佹棤鍏ㄩ€夈€佹樉寮忕偣鍑绘墠鍙戝喅瀹氾級 | 鐢诲竷缂栬緫锛坄/editor`銆乣/debug`锛夊湪灏忓睆鐨勫彲鐢ㄦ€р€斺€旂エ闈㈡槑纭笉鎵胯 |

## 8. 淇簡 / 鏈慨锛堝綊灞?epic锛?
### 8.1 淇簡锛堟湰绁ㄨ寖鍥达級

| # | 缂哄彛锛堜慨鍓嶏級 | 淇 | 瑕嗙洊 |
|---|---|---|---|
| 1 | 鍏ㄥ簱 0 涓〉闈㈢骇 `@media`锛沗/chat` 鍙紩 canvas.css锛宍.topbar/.page/.panel` 鏃犲畾涔夛紱`/runs` 8 鍒楄〃鏍兼棤妯悜婊氬姩 | 鏂板 `mobile.css`锛?20/480px銆?4px銆乣.table-scroll`锛夛紝6 椤垫帴绾匡紝`/runs` 鍖呮粴鍔ㄥ鍣紝chat 琛ラ〉闈㈢骇甯冨眬 | 搂2銆?.1銆?.2 |
| 2 | 瀹℃壒鍙湁 `invalidated` 鏂囨锛屾病鏈夎繃鏈熻涔夛紱`approval_expired` 鍙湪閫氱敤閿欒閲?| `approvalExpiryNote` 鍖哄垎 capability 鎯版€?vs node sweeper锛沗describeDecisionFailure` 缁欐瘡绫绘嫆缁濈殑涓嬩竴姝ワ紱澶辫触鍚庨噸璇绘湇鍔＄鐘舵€併€佷笉鑷姩閲嶈瘯 | 4.4銆伮? |
| 3 | 鎻愪氦 409 `idempotency_conflict` 浼氫繚鐣欐棫閿紝閲嶈瘯鍙兘缁х画鍐茬獊 | `describeSubmitFailure`锛氬啿绐佹椂 `reuseKey=false`锛屾彁绀哄埛鏂?鏂颁换鍔★紱缃戠粶澶辫触鎵嶅鐢ㄩ敭 | 4.5 |
| 4 | 鍊欓€夊彧鏄剧ず鈥滄潵婧愨€濆浐瀹氭枃妗堜笌搴旂敤 id锛屾棤淇濆瓨鑼冨洿锛涜瘖鏂璞￠粯璁ゅ叏閫?| 鍊欓€夋樉绀烘潵婧?浜や簰灏佸瓨/淇濆瓨鑼冨洿/搴旂敤锛沗dataset.selection="none"`銆佹棤鍏ㄩ€夛紱璇婃柇瀵硅薄 `checked=false` | 4.6銆?.7 |
| 5 | 涓汉鏁版嵁鏂囨。鍙樉绀哄師濮?`state`锛屽け璐ョ爜涓嶅彲瑙?| 7 鐘舵€佷腑鏂囨枃妗?+ 澶辫触鏃惰鍙栫湡瀹?diagnostics 鏄剧ず绋冲畾澶辫触鐮?涓枃鍚箟 + 涓嬩竴姝?| 4.7 |
| 6 | 鍒犻櫎鍙湁涓€鍙モ€滅墿鐞嗗壇鏈紓姝ユ竻鐞嗏€濓紝鏈垎鈥滅珛鍗冲仠姝㈣闂?澶囦唤鏈熼檺鈥?| `accessStoppedText`/`cleanupPolicyText` 鍒嗘爮 + 浠诲姟鐘舵€?鍓╀綑 scopes锛堜粛涓嶅０绉板瓧鑺傚凡鍒狅級 | 4.7銆伮? |
| 7 | 寮曠敤 404 鏃跺彧鏄剧ず閫氱敤閿欒锛宍citation-meta` 浠嶅彲鑳芥槸鏃ф枃妗?| `citationOpenFailure` 娓呮鏂囥€乣data-citation-available=false`銆佹槑纭€滀笉鍥為€€缂撳瓨 + 閲嶆柊妫€绱⑩€濓紱retrieval/answer 涓や釜鍏ュ彛閮借蛋鍚屼竴瑙勫垯 | 4.8銆伮? |
| 8 | 绌烘绱㈠彧鏈変竴鍙モ€滅┖缁撴灉涓嶆槸閿欒鈥濓紝鏃犱笅涓€姝?| `emptyNextText` + 椤甸潰绗簩娈佃惤 | 4.8 |

### 8.2 鏈慨锛堝綊灞炲叾瀹?epic / 鏄庣‘涓嶅湪鏈エ鑼冨洿锛?
| 缂哄彛 | 璇佹嵁 | 褰掑睘 |
|---|---|---|
| 灏忓睆瀹屾暣鐢诲竷缂栬緫/鑺傜偣璋冭瘯琛?| 绁ㄩ潰涓?`UX.md:13` 鏄庣‘涓嶆壙璇猴紱`canvas.css` 浠?900px 鍗曞垪锛屾湰绁ㄦ湭鏀?canvas | 鏄庣‘涓嶅湪 T098 |
| 鍒犻櫎鍚庣殑鐗╃悊娓呯悊/澶囦唤鏈熼檺鐪熷疄瀹屾垚 | `DELETE` 杩斿洖 `pending` job銆乣remaining_scopes`锛涙湰绁ㄥ彧鏄剧ず鐪熷疄 job锛屼笉澹扮О鍒犻櫎 | F07/T071/T072 娓呯悊鍊哄姟 |
| `MemoryCandidate` 濂戠害娌℃湁 `source_run_id`锛坄additionalProperties:false`锛?| 椤甸潰鍙兘鏄剧ず鈥滀氦浜掔敱鏈嶅姟绔皝瀛樷€濓紝涓嶈兘缂栭€犲叿浣?Run id锛汥B 鏈?`source_run_id` 浣嗕笉鍦ㄥ绾︽姇褰?| F09/濂戠害娌荤悊 |
| 鏂囨。 tombstone 鍚庡垪琛ㄤ笉鍐嶆灇涓捐鏂囨。 | `GetDocument`/list 杩囨护 `deleted_at IS NULL`锛涘垹闄ゅ悗鐨勭湡瀹炲弽棣堟槸 `202` job + 鍒楄〃娑堝け + 鏂囨。/寮曠敤 404銆傞〉闈㈡槧灏勪簡 7 涓姸鎬侊紝浣嗕笉浼氫吉閫犱竴鏉″凡鍒犳枃妗ｇ殑鍙垹鏉＄洰 | F10/F11 鏃㈡湁鎶曞奖 |
| 璇婃柇鎺堟潈瀵硅薄鍒楄〃娌℃湁鈥滄寜杩愯閫夋嫨/鎼滅储鈥濇帶浠讹紝浠嶉渶鎵嬭緭 run UUID | 鏃㈡湁 F11/T073 椤甸潰鑳藉姏锛涙湰绁ㄥ彧鏀归粯璁や笉鍕鹃€?| F11/T073 |
| 鍒犻櫎浠诲姟涓嶈疆璇㈠埌鈥渃ompleted鈥濓紙姣忛〉鏈€澶?5 娆°€?s 闂撮殧锛夛紝鍚庡彴娓呯悊鎭?pending | 鏃㈡湁 F07 鍊哄姟锛涢〉闈㈠瀹炴樉绀衡€滃凡鍋滄鑷姩鍒锋柊鈥?| F07 |
| 鐪熷疄娴忚鍣?DOM / 鍍忕礌/濯掍綋鏌ヨ | 鐜鏃?Playwright/Chromium锛坄ENV.md` 搂1锛?| 鍏ㄦ壒娆℃棦瀹氭墜娈?|
| 鐪熷疄鍘傚晢妯″瀷/鍑嵁/GPU | 鍙湁 `dev-stub` | F14/T090 |
| `/chat` 姝ｆ枃褰?Run 杈撳叆 `node_input_invalid` | 宸茬煡闆嗘垚缂哄彛锛坄ENV.md` 搂8锛夛紱鏈エ涓嶇粫杩?| F08/F04B 鏃㈡湁缂哄彛 |
| `/editor`銆乣/debug` 鐨勫搷搴斿紡甯冨眬 | 绁ㄩ潰鏄庣‘涓嶅仛锛涘彧鍦ㄩ〉闈㈠０鏄庤竟鐣?| 鏄庣‘涓嶅湪 T098 |

## 9. 澶嶈窇涓庤鍐?
- 澶嶈窇鍛戒护涓?搂5 鐩稿悓锛涘厛鍗?`E:\Orchara\.scratch\f12\locks\realstack`锛岀粨鏉熷垹闄ゃ€?- 瑁佸喅锛歍098 鐨勪簲鏉?AC 鍦ㄢ€滅湡瀹炴爤 + 宸叉彁浜ら〉闈㈠瓧鑺傗€濅笂鍧囨湁鎵ц鍨嬭瘉鎹紱鍞竴涓嶈兘鐢辨湰鐜璇佹槑鐨勬槸
  **鐪熷疄娴忚鍣ㄥ竷灞€/瑙︽帶**锛屽凡鏄庣‘鏍?`not_run`锛屾病鏈夋妸闈欐€?CSS 瑙勫垯鎴?Node shim 鍐欐垚鐪熷疄娓叉煋閫氳繃銆?- 椤甸潰涓庢爤娴嬭瘯鐨勬柇瑷€鍊笺€佸師濮?tail銆佺増鏈?hash/profile銆乻tub/real 鍒嗙晫涓庢湭淇」鍧囧湪鏈姤鍛婂唴銆?