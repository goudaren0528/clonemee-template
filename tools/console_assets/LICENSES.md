---
type: asset-licenses
slug: assistant-console-avatars
status: active
updated: 2026-10-20
summary: "六个离线 DiceBear Pixel Art 固定形象的官方来源、字节指纹与 CC0/MIT 许可证据"
---

# 形象资产来源与许可

六个固定角色形象取自 DiceBear Pixel Art 官方 HTTP API（9.x 通道）。设计按 CC0 1.0、代码按 MIT 授权；许可依据是 GitHub 固定 commit 上的 LICENSE 文件，不是原型页文字，也不是 SVG 自带 metadata。仓库里只保存清洗后的静态 SVG，运行时不再访问网络。

## 1. 获取记录

- 获取方式：直接请求官方接口，一次一个角色；不走 npm、不装包、不用第三方镜像
- 请求基址：`https://api.dicebear.com/9.x/pixel-art/svg?seed=assistant-<slug>`
- 官方接口返回类型为 `image/svg+xml`，文件名 `avatar.svg`（复核时核对）
- 落盘位置（构建期临时目录，不入库）：`%TEMP%\opencode\assistant-avatars\<slug>.svg`

| slug | 角色 | seed | 原始字节 | 原始 sha256 |
|---|---|---|---|---|
| research | 调研 | `assistant-research` | 2493 | `22f4295ac37ac3d2557707f85a0ed011e16f46931a1b737dfb6900fad201789b` |
| product | 产品 | `assistant-product` | 1598 | `5e7a3c45ec17491db35dc44b5a022f0cf0e8afe0f41fe15b9d3f0653cbcfcb42` |
| pm | 跟进 | `assistant-pm` | 1631 | `302a38464dd567805e3d73689cb9977c1c4b5be1744ca2d918b406c9e8cb01c6` |
| investigate | 排查 | `assistant-investigate` | 1542 | `2d4d6c8f7d4c00a5dd3304e7e8310026454d5bfb6d872808ebe0f1eac751f369` |
| dev | 开发 | `assistant-dev` | 1549 | `abbda3580550624ae3a9052c6a5d16f5c5efda01a0825816bfaf2777eca3a9d9` |
| accept | 验收 | `assistant-accept` | 1448 | `3fb4a3bf090534259392756b1175d83ad0742c56aac793e1b0124bb9d7e30438` |

复核方法：下载后逐个重算 sha256，与本文登记的期望值逐一比对。

## 2. 版本边界（必须分开看的两件事）

**获取通道是 API 9.x，不是某个具体 patch。** 接口路径只有大版本 `9.x`，响应里也没有任何版本号或构建号字段（实际响应头为 CDN/Server、`Date`、`Content-Type`、`Content-Length`、`Cache-Control`、`Vary`、`x-robots-tag` 等，无版本信息）。所以本文件不声明、也不能声明这些资产由 9.4.3 生成。

**源码审计基线是 v9.4.3。** 许可和生成逻辑的核对固定在 commit `dc3dc40438791cd0bec9ef50512f2baeed53a633`（tag `v9.4.3`，`packages/@dicebear/pixel-art` 与 `packages/@dicebear/core` 的版本均为 9.4.3）。这个基线只用于许可认定和结构阅读，不构成「资产版本 = 9.4.3」的证据。

两句合并成一句可引用的话：资产经官方 API 9.x 取得，许可以 v9.4.3 源码基线核实。

字节可复现性：同一 seed、同一 9.x 算法下目前能复现上表 hash；若上游改动 9.x 的实现或依赖，字节会变，到时必须重新登记 hash，不能沿用旧值声称一致。

## 3. 许可证据

| 项 | 值 |
|---|---|
| 设计许可 | CC0 1.0（`https://creativecommons.org/publicdomain/zero/1.0/`） |
| 设计者 | DiceBear（`https://www.dicebear.com`） |
| 设计源 | `https://www.figma.com/community/file/1198754108850888330` |
| 代码许可 | MIT，`Copyright (c) 2024 Florian Körner` |
| 官方样式页 | `https://www.dicebear.com/styles/pixel-art/` |
| 源码基线 | commit `dc3dc40438791cd0bec9ef50512f2baeed53a633`（tag `v9.4.3`） |

固定 commit 上的 LICENSE 文件（永久链接，不随上游改动）：

- 设计：`https://raw.githubusercontent.com/dicebear/dicebear/dc3dc40438791cd0bec9ef50512f2baeed53a633/packages/@dicebear/pixel-art/LICENSE`
  大小 1397 字节，sha256 `00c3ee5db6054cecade0208802a41ad1a0fb3d12c7ea152e104bac39cf1e8a2c`
- 代码：`https://raw.githubusercontent.com/dicebear/dicebear/dc3dc40438791cd0bec9ef50512f2baeed53a633/packages/@dicebear/core/LICENSE`
  大小 1072 字节，sha256 `c239922b5a7a4fadaacb98746d1f49ca9e1c765a703b27beaf2d765cef6cbe7f`

旁证（只作旁证，不单独作为许可依据）：每个接口返回的 SVG 自带 metadata，其中 `dcterms:license` 指向 CC0 1.0，`dc:rights` 写明「Pixel Art … by DiceBear, licensed under CC0 1.0」，`dc:source` 指向上面那个 Figma 文件。该 metadata 已按第 5 节删除，不再进入产物。

## 4. seed 与角色对应

| avatarKey / slug | 角色 | 说明 |
|---|---|---|
| `research` | 调研 | 固定形象，不随任务变化 |
| `product` | 产品 | 固定形象，不随任务变化 |
| `pm` | 跟进 | 固定形象，不随任务变化 |
| `investigate` | 排查 | 固定形象，不随任务变化 |
| `dev` | 开发 | 固定形象，不随任务变化 |
| `accept` | 验收 | 固定形象，不随任务变化 |

角色 ID 与 `avatarKey` 都是上表 slug；未知角色不取新图，由壳层给中性占位。

## 5. 清洗动作（原始 → 内嵌）

1. 删除整棵 RDF metadata（`metadata` 元素及其下 `rdf:RDF`、`rdf:Description`、`dc:title`、`dc:creator`、`dc:source`、`dcterms:license`、`dc:rights`）。这段只有声明文本，不含任何绘制内容。
2. 用默认 SVG 命名空间重新序列化，保证根元素形如 `<svg xmlns="http://www.w3.org/2000/svg" …>`，并确认产物中不出现 `ns0` / `ns1` 之类前缀。
3. 不重绘、不缩放、不改色、不调整元素顺序；所有 `path` 的 `d`、`fill`、`fill-opacity` 与原始响应逐字一致。
4. 以 UTF-8 无 BOM 写入 `tools/console_assets/avatars.json`：顶层是纯对象，键为 6 个 slug，值为 SVG 字符串，无额外包装字段。

清洗后逐角色登记：

| slug | 清洗后字节 | 清洗后 sha256 | 元素统计 |
|---|---|---|---|
| research | 1768 | `3bd2a2aac865de2a0c9f8119891b6dd8d7d4475abd784a0b4eadfe588acf541c` | svg 1、mask 1、rect 1、g 2、path 17 |
| product | 866 | `8fec2a0a5899852fb9515747e11bd2d38dae428e298e1815d5bdb52cfc7a5bab` | svg 1、mask 1、rect 1、g 1、path 10 |
| pm | 899 | `1b384b3e7a94705a36924120464c4cf2e7784befe714624f2989d065cb767321` | svg 1、mask 1、rect 1、g 1、path 10 |
| investigate | 808 | `ecc8f0605a785d0c38503558af38f7bb8a3a7ef46d11faeb712b5be64c8f0a84` | svg 1、mask 1、rect 1、g 1、path 8 |
| dev | 816 | `71799a411cad1e6e7748d2eafc59176f84f199d991067c3c96597201175cf00e` | svg 1、mask 1、rect 1、g 1、path 9 |
| accept | 714 | `eb3e225540492a30c07484f66107803610b2150d7dd06c4ce050654386d813e7` | svg 1、mask 1、rect 1、g 2、path 8 |

整份 `avatars.json`：6405 字节，sha256 `8e06181b450a77e5bedbf9f03052203487d2292778182c95f5003f58a6162662`。

## 6. 结构审计（有限白名单）

允许的元素只有五种：`svg`、`mask`、`rect`、`g`、`path`。每个文件里每种元素的个数见上表，元素总数在 12 到 22 之间。

允许的属性按元素分别是：

- `svg`：`xmlns`、`viewBox`、`fill`、`shape-rendering`
- `mask`：`id`
- `rect`：`width`、`height`、`rx`、`ry`、`x`、`y`、`fill`
- `g`：`mask`、`fill`
- `path`：`d`、`fill`、`fill-opacity`

取值规则：`fill` 只允许 `none` 或 `#` 开头的十六进制颜色（3 位、6 位、8 位）；`fill-opacity` 只允许 0 到 1 的数值；`mask` 只允许 `url(#viewboxMask)`；`id` 只允许 `viewboxMask`；`viewBox` 固定 `0 0 16 16`；`shape-rendering` 只允许 `crispEdges`；`d` 只由路径指令字母、数字、逗号、点、正负号和空格组成。

已逐文件确认不存在：`script`、`style`、`foreignObject`、`image`、`use`、`a`、`title`、`desc`、`text`、`animate`、`set`、DTD 与 `ENTITY` 声明、XML 处理指令、注释、任何 `on*` 事件属性、`href`/`xlink` 引用、`style` 属性，以及 `http:`、`https:`、`data:`、`javascript:`、协议相对 `//` 之类外部引用。根元素 `xmlns` 里的 `http://www.w3.org/2000/svg` 是命名空间标识，不算外部引用；除它之外产物中没有任何协议串。元素和属性之外没有文本节点，也没有带命名空间的属性。

`mask` 说明：唯一一个 `mask` 是 16×16 满幅纯白矩形，等价于恒等遮罩，不裁剪掉任何内容；它的 `id` 固定为 `viewboxMask`，页面壳层会按实例重命名，避免同页多个形象互相引用。资产本身保留原名，不做改名。

这里得到的是「本产物落在这份白名单内」的结论，不是对任意 SVG 的通用安全性证明。

## 7. 代码许可正文（MIT，逐字）

以下文本取自 `packages/@dicebear/pixel-art/LICENSE` 的 `# Code` 小节：

```text
MIT License

Copyright (c) 2024 Florian Körner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`packages/@dicebear/core/LICENSE` 的正文与此段完全相同，只是没有设计/代码分节标题，所以两份文件 sha256 不同。本项目没有安装或分发 DiceBear 的代码包；资产准备时通过官方 API 获取静态 SVG，控制台生成与运行均不调用该 API。

## 8. 设计许可证据（CC0 1.0）

`packages/@dicebear/pixel-art/LICENSE` 原文的分节：

```text
Licenses
--------

# Design
Applies to all design files in this package.

Source: Pixel Art (https://www.figma.com/community/file/1198754108850888330)
Designer: DiceBear (https://www.dicebear.com)
License: CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)

# Code
...
```

也就是说：设计文件按 CC0 1.0 释出，代码按 MIT。官方样式页 `https://www.dicebear.com/styles/pixel-art/` 表述一致。CC0 1.0 全文与法律文本见 `https://creativecommons.org/publicdomain/zero/1.0/legalcode`，本文件不复述全文。

CC0 不要求署名。本项目仍在角色管理页标注形象来源与许可（PRD 4.6、AC-9），这是自愿标注，不是许可义务。

## 9. 使用约束

- 形象随 HTML 内嵌，页面运行时不请求 DiceBear 接口、CDN、字体或图片，断网可用。
- SVG 只作为受控字符串注入数据节点，不拼进 HTML 片段，不当 HTML 解析。
- 换 seed、换样式或上游大版本变化时，本文件与 `avatars.json` 一起更新并重新登记原始 hash 与清洗后 hash。
- 不把 metadata 里的来源文本当作许可依据；许可只认第 3 节的 LICENSE 文件。

## 10. 复核方法

```text
curl -s "https://api.dicebear.com/9.x/pixel-art/svg?seed=assistant-research" | sha256sum
curl -s "https://raw.githubusercontent.com/dicebear/dicebear/dc3dc40438791cd0bec9ef50512f2baeed53a633/packages/@dicebear/pixel-art/LICENSE" | sha256sum
```

第一条应与第 1 节的原始 hash 一致；第二条应与第 3 节的 LICENSE hash 一致。清洗后 hash 用第 5 节的字节流对 `avatars.json` 中对应字符串做 UTF-8 编码后计算。
