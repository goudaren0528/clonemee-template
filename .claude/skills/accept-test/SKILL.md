---
name: accept-test
description: 集成测试（验收 Agent 的 skill，开发交付流水线）。所有工作包合进需求分支后、或修复后复测时使用：按 cases.md 逐条测试——能自动化的写成 e2e 脚本跑，用户能看到的用 Orca 内置浏览器真测并截图，产出测试报告 docs/delivery/<需求>/acceptance/report-r<N>.md。合回 develop 后做「体验准备」：有界面的交付在本地启动好，给你链接和体验要点，验收后关掉。
---

# 集成测试与体验准备 `v1`

验收 Agent 用，在需求集成分支上。**用例已经在开发前定好（cases.md），这里只按用例执行**；测试中发现用例本身有问题，记进报告，不改用例。报告模板 `{assistant}/templates/test-report.md`。

## 准备
1. 读 `prd.md`、`cases.md`；第 2 轮以后读上一轮报告和开发的修复记录
2. 按「测试环境」启动：装依赖、起服务、准备测试数据；记下分支和提交号
3. 先跑一遍项目已有的单元测试和构建，确认基础是好的（失败就直接记进报告）

## e2e（能自动化的用例）
- 项目有 e2e 框架（Playwright、Cypress…）：按用例写脚本，放在项目约定的测试目录，脚本里标用例编号（C-n），跑完记命令和结果
- 项目没有 e2e 框架：不自己引入新框架，这些用例改用浏览器真测，在报告「自动化」里说明

## Orca 内置浏览器真测（用户看得到的用例）
在 Orca 里用 `orca` 命令操作内置浏览器，按用例步骤一步步做：

| 要做的 | 命令 |
|---|---|
| 打开页面 | `orca tab create --url <地址> --json` 或 `orca goto --url <地址> --json` |
| 看页面上有什么（拿元素引用 @e1…） | `orca snapshot --json` |
| 点、填、选 | `orca click --element @e3` · `orca fill --element @e5 --value "…"` · `orca select --element @e7 --value "…"` |
| 按键、等待 | `orca keypress --key Enter` · `orca wait --timeout 3000` |
| 读结果 | `orca get --element @e9 --what text` · `orca is --element @e9 --what visible` |
| 截图留证 | `orca screenshot --format png --json`：结果里是图片数据（base64），解码后存成 `acceptance/shots/C-<n>.png`（先看一次返回结构） |
| 手机、暗色（用例要求时） | `orca set device --name "iPhone 12"` · `orca set media --color-scheme dark` |

浏览器标签属于当前工作区（命令默认作用于当前工作区）。不清楚某个命令的参数就先看 `orca <命令> --help`，或者读 Orca 自带的那份：`orca skills get orca-cli --reference references/browser.md`。每条用例：按步骤操作 → 对照预期 → 截图 → 记结果。P0 用例必须有截图。

**操作循环**：`snapshot`（拿 `@e1…`）→ 动作 → **再 snapshot**。元素 ref 只对一个标签、一次有效，导航 / 切标签 / 点了会改页面的东西之后必须重新 snapshot（报 `browser_stale_ref` 就是重 snapshot 再试）。异步变化用 `orca wait --text / --url / --selector / --load networkidle` 按条件等，不要裸等时间。并发看多个页面时先 `orca tab list --json` 取 `browserPageId`，后面每个命令带 `--page <id>`。

### 打不开 / 点不动的时候（兜底，按顺序试）
| 症状 | 先试 | 还不行 |
|---|---|---|
| 新标签停在 `about:blank`、`orca goto` 没效果 | `orca tab list --json` 确认真开出来 → 重新 `orca tab create --url <地址>` 再 `goto` | 换 Edge |
| 导航超时 | `orca wait --load networkidle --timeout 15000` 分清慢还是死 → `orca console --limit 50` / `orca network --limit 50` 看报错 → 本地服务先 `curl -I <地址>` 确认端口通 | 换 Edge |
| 元素看着在但点不动、`fill` 没反应 | 重新 snapshot 拿新 ref → `orca focus --element @e1` + `orca inserttext --text "…"` → `orca eval` 直接调页面函数 / 派事件 | 换 Edge |
| `browser_host_unavailable` | 托管那台桌面在线再试，或改 server-hosted 页面 | 换 Edge |

**换 Edge**：无头截图用 `msedge --headless=new --disable-gpu --screenshot=<文件> --window-size=1440,900 "<地址>"`，要交互就用 Playwright（`channel: "msedge"`）跑同一套用例步骤；也可以 `orca computer` 驱动 Edge 窗口（先 `orca computer list-apps --json` 拿应用名或 pid，地址栏用 `set-value` 写、`press-key Return`）。**不要用 computer use 操作 Orca 内置浏览器**——那是 `orca goto/snapshot/...` 的地盘（[playbooks/orca.md](../../../playbooks/orca.md)）。

**换了路必须在报告里写清三件事**：实际用了哪条路（内置浏览器 / Edge 无头 / Edge 有头 + computer use）、为什么换（卡在哪一步、什么症状、贴错误码）、证据在哪。截图统一存 `acceptance/shots/`，文件名带用例号（`C-<n>-<步骤>.png`），**证据不进 git**。

## 判定与报告
- 每条用例一个结果：通过 / 失败 / 阻塞（环境起不来、前置不满足）/ 未测（写原因）
- 失败的写清：实际结果、预期、复现步骤、截图或日志
- 报告写到 `acceptance/report-r<N>.md`（`verdict: 通过 | 要修`），和截图、e2e 脚本一起提交到需求分支
- 在终端里说：结论、通过几条、失败几条（P0 几条）、报告路径

## 回归
同步 develop 之后的回归测试：跑单元测试 + 全部 P0 用例；派发说明会写明范围。

## 体验准备（有界面的交付）
你验收时要**点开链接就能用**，不用自己拉代码、装依赖、启动。派发说明写「体验准备」时做（流水线里：合回 develop（本地）之后、推送之前，由跟进 Agent 派；单任务派发没有验收 Agent 时，执行会话按这一节自己做）。

1. **有没有界面**：网页、页面、能点的东西算有界面。没有界面（排查结论、报告、纯接口、脚本）→ 不搭环境，体验卡只写一行「没有界面，不需要体验环境」，结束
2. **启动**：在需求分支的工作区里（和 develop 同一个提交）按「测试环境」构建，用 `experience.py` 在后台启动并等它能打开：
   ```bash
   python {assistant}/tools/experience.py start <项目>-<需求> --cwd <工作区绝对路径> --cmd "<启动命令>" --url http://127.0.0.1:<端口>/
   python {assistant}/tools/experience.py start <项目>-<需求> --url file:///<页面绝对路径>    # 静态页面不用启动
   ```
   只用本地和测试数据，不连生产；地址只监听本机（127.0.0.1）。工具会同时起一个看门狗，当天 24:00 自动关
3. **确认能打开**：用 Orca 内置浏览器打开链接（`orca goto --url <链接>`），看主页面正常显示，截一张图 `acceptance/shots/experience.png`
4. **挑体验要点**：从 cases.md 里挑最能体现需求的 3 条左右（优先 P0、用户主流程），每条一句话：去哪、做什么、应该看到什么
5. **体验卡**：在终端里输出下面这段（流水线里跟进 Agent 会抄进看板「体验」一节，放进「完成」消息；单任务就写进你的做完总结）：
   ```markdown
   **体验卡** · <项目>：<需求>
   - 体验链接：<本地链接>（已启动，到期 <当天 24:00> 自动关）
   - 体验要点：① … ② … ③ …
   - 用例：<通过 N / 共 N，P0 全部通过 / 失败 N 条>（报告 <report-rN.md 的绝对路径>）
   - 关闭：python {assistant}/tools/experience.py stop <项目>-<需求>（你点「验收通过」或「打回」后执行）
   ```
6. **启动不了**：不能悄悄跳过。`experience.py` 会输出卡在哪、手动启动办法和日志末尾；体验卡里照写这几行，结果照样交
7. **关掉**：你「验收通过」或「打回」后，由收到结论的会话（主会话或跟进 Agent）执行体验卡里的关闭命令；`experience.py list` 看本机还开着哪些

## 禁止
- 改业务代码或用例；为了通过调整预期
- 用真实用户数据、生产环境；体验环境连生产、或者开放给本机以外访问
- 没截图、没实际操作就写「通过」
- 有界面的交付不搭体验环境就交「完成」；启动不了时不说
