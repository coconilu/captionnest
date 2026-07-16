# CaptionNest 品牌规范

## 名称

| 项目 | 规范 |
|---|---|
| 正式名称 | `CaptionNest`，大小写固定 |
| 含义 | `Caption` 表示字幕，`Nest` 表示把识别、翻译和字幕文件安放在一个本地、安全的工作空间里 |
| 主程序 | `captionnest.exe` |
| sidecar | `captionnest-sidecar.exe` |
| Python 发行包 / CLI | `captionnest` |
| 内部 Python 导入路径 | 保留 `sublingo_local`，它不是对外品牌 |

## 图标

图标用一个气泡容纳上下两行字幕：暖白色上行代表源语言，青色下行代表目标语言；深青色底强调本地工具的稳重感。

| 资产 | 用途 |
|---|---|
| `apps/desktop/icons/app-icon.svg` | 唯一规范源文件，允许无损修改和重新导出 |
| `apps/desktop/icons/32x32.png` | Windows 小图标 |
| `apps/desktop/icons/128x128.png` | Windows / Tauri 图标 |
| `apps/desktop/icons/128x128@2x.png` | 高 DPI 图标 |
| `apps/desktop/icons/icon.ico` | EXE、NSIS 安装器和卸载入口 |
| `apps/web/public/favicon.svg` | Web UI favicon 与页头品牌标记；内容应与规范源文件保持一致 |

## 配色

| 名称 | 色值 | 用途 |
|---|---|---|
| Nest 深青 | `#032F3B` / `#064B59` | 图标背景 |
| Caption 青 | `#08D4ED` / `#18E0F5` | 气泡与译文行 |
| Source 暖白 | `#FFF1D2` | 原文行 |
| UI 主色 | `#0B8492` | 白底界面上的可读按钮、焦点和状态 |

## 使用边界

- 不在图标内部加入文字、国旗或具体语言缩写。
- 不拉伸、不旋转、不改变上下两行的语义顺序。
- 发布前从 SVG 重新生成 PNG/ICO，不能复用旧品牌构建缓存。
- Windows 安装器显示的受信任“发布者”最终由代码签名证书主体决定，不由本文件或 Tauri 文案替代。
