// RS-A 品牌客户端半边 (dsh.client, platform: web)。
// 零构建链: 直接以 __ModuleLoader__ bundle 形式手写 (与官方包产物同构)。
//
// 职责:
//   1. 覆盖三个 brand slot (动态注册优先级数值低于 shipped -> single slot 胜出):
//      sidebar.brand.mark / sidebar.brand.name / conversation.hero.brand.mark
//   2. locale 覆盖 conversation 命名空间的 hero 文案:
//      hero.headline "探索未至之境" -> "俯瞰世界"; hero.preview "预览版" -> ""
//   3. 页面 title 动态拦截: "... — DeepSeek Harness" -> "... — RS-A"
//      (title 随会话名变, 静态注入无效, 用 title 节点 MutationObserver)
window.__ModuleLoader__.load({
	id: "@rs/remote-sensing-tools",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		var React = require("react");

		function BrandName() {
			return React.createElement(
				"span",
				{ style: { fontWeight: 600, letterSpacing: "0.02em" } },
				"RS-A",
			);
		}

		function BrandMark(_props) {
			// 🛰 卫星标 — 主题契合遥感, 免 SVG 资产依赖
			return React.createElement(
				"span",
				{ style: { fontSize: "1.15em", lineHeight: 1 } },
				"🛰",
			);
		}

		function apply(ctx) {
			// single slot 语义: lowest priority renders — official 占 0, 我们 -1 完成遮蔽
			// ⚠ 并发 boot 竞态: dsh 前端用 Promise.all 并发应用各插件 client bundle,
			// 本 bundle 若先于 conversation/sidebar 包执行, slot 声明表还不存在,
			// register 抛 "slot ... is not declared" → 整个 entry 失败 → 全屏
			// "Failed to load plugins" 面板挡死 UI (WebView2 冷缓存必现, 42 号实测;
			// Chrome 热缓存只是竞态恰好赢)。slot 注册表是响应式的, 晚注册即重渲染
			// —— 退避重试且绝不向上抛: 最坏情形丢品牌, 绝不挡死界面。
			var shadow = function (name, comp) {
				var tries = 0;
				var attempt = function () {
					try {
						ctx.slots.register({ name: name, priority: -1 }, comp);
					} catch (e) {
						if (++tries < 60) { setTimeout(attempt, 200); return; }
						if (typeof console !== "undefined") console.warn("[RS-A brand] slot 注册放弃:", name, e && e.message);
					}
				};
				attempt();
			};
			shadow("sidebar.brand.mark", BrandMark);
			shadow("sidebar.brand.name", BrandName);
			shadow("conversation.hero.brand.mark", BrandMark);

			// 文案替换 (title + hero 标语): locale.register 对同 ns+locale 抛错、
			// 无覆盖通道, 故走 DOM 文本替换; 替换后不再命中关键词, 观察者自然收敛。
			// 另: "预览版"文字置空后其徽标容器(带边框小框, hero 右上角残留横杠)
			// 仍在 —— 用语义类段 _previewBadge 注入 CSS 连容器一起根除。
			var st = document.createElement("style");
			st.textContent = '[class*="_previewBadge"]{display:none!important}';
			(document.head || document.documentElement).appendChild(st);

			var RULES = [
				[/DeepSeek Harness/g, "RS-A"],
				[/探索未至之境/g, "俯瞰世界"],
				[/预览版/g, ""],
				[/推理等级\s*(Default|High|Medium)(?![\w])/g, "思考 · 开"],
				[/推理等级\s*(Low|Minimal|None|Off)(?![\w])/g, "思考 · 关"],
			];
			// 等级值两态大写 (仅用于推理等级专用元素/属性, 不进全文 RULES
			// 防止误伤对话正文里出现的 "High")
			var EFFORT_MAP = { "Default": "ON", "High": "ON", "Medium": "ON",
				"Minimal": "ON", "Low": "ON", "Off": "OFF", "off": "OFF", "on": "ON" };
			var rewriteTree = function (root) {
				var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
				var n;
				while ((n = walker.nextNode())) {
					var t = n.textContent;
					if (!t || (!t.includes("DeepSeek") && !t.includes("探索") && !t.includes("预览版") && !t.includes("推理等级"))) continue;
					var next = t;
					for (var i = 0; i < RULES.length; i++) next = next.replace(RULES[i][0], RULES[i][1]);
					if (next !== t) n.textContent = next;
				}
			};
			var rewriteAttrs = function (root) {
				var els = root.querySelectorAll("[aria-label], [title]");
				for (var i = 0; i < els.length; i++) {
					var el = els[i];
					["aria-label", "title"].forEach(function (a) {
						var v = el.getAttribute(a);
						if (v && v.indexOf("推理等级") !== -1) {
							for (var k = 0; k < RULES.length; k++) v = v.replace(RULES[k][0], RULES[k][1]);
							el.setAttribute(a, v);
						}
					});
				}
			};
			function patchEffortLabels(root) {
				// 推理等级的当前值显示 (触发器与选中项) —— 类名语义段定位
				var els = root.querySelectorAll('[class*="_triggerEffort"], [class*="_cellValue"]');
				for (var i = 0; i < els.length; i++) {
					var txt = (els[i].textContent || "").trim();
					if (EFFORT_MAP[txt] && els[i].textContent !== EFFORT_MAP[txt]) {
						els[i].textContent = EFFORT_MAP[txt];
					}
				}
				var titled = root.querySelectorAll("[title]");
				for (var j = 0; j < titled.length; j++) {
					var v = titled[j].getAttribute("title");
					if (v && /(High|Default|Medium|Low|Minimal|Off|off|on)/.test(v)) {
						titled[j].setAttribute("title", v.replace(/(High|Default|Medium|Low|Minimal)/g, "ON").replace(/(Off|off)/g, "OFF").replace(/on/g, "ON"));
					}
				}
			}
			var sweep = function () {
				rewriteTree(document);
				rewriteAttrs(document);
				patchEffortLabels(document);
				// shadow DOM 里的文本一并处理
				var stack = [document];
				while (stack.length) {
					var cur = stack.pop();
					for (var _i = 0, _a = cur.querySelectorAll("*"); _i < _a.length; _i++) {
						var el = _a[_i];
						if (el.shadowRoot) {
							rewriteTree(el.shadowRoot);
							stack.push(el.shadowRoot);
						}
					}
				}
			};
			sweep();
			new MutationObserver(function () {
				try { sweep(); } catch (e) { /* 隔离: 单功能异常不拖垮其余润色 */ }
				try { patchSessionExport(); } catch (e) { }
				try { injectSettingsEntry(); } catch (e) { }
				try { setupInputHistory(); } catch (e) { }
				try { syncCredTab(); } catch (e) { }
				try { patchReasoningMenu(); } catch (e) { }
			}).observe(
				document.documentElement, { subtree: true, childList: true, characterData: true });

			// ---- Session log 导出兜底 (下载器劫持修复) ----
			// dsh 原生流程把 GET URL 交给浏览器下载管理器; 本机第三方下载器
			// (IDM/迅雷类) 接管后对查询串 URL 报"无法从网站上提取文件"。
			// 改道: 拦截按钮点击 -> fetch -> blob -> a[download] 触发原生保存,
			// blob: URL 不被下载器劫持。sessionId 从页面自身 API 调用里捕获。
			var lastSessionId = "";
			var origFetch = window.fetch.bind(window);
			window.fetch = function (input, init) {
				try {
					var url = typeof input === "string" ? input : (input && input.url) || "";
					if (url.indexOf("/api/session.export") !== -1) {
						var m = /[?&]sessionId=([^&]+)/.exec(url);
						if (m) lastSessionId = decodeURIComponent(m[1]);
					} else if (url.indexOf("/api/session.history") !== -1 && init && init.body) {
						var sid = JSON.parse(init.body).sessionId;
						if (sid) lastSessionId = sid;
					}
				} catch (e) { /* 观测旁路: 失败不影响原请求 */ }
				return origFetch(input, init);
			};

			function exportToast(msg, ok) {
				var d = document.createElement("div");
				d.textContent = msg;
				d.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
					"z-index:2147483647;padding:8px 16px;border-radius:8px;font:13px sans-serif;" +
					"background:" + (ok ? "#e8eaed" : "#3a3d42") + ";color:" + (ok ? "#14161a" : "#f1f3f5") + ";box-shadow:0 4px 12px rgba(0,0,0,.3)";
				document.body.appendChild(d);
				setTimeout(function () { d.remove(); }, ok ? 2500 : 6000);
			}

			function blobExport(btn) {
				if (!lastSessionId) { exportToast("还未捕获到会话 ID, 请先切换一次会话再试", false); return; }
				var url = "/api/session.export?sessionId=" + encodeURIComponent(lastSessionId) +
					"&includeDescendants=true";
				origFetch(url).then(function (r) {
					if (!r.ok) throw new Error("HTTP " + r.status);
					return r.blob();
				}).then(function (b) {
					var a = document.createElement("a");
					a.href = URL.createObjectURL(b);
					a.download = "dsh-session-" + lastSessionId + ".zip";
					document.body.appendChild(a);
					a.click();
					setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
					exportToast("会话日志已保存 (ZIP " + Math.round(b.size / 1024) + " KB)", true);
				}).catch(function (e) {
					exportToast("导出失败: " + e.message, false);
				});
			}

			function patchSessionExport() {
				var stack = [document];
				while (stack.length) {
					var cur = stack.pop();
					var els = cur.querySelectorAll("button");
					for (var i = 0; i < els.length; i++) {
						var b = els[i];
						if (b.getAttribute("data-rs-export") === "1") continue;
						if ((b.textContent || "").trim() === "Session log") {
							b.setAttribute("data-rs-export", "1");
							b.addEventListener("click", function (ev) {
								ev.stopPropagation(); ev.preventDefault();
								blobExport(this);
							}, true); // capture: 抢在 React 合成事件前接管
						}
					}
					var all = cur.querySelectorAll("*");
					for (var j = 0; j < all.length; j++) {
						if (all[j].shadowRoot) stack.push(all[j].shadowRoot);
					}
				}
			}
			patchSessionExport();
			setupCredentialPanel();
			setInterval(function () {
				try { patchEffortLabels(document); } catch (e) { }
			}, 800);

			// ---- 推理等级菜单收敛为 off/on 两项 ----
			// dsh 原生六挡 (Default/Off/Minimal/Low/Medium/High) 对 M3 无意义
			// (代理层已翻译为开/关), 菜单同步收敛: 保留 Off->off / High->on,
			// 其余隐藏。菜单每次展开重建, observer 幂等补丁。
			function patchReasoningMenu() {
				var KEEP = { "Off": "OFF", "High": "ON" };
				var HIDE = ["Default", "Minimal", "Low", "Medium"];
				var stack = [document];
				while (stack.length) {
					var cur = stack.pop();
					var items = cur.querySelectorAll('[role="menuitemradio"]');
					for (var i = 0; i < items.length; i++) {
						var it = items[i];
						var txt = (it.textContent || "").trim();
						if (HIDE.indexOf(txt) !== -1) { it.style.display = "none"; continue; }
						if (KEEP[txt] && it.getAttribute("data-rs-effort") !== "1") {
							it.setAttribute("data-rs-effort", "1");
							var walker = document.createTreeWalker(it, NodeFilter.SHOW_TEXT);
							var n;
							while ((n = walker.nextNode())) {
								if (n.textContent.trim() === txt) n.textContent = KEEP[txt];
							}
						}
					}
					var all = cur.querySelectorAll("*");
					for (var j = 0; j < all.length; j++) {
						if (all[j].shadowRoot) stack.push(all[j].shadowRoot);
					}
				}
			}
			setupInputHistory();

			// ---- 输入框上下键历史消息 (终端式) ----
			// 光标在起点按 ArrowUp 依次回填本会话已发送消息 (Enter 时入栈),
			// ArrowDown 反向, 退到最新恢复发送前草稿; 多行编辑中 ArrowUp 仍作
			// 光标移动不劫持。
			// 历史栈持久化 + 按 uid 隔离: key=rs_input_history:{uid}
			// (uid 在首次 authFor 时缓存); 内存栈刷新即失, localStorage 兜底。
			var hist = { stack: [], idx: -1, draft: "", loadedUid: "" };

			function histKey(uid) { return "rs_input_history:" + uid; }

			function ensureHistLoaded() {
				var uid = window.__rsCurrentUid || "rs-a-user";
				if (hist.loadedUid === uid) return;
				hist.loadedUid = uid;
				hist.stack = []; hist.idx = -1; hist.draft = "";
				try {
					var arr = JSON.parse(localStorage.getItem(histKey(uid)) || "[]");
					if (Array.isArray(arr)) hist.stack = arr;
				} catch (e) { }
			}
			var _setVal = Object.getOwnPropertyDescriptor(
				HTMLTextAreaElement.prototype, "value").set;

			function pushHistory(v) {
				ensureHistLoaded();
				if (hist.stack[hist.stack.length - 1] === v) { hist.idx = -1; return; }
				hist.stack.push(v);
				if (hist.stack.length > 50) hist.stack.shift();
				hist.idx = -1;
				var uid = window.__rsCurrentUid || "rs-a-user";
				try { localStorage.setItem(histKey(uid), JSON.stringify(hist.stack)); } catch (e) { }
			}

			function setInputVal(t, v) {
				_setVal.call(t, v); // React 受控组件需原生 setter + input 事件同步
				t.dispatchEvent(new Event("input", { bubbles: true }));
				t.selectionStart = t.selectionEnd = v.length;
			}

			function bindHistory(t) {
				t.setAttribute("data-rs-hist", "1");
				t.addEventListener("keydown", function (ev) {
					if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && t.value.trim()) {
						pushHistory(t.value);
						return;
					}
					var browsing = hist.idx >= 0;
					if (ev.key === "ArrowUp" && (browsing || (t.selectionStart === 0 && t.selectionEnd === 0))) {
						ensureHistLoaded();
						if (!hist.stack.length) return;
						if (!browsing) { hist.draft = t.value; hist.idx = hist.stack.length - 1; }
						else if (hist.idx > 0) hist.idx--;
						else return; // 已到最早一条
						ev.preventDefault();
						setInputVal(t, hist.stack[hist.idx]);
					} else if (ev.key === "ArrowDown" && browsing) {
						ev.preventDefault();
						if (hist.idx < hist.stack.length - 1) {
							hist.idx++;
							setInputVal(t, hist.stack[hist.idx]);
						} else {
							hist.idx = -1;
							setInputVal(t, hist.draft);
						}
					}
				}, true);
			}

			function setupInputHistory() {
				var stack = [document];
				while (stack.length) {
					var cur = stack.pop();
					var tas = cur.querySelectorAll("textarea");
					for (var i = 0; i < tas.length; i++) {
						var t = tas[i];
						if (t.getAttribute("data-rs-hist") === "1") continue;
						var tag = (t.getAttribute("aria-label") || t.placeholder || "");
						if (tag.indexOf("消息") !== -1 || tag.indexOf("发消息") !== -1) bindHistory(t);
					}
					var all = cur.querySelectorAll("*");
					for (var j = 0; j < all.length; j++) {
						if (all[j].shadowRoot) stack.push(all[j].shadowRoot);
					}
				}
			}

			// 入口注入 (apply 作用域, observer 回调直接可达; 打开动作经
			// window 桥进入 setupCredentialPanel 闭包):
			// 克隆左侧导航 (通用设置/模型/插件/Agent 预设) 的最后一项作原型 ——
			// 图标 (白色线性 SVG) / 类名 / 尺寸全部与原页一致, 升级免疫。
			function injectSettingsEntry() {
				var dlg = document.querySelector('[role="dialog"]');
				if (!dlg || dlg.querySelector("#rs-cred-entry")) return;
				if (!dlg.textContent || dlg.textContent.indexOf("通用设置") === -1) return;
				var navList = dlg.querySelector('[class*="_navList"]');
				if (!navList) {
					var nav = dlg.querySelector("nav");
					if (nav) navList = nav.lastElementChild;
				}
				if (!navList) return;
				var cells = navList.querySelectorAll("button");
				if (!cells.length) return;
				var proto = cells[cells.length - 1];
				var clone = proto.cloneNode(true);
				clone.id = "rs-cred-entry";
				// 去激活态类 (类名含 active 的 token), 清 React 状态属性
				clone.className = proto.className.split(/\s+/)
					.filter(function (c) { return c && c.toLowerCase().indexOf("active") === -1; })
					.join(" ");
				clone.removeAttribute("aria-current");
				clone.removeAttribute("disabled");
				var label = clone.querySelector('[class*="_navLabel"], span');
				if (label) label.textContent = "凭证管理";
				var activeCell = navList.querySelector("button[class*='active']");
				if (activeCell) {
					var m = String(activeCell.className).match(/\S*active\S*/g);
					if (m) clone.setAttribute("data-rs-active", m.join(" "));
				}
				clone.addEventListener("click", function (ev) {
					ev.stopPropagation();
					selectCredTab();
				});
				navList.appendChild(clone);
				syncCredTab();
			}

			// ---- 凭证管理右侧内容页 (与其他设置项同交互) ----
			// 选中态由本插件维护: 点导航项 -> 原生项去 active、content 原生
			// 子元素隐藏并注入 #rs-cred-page; 用户点回原生 tab 时 React 重
			// 渲染会移除克隆项, observer 重新注入即回到未选中态。
			function _navList(dlg) {
				return dlg.querySelector('[class*="_navList"]') ||
					(dlg.querySelector("nav") || {}).lastElementChild;
			}
			function _stripActive(cls) {
				return String(cls).split(/\s+/).filter(function (c) {
					return c && c.toLowerCase().indexOf("active") === -1;
				}).join(" ");
			}

			function selectCredTab() {
				var dlg = document.querySelector('[role="dialog"]');
				if (!dlg) return;
				var navList = _navList(dlg);
				var ours = dlg.querySelector("#rs-cred-entry");
				if (!navList || !ours) return;
				var activeCls = ours.getAttribute("data-rs-active") || "";
				var cells = navList.querySelectorAll("button");
				for (var i = 0; i < cells.length; i++) {
					cells[i].className = _stripActive(cells[i].className);
				}
				ours.className = (ours.className + " " + activeCls).trim();
				syncCredTab(true);
			}

			function syncCredTab(force) {
				var dlg = document.querySelector('[role="dialog"]');
				if (!dlg) return;
				var ours = dlg.querySelector("#rs-cred-entry");
				if (!ours) {
					// 兜底: 克隆项被 React 重渲染移除 (用户点了原生 tab) -> 清残留页
					var content0 = dlg.querySelector('[class*="_content"]');
					var stale = content0 && content0.querySelector("#rs-cred-page");
					if (stale) stale.remove();
					return;
				}
				var ourActive = String(ours.className).toLowerCase().indexOf("active") !== -1;
				var content = dlg.querySelector('[class*="_content"]');
				if (!content) return;
				// 并存态: 用户点回原生 tab (原生项 active) -> 即刻退出我们的页面
				var navList2 = _navList(dlg);
				if (navList2) {
					var ncs = navList2.querySelectorAll("button");
					for (var q = 0; q < ncs.length; q++) {
						if (ncs[q] !== ours &&
							String(ncs[q].className).toLowerCase().indexOf("active") !== -1) {
							ourActive = false;
							ours.className = _stripActive(ours.className);
							break;
						}
					}
				}
				if (!ourActive) {
					var page = content.querySelector("#rs-cred-page");
					if (page) {
						page.remove();
						for (var k = 0; k < content.children.length; k++) {
							content.children[k].style.display = "";
						}
					}
					return;
				}
				// 选中态: 隐藏原生子元素 (排除我们的页), 确保页面存在
				for (var i = 0; i < content.children.length; i++) {
					if (content.children[i].id !== "rs-cred-page") content.children[i].style.display = "none";
				}
				if (!content.querySelector("#rs-cred-page") || force) {
					var old = content.querySelector("#rs-cred-page");
					if (old) old.remove();
					content.appendChild(buildCredPage());
					// 页面入 DOM 后再刷新状态 (元素可寻址)
					if (window.__rsRefreshCred) window.__rsRefreshCred();
				}
			}

			function buildCredPage() {
				var page = document.createElement("div");
				page.id = "rs-cred-page";
				page.style.cssText = "color:#e8eaed;font:13px sans-serif;padding:4px 2px";
				page.innerHTML =
					'<div style="font-size:16px;font-weight:600;margin-bottom:6px">GEE 凭证管理</div>' +
					'<div style="border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:12px 14px;margin-bottom:14px">' +
					'<div style="font-size:12px;color:#9aa4b2;margin-bottom:6px">本机账号 (记录按用户隔离)</div>' +
					'<div id="rs-uid-now" style="font-size:13px;color:#e8eaed;margin-bottom:8px">…</div>' +
					'<div style="display:flex;gap:8px">' +
					'<input id="rs-uid-new" placeholder="新用户名 (如 xiaoming)" style="flex:1;box-sizing:border-box;background:#111418;color:#e8eaed;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:7px;font:12.5px sans-serif">' +
					'<button id="rs-uid-switch" style="padding:7px 12px;border:none;border-radius:8px;background:#e8eaed;color:#14161a;cursor:pointer;font-size:12px;font-weight:600">创建/切换</button>' +
					'</div>' +
					'<div style="font-size:11px;color:#6b7480;margin-top:6px">新用户需在下方面粘贴 GEE 凭证后保存</div></div>' +
					'<div style="font-size:12.5px;color:#9aa4b2;margin-bottom:16px;line-height:1.6">' +
					"更换 Google Earth Engine Service Account 凭证。Key 仅存本机后端加密盘 (Fernet)," +
					"执行瞬间解密用完即弃, 绝不上传。</div>" +
					'<div id="rs-cred-status" style="border:1px solid rgba(255,255,255,.12);border-radius:10px;' +
					'padding:12px 14px;margin-bottom:14px;color:#9aa4b2">查询中…</div>' +
					'<textarea id="rs-cred-json" placeholder="粘贴 Google Service Account JSON Key 全文" style="width:100%;height:130px;box-sizing:border-box;background:#111418;color:#e8eaed;border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:10px;font:12px monospace"></textarea>' +
					'<input id="rs-cred-proj" placeholder="GEE Project ID (可选)" style="width:100%;box-sizing:border-box;margin-top:8px;background:#111418;color:#e8eaed;border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:10px;font:13px sans-serif">' +
					'<div style="margin-top:14px;display:flex;gap:10px;align-items:center">' +
					'<button id="rs-cred-save" style="padding:9px 22px;border:none;border-radius:8px;background:#e8eaed;color:#14161a;cursor:pointer;font-size:13px;font-weight:600">验证并保存 / 更换</button>' +
					'<span style="font-size:11.5px;color:#6b7480">更换后访问令牌不变, 工具链零中断</span></div>';
				page.querySelector("#rs-cred-save").onclick = function () { if (window.__rsSaveCreds) window.__rsSaveCreds(); };
				page.querySelector("#rs-uid-now").textContent =
					"当前: " + (localStorage.getItem("rs_current_uid") ||
						window.__rsCurrentUid || "rs-a-user");
				page.querySelector("#rs-uid-switch").onclick = function () {
					var nu = page.querySelector("#rs-uid-new").value.trim();
					if (!nu) { exportToast("请输入用户名", false); return; }
					localStorage.setItem("rs_current_uid", nu);
					page.querySelector("#rs-uid-now").textContent = "当前: " + nu;
					exportToast("已切换到 " + nu + " — 若为新用户请粘贴其 GEE 凭证保存", true);
					if (window.__rsRefreshCred) window.__rsRefreshCred();
				};
				return page;
			}

			// ---- GEE 凭证管理面板 (侧栏底部) ----
			// 经同源 /rs-auth-token (TS 插件路由) 取 Bearer -> 直连后端 :8000
			// 查询状态/换绑。凭证 JSON 只进后端加密盘, 浏览器不落存。
			function setupCredentialPanel() {

				// 当前本机账号: localStorage 优先 (多用户切换), 回退插件缺省
				function currentUid() {
					try { return localStorage.getItem("rs_current_uid") || "rs-a-user"; }
					catch (e) { return "rs-a-user"; }
				}
				function authFor(uid) {
					try {
						var m = JSON.parse(localStorage.getItem("rs_uid_tokens") || "{}");
						if (m[uid]) return { token: m[uid], userId: uid };
					} catch (e) { }
					return fetch("/rs-auth-token").then(function (r) {
						if (!r.ok) throw new Error("本地插件路由不可用 (" + r.status + ")");
						return r.json();
					});
				}
				function rsFetch(path, opts) {
					var uid = currentUid();
					return Promise.resolve(authFor(uid)).then(function (auth) {
						opts = opts || {};
						opts.headers = Object.assign({}, opts.headers, {
							"Authorization": "Bearer " + auth.token,
							"Content-Type": "application/json",
						});
						return fetch("http://127.0.0.1:8000" + path, opts);
					});
				}

				function refreshStatus() {
					var el = document.getElementById("rs-cred-status");
					if (!el) return;
					rsFetch("/credentials/status?user_id=" + currentUid()).then(function (r) {
						return r.json();
					}).then(function (s) {
						el.innerHTML = s.bound
							? '\u2705 已绑定: <code style="color:#8ab4f8">' + String(s.email).slice(0, 44) + "</code>"
							: "\u26a0\ufe0f 尚未绑定凭证";
					}).catch(function (e) {
						el.textContent = "后端不可达: " + e.message;
					});
				}

				function saveCreds() {
					var btn = document.getElementById("rs-cred-save");
					var raw = document.getElementById("rs-cred-json").value.trim();
					var proj = document.getElementById("rs-cred-proj").value.trim();
					if (!raw) { exportToast("请先粘贴 Service Account JSON", false); return; }
					var key;
					try { key = JSON.parse(raw); } catch (e) { exportToast("JSON 解析失败: " + e.message, false); return; }
					if (!key.client_email || !key.private_key) { exportToast("缺少 client_email / private_key 字段", false); return; }
					btn.disabled = true; btn.textContent = "保存中…";
					rsFetch("/credentials/replace", {
						method: "POST",
						body: JSON.stringify({
							user_id: currentUid(),
							credentials: {
								service_account_email: key.client_email,
								key_json: raw,
								...(proj ? { gee_project: proj } : {}),
							},
						}),
					}).then(function (r) {
						if (r.status === 401) throw new Error("访问令牌无效");
						if (!r.ok) throw new Error("HTTP " + r.status);
						exportToast("GEE 凭证已更新 \u2705 (访问令牌不变, 工具零中断)", true);
						document.getElementById("rs-cred-json").value = "";
						refreshStatus();
				}).catch(function (e) {
					exportToast("保存失败: " + e.message, false);
				}).finally(function () {
					btn.disabled = false; btn.textContent = "验证并保存 / 更换";
				});
				}

				// 右侧内容页桥: 页面元素经 window 进入本闭包取状态/保存动作
				window.__rsRefreshCred = refreshStatus;
				window.__rsSaveCreds = saveCreds;
			}
		}

		exports.apply = apply;
		exports.inject = ["slots"];
		return module.exports;
	},
});
