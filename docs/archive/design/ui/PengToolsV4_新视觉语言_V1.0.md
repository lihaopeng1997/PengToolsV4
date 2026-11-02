# PengToolsV4 新视觉语言 V1.0

> 冻结日期：2026-08-14  
> 来源：ui-ux-pro-max（developer tools / IDE，VARIANCE=4 MOTION=2 DENSITY=7）+ Taste / Impeccable 收紧 + 产品离线约束  
> 实现入口：`ui/theme_manager.py` → `resources/style.qss`

## 1. 命题

Windows 离线个人开发/运维工作台。气质：沉静、密而不挤、长时间不刺眼。  
四套主题同一字阶/间距/圆角，只换色相。

| ID | 中文 | 角色 |
|---|---|---|
| `calm` | 静谧办公 | 默认。石墨鼠尾草 |
| `clear` | 晴空清晰 | 冷钢蓝灰 |
| `warm` | 暖书房 | 纸感棕调 |
| `black` | 墨黑 | 近黑分层。替换原 `night`（夜间安读） |

旧设置 `ui_theme=night` 必须映射为 `black`。

## 2. 共享尺度

- 字号：11 / 12 / 13 / 14 / 16 / 20 / 24
- 字体：界面 Microsoft YaHei UI / Segoe UI；代码 Consolas 等宽
- 间距：4px 栅格；页边 24 / 20 / 16
- 圆角：控件 8、卡片 12、对话框 16
- 动效：hover/focus 150–250ms；禁止背景粒子与玻璃拟态

## 3. Token（冻结）

### 3.1 calm

| Token | Hex |
|---|---|
| APP_BG | `#F3F2EC` |
| SIDEBAR_BG | `#F8F7F2` |
| SURFACE | `#FFFEFB` |
| TEXT_STRONG | `#1A1F1C` |
| TEXT | `#3A423D` |
| TEXT_MUTED | `#6B746E` |
| BORDER | `#DDDAD2` |
| PRIMARY | `#3F6B56` |
| PRIMARY_SOFT | `#E4EFE8` |
| PRIMARY_ACTIVE | `#2F5342` |

### 3.2 clear

| Token | Hex |
|---|---|
| APP_BG | `#F2F4F7` |
| SIDEBAR_BG | `#F7F9FC` |
| SURFACE | `#FFFFFF` |
| TEXT_STRONG | `#161D26` |
| TEXT | `#38424E` |
| TEXT_MUTED | `#667486` |
| BORDER | `#D7DEE7` |
| PRIMARY | `#3A5770` |
| PRIMARY_SOFT | `#E6EEF5` |
| PRIMARY_ACTIVE | `#2C4559` |

### 3.3 warm

| Token | Hex |
|---|---|
| APP_BG | `#F6F2EA` |
| SIDEBAR_BG | `#FBF8F2` |
| SURFACE | `#FFFCF7` |
| TEXT_STRONG | `#241C16` |
| TEXT | `#4A3E33` |
| TEXT_MUTED | `#7A6C5C` |
| BORDER | `#E6DCCE` |
| PRIMARY | `#7A5133` |
| PRIMARY_SOFT | `#F1E6D8` |
| PRIMARY_ACTIVE | `#5E3C25` |

### 3.4 black

| Token | Hex |
|---|---|
| APP_BG | `#09090B` |
| SIDEBAR_BG | `#111114` |
| SURFACE | `#161618` |
| ELEVATED_SURFACE | `#1E1E22` |
| CODE_BG / INPUT_BG | `#070708` / `#0E0E10` |
| TEXT_STRONG | `#F4F4F5` |
| TEXT | `#C8C8CC` |
| TEXT_MUTED | `#8A8A90` |
| BORDER | `#2A2A2E` |
| PRIMARY | `#8FBB9E` |
| PRIMARY_SOFT | `#152019` |
| PRIMARY_ACTIVE | `#A8CDB4` |
| TABLE_SELECT | `#1E2A22` |
| FOCUS_RING | `#A8CDB4` |
| ON_PRIMARY | `#0A100C` |

层次：`APP_BG` ≤ `SIDEBAR_BG` ≤ `SURFACE` ≤ `ELEVATED_SURFACE`；`CODE_BG` 内凹更深。  
禁止白卡片、禁止薄荷绿大面积铺底。

## 4. Skill 摘要（未照抄 Web 建议）

- ui-ux-pro-max 默认给出 OLED 深色 + JetBrains Mono / IBM Plex + 运行绿。**字体不采用**（离线、无 CDN）。**墨黑吸收其近黑分层**；三套浅色按「Productivity Tool / sage office」中性纸感改写。
- Taste / Impeccable：单一强调色、去饱和、可见焦点、选中底不得亮过正文。
- react-bits：只保留 hover / focus / 已有 Aurora 加载条。
- Karpathy：只改外观入口与三个高频页，不重写业务。

## 5. 反模式

霓虹、玻璃拟态、粒子、大面积渐变、Emoji 图标、AI 紫粉、纯 `#000` 铺底、墨黑主题白卡片。
