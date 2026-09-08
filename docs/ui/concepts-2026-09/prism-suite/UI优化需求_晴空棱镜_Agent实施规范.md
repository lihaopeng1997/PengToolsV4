# PengToolsHub 晴空棱镜：全软件 UI 重构开发规格 V2.0

日期：2026-09-08。**本文件是唯一当前实施需求，替代 V1.1；不要求开发 Agent 读取历史对话。** 原型：同目录 index.html。代码基线：D:/PengTools，main，提交 `7c4e0e3fe1c31dbccdeeff12f0631d00b69cddb2`。基线只作核对，不授权切分支或重置工作区。

用户目标：推翻全软件旧 UI，以已选定的“02 晴空棱镜”重做所有现有页面、导航、表单、弹框、图标、提示、边框视觉、悬浮窗和状态；**保持现有功能逻辑不变**。首页图标要轻动态；星期后显示多样化经典句；收起的悬浮窗只有一个图标。

交付性质：本文件可直接作为开发任务输入，组件、文件、约束、技术决策与验收均在文中。技术可行性由源码和官方文档支持；尚未执行正式软件重构或对目标效果做原生性能实测。不要把“技术可实现”写成“已经在生产完成”。

## 阅读顺序与执行规则

1. 先读第1–4节：范围、架构、技术决策、代码权威。
2. 第5–10节定义最终 UI、坐标、组件与模块落点，尺寸是逻辑像素。
3. 第11–15节是实现接口、行为保持、任务依赖、测试与交付要求。
4. 附录列出本地类、控件信号和源码定位；完整静态索引为 implementation-source-baseline.json。它不含业务记录或配置值。

相互冲突时：用户“功能不变” > 当前业务源码 > 本文明确技术裁决 > 目标尺寸 > HTML演示。HTML不是可直接替换生产的网页，不允许把其模拟业务代码复制进正式软件。

## 1. 重构范围与不变契约

### 1.1 必须整体重做的展示面

全部22个导航叶子入口（首页、设置及20个功能入口），原生保底首页/侧栏，公共表单/菜单/确认框/错误框，数据库连接配置、需求编辑/附件/测试点、提签及配置、服务器/分类/命令/模型/技能/笔记编辑等子窗口；图标资产、状态栏、加载/空/错/禁用/选中态；52×52悬浮入口及其工具、聊天、学习模式。

这是重建信息层级、卡片/分栏/工具条与整套展示组件，不接受只替换一张QSS、只改首页，或只交一批截图。后台类、数据模型、线程与业务回调仍使用原实例。页面重排不意味着重建连接/模型/编辑文档。

### 1.2 不可修改的行为

- 数据库：连接定义、认证模式、SQL方言、守卫、事务、分页/查询范围、取消、结构扫描、结果模型与导出。
- 需求/发版：状态及测试点判断、实际上线日期与月份、筛选/排序/统计、目录/SVN、SQL提取/生成、提签模板、导出文件与保存字段。
- 接口/日志：代理/证书/浏览器/端口、捕获队列/刷新节奏、SSH会话、多机并发、终端执行、历史、停止与清理。
- AI：模型配置、token、技能权限、绑定目录、执行模式、流式生成、停止机制和历史持久化。
- 其他：证件/VIN算法、格式化算法、国密、日报自动保存/提醒、学习解锁、设置键/默认值、剪贴板/文件操作。
- 系统：启动顺序、延迟装载、single instance、WebEngine策略与回退、托盘、退出/隐藏/恢复、窗口关闭确认。

必须保留：已有所有按钮/菜单/双击/快捷键的可达性、启用规则、输入参数、回调调用次数、成功/失败语义、原语言切换。可把同一个动作重新排布或放入“更多”，但不能实现另一份操作逻辑。拖动splitter仍能恢复原有保存状态。

### 1.3 原型与生产的正式裁决

| 原型表现 | 本次生产实施裁决 |
|---|---|
| 全局多标签、标签关闭/固定 | 当前MainWindow是QStackedWidget槽位，不新建全软件标签生命周期。移除这段原型UI；数据库自身SQL标签保留并重做样式 |
| 全局搜索、通知和文件/视图/帮助菜单 | 不新增假通知中心/新搜索范围/新菜单行为。已有快速面板入口继续openPalette；现有菜单换皮。没有的入口不放入生产 |
| 主窗自定义最小化/最大化按钮 | 当前QMainWindow使用系统边框。本期保留系统非客户区按钮及行为，重做客户区和品牌。第3节说明可选无边框技术，不在本期实施 |
| 任务勾选会直接写完成 | HomeBridge无直接完成写入槽。首页点击任务继续openRequirement(id)进入现有编辑，已有原生完成操作原样保留；不能新增完成门槛或API |
| 新增的3个设计展板 | 仅开发审阅资源，不加入正式导航和打包业务入口 |
| 52×52单图标 | 与现有QuickPanel一致，直接沿用几何/锚点，替换绘制。展开全部已有功能保留 |
| 固定9/12、模拟聊天、模拟导出成功 | 禁止复制到生产；只使用真实现有状态，原有is_demo回退标记保持 |

上述裁决是保持功能边界后的完整生产设计，不是让开发者在实现中临时自行删改功能。真正新增业务功能需独立需求，本次不做。

## 2. 当前技术栈：声明与实测分开

| 层 | 当前证据 | 目标用途 |
|---|---|---|
| Python | requirements注释基线3.12；当前解释器实测3.12.3 | 原业务、原生控件和线程 |
| PyQt6 | requirements锁定6.11.0，当前安装同版本 | QWidget/QLayout/QPainter/QtSvg/信号槽 |
| Qt runtime | PyQt6-Qt6 6.11.2，当前安装一致 | 既有绘制、窗口、DPI |
| WebEngine | Python包6.11.0、Qt runtime6.11.2，安装一致 | 现有本地侧栏和首页两个视图 |
| Vue / TS / Vite | package.json：3.5.42 / 6.0.2 / 8.2.2 | 拆分首页与侧栏展示组件、静态资源构建 |
| QWebChannel | ui/web_shell.py + frontend/src/shared/bridge.ts | 复用既有DTO/槽/信号 |
| 打包 | requirements-build.txt：PyInstaller6.22.2 | 保留既有build脚本；重构阶段不升级 |
| 样式/组件 | ThemeManager、design_system、PageChrome、responsive、field_metrics、motion、icons、splitter_prefs | 直接演进现有基础设施 |

frontend/vite.config.ts是chrome.html和dashboard.html双入口、base='./'；不是单SPA。不增加Router，不以route URL替代Python导航。生产加载resources/webui/vue中的相对资源；源码在frontend，构建产物不可手改。

## 3. 技术可行性与新增技术决策

### 3.1 明确结论

**当前栈可完成全部本期目标；新增运行时依赖0个，新增服务0个，技术栈迁移0项。** 需要新增少量项目内展示组件、SVG资产、静态句库和验证代码；这属于代码组织，不是引入新框架。

| 效果 | 实现技术 | 结论/限制 | 最终选择 |
|---|---|---|---|
| 全新页面层级、分栏、卡片、工具条 | QVBox/QHBox/QGridLayout、QSplitter；Web CSS Grid/Flex | 可实现，尺寸用布局约束 | 全量重做展示容器，业务实例保留 |
| 渐变、圆角、细边框 | QSS/QPainter；CSS | 可实现；QDialog外围系统框不等于内部QFrame | 统一QFrame内容表面，保留模态语义 |
| 玻璃感 | Qt半透明色+渐变高光；Web backdrop-filter | Web只能模糊自身绘制上下文后方，不能模糊旁边Qt工作台/系统桌面 | Web局部blur24；Qt默认实底浅玻璃感，不做跨窗口采样 |
| 真正Windows桌面Acrylic/Mica | 平台API、无边框/原生合成集成 | 不属于QSS；涉及系统版本、句柄、DPI、生命周期 | 本期不用；不安装pywin32/qframelesswindow等依赖 |
| 阴影 | 小型QPainter边缘绘制或单个局部shadow；CSS box-shadow | 全窗/大表格graphics effect成本高 | 禁止在QWebEngineView或滚动表格父层挂模糊effect |
| 图标轻动效 | Vue/CSS transform；Qt QPropertyAnimation/QVariantAnimation+QPainter | QSS无CSS keyframes/transition通用替代 | Web首页循环；原生首页装饰单实例动画；普通控件<=200ms |
| 整体窗口圆角/自定义标题栏 | FramelessWindowHint + 原生窗口交互处理 | 可实现但会触及拖拽、缩放、Snap、系统菜单、跨屏与关闭语义 | 本期保留系统边框；客户区圆角和品牌可做 |
| SVG高清图标 | 现有ui/icons.py、QtSvg、Web SVG | 可实现，DPR正确缓存 | 使用项目资产，不加图标CDN |
| 长表格/SQL/终端 | 当前QTableWidget/View、模型、SqlEditor、SshTerminal | 可实现；不要为了视觉替换为浏览器DOM表格 | 原编辑器与结果模型直接复用 |
| 弹框/下拉跨Qt与Web边界 | QDialog/QMenu；Web局部浮层 | Web浮层不能越过自身视图矩形 | 全局/业务模态使用Qt，Web仅自身范围tooltip等 |
| 多主题/密度/高DPI | ThemeManager、QPalette、layout_metrics、现有偏好 | 可实现，字体DIP与DPR不同 | 保留calm/black ID、默认值和全部持久化键 |
| 每日一句 | 本地静态数据+日期选择纯函数 | 可实现，不改summary或联网 | 本地日历轮换，12条初始句库 |

QGraphicsBlurEffect模糊的是它自己的源内容，不能把它当作Windows背景模糊。QSS只使用Qt支持的属性，禁止复制backdrop-filter、CSS box-shadow、CSS变量或@keyframes到style.qss。

### 3.2 技术选型排除表

Electron/Tauri：不采用，会替换宿主和系统集成；QML/Qt Quick：不采用，增加第三套渲染体系；Element Plus/Ant Design/Tailwind：不采用，现有Web仅两页，自建少量展示组件更可控；Pinia/Router：不采用，现有props/bridge足够且Python拥有导航；GSAP/Lottie/Three.js：不采用，轻动效用CSS/Qt即可；Monaco/xterm：不采用，避免改编辑器/终端行为；在线名言API：不采用，离线、零隐私外发。不得因为这些库“更好看”破坏已锁定依赖。

若以后明确要求Windows桌面真实模糊及完全自绘边框：单独建平台窗口需求，验证WM_NCHITTEST、Snap、屏幕负坐标、DPI变化、Alt+F4、系统菜单和退出，先做隔离验证再选原生调用或小库。本文件不把这项作为完成本期UI的前置条件，也不授权修改主窗口flags。

### 3.3 官方依据（查阅日期2026-09-08）

- [Qt样式表支持范围](https://doc.qt.io/qt-6/stylesheet-reference.html)：用于属性、子控件、状态选择；不等于浏览器CSS。
- [QPropertyAnimation](https://doc.qt.io/qt-6/qpropertyanimation.html)：Qt属性动画依据；项目已有ui/motion.py。
- [QWidget](https://doc.qt.io/qt-6/qwidget.html)：原生窗口、透明背景及绘制约束。
- [QGraphicsBlurEffect](https://doc.qt.io/qt-6/qgraphicsblureffect.html)：源内容模糊，不是系统背景采样。
- [Qt高DPI](https://doc.qt.io/qt-6/highdpi.html)：设备无关坐标，不重复乘DPR。
- [Vue Transition](https://vuejs.org/guide/built-ins/transition.html)：Web进出场的现有框架能力。
- [MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)：Web局部背景滤镜；采用@supports回退。

上述资料证明API能力，不证明本机目标GPU/系统下的最终性能。运行与视觉验收在第14节执行。

## 4. 实现架构与文件职责

```text
run.py（原样） -> MainWindow（原槽位/生命周期）
   ├─ 侧栏host：原生fallback / Web chrome（两者一起重做外观）
   ├─ 内容host：新增无业务状态的ContextHeader + 原stack
   │    ├─ 首页host：原生DashboardPanel / Web dashboard
   │    └─ 现有原生panels实例（重排控件，不替换业务）
   └─ 原statusbar、QuickPanel、TrayService

ThemeManager.palette -> QPalette/QSS/Qt绘制
                     -> 既有themeChanged -> CSS tokens
navigation_model -> 原nav payload -> Chrome导航
summary provider -> 原dashboardSummary/summaryChanged -> 展示组件
```

不将Web视图铺满主窗口覆盖原生面板；不新增WebView数量；不在页面切换时reload WebEngine。保留pageReady、10s健康超时、renderProcessTerminated和fallback。

| 文件 | 允许改动 | 禁止改动 |
|---|---|---|
| main_window.py | 容器排布、边距、ContextHeader展示接入、原生侧栏绘制 | _ensure/_mount调用时机、stack索引、导航解析、关闭/回退/启动状态机 |
| ui/theme_manager.py | calm/black配色和现有语义映射；界面展示名可为晴空棱镜/墨黑 | ID/alias/default和保存流程 |
| ui/layout_metrics.py、field_metrics.py | 尺寸权威引用和展示默认值；统一避免重复常量 | 私改各模块断点或用户保存键 |
| ui/design_system.py、page_chrome.py、responsive.py | 通用展示组件/动作条/样式 | 点击业务、QAction启用策略 |
| ui/icons.py、resources/icons、resources/brand | SVG/ICO视觉及缓存使用 | 业务角色ID/图标加载公共函数契约 |
| frontend/src/chrome/*、dashboard/*、styles/base.css | 组件拆分、CSS、离线句库 | 导航/完成业务新实现 |
| ui/web_shell.py、shared/bridge.ts、dashboard/types.ts | 默认只读，保留接口 | 加新业务槽、放宽协议、绕过fallback |
| panels/*.py、ui/*编辑器 | _setup_ui、布局/paint/delegate外观 | 执行/保存/验证/线程方法 |
| tools/*、config.py、run.py | 本期只读 | 禁止借重构修改 |
| resources/webui/vue/* | 由build:embedded生成 | 手改bundle |

同一个方法混合建UI和connect时，可以改布局语句，connect保持对应同一receiver且次数一致。把控件移入新容器后原self字段仍指向原对象。不要全局改objectName破坏测试/选择器；新增样式优先dynamicProperty，例如surfaceRole/actionRole，既有objectName保留。

## 5. 生产坐标、尺寸和响应式（覆盖V1旧坐标）

坐标原点：QMainWindow客户区(0,0)，包含QStatusBar，不含Windows标题栏/外阴影。1440×900指Qt resize的客户区；CSS px与Qt DIP对应，不重复乘DPR。V1中44高仿标题栏及36高全局标签栏从生产坐标删除。

### 5.1 总体网格

- W/H=当前客户区；S=28状态栏；N=当前导航实际宽；P=当前模式边距。
- 新的只读上下文头高52，位于内容列顶端。只放当前模块图标/名称、已有快速面板入口；不放假通知/新标签/新业务菜单。ContextHeader不是新导航模型。
- 页面头x=N+P、y=52+P，高T=72（low-height时60）。工作区x相同、y=52+P+T+G；G=16（compact12、narrow/low10）。C=W-N-2P；V=H-S-P-y。
- MainWindow.content_layout负责页面外边距，原生Panel和Web根部不得重复加P。ContextHeader单独固定区，pageChrome在panel内；禁止两个同名页面标题重叠。
- 默认展开宽采用现有模式：wide248、standard220、compact84、narrow72；用户折叠偏好优先。不得为更像原型把用户默认强改84。

| 基准状态 | N/P/T/G | 内容x,y | C,V | 可观测外框 |
|---|---|---|---|---|
|1440×900，展开|248/24/72/16|272,164|1144,684|上下文头(248,0,1192,52)，状态栏(0,872,1440,28)|
|1440×900，图标栏|84/24/72/16|108,164|1308,684|上下文头(84,0,1356,52)|
|1280×800，展开|220/20/72/16|240,160|1020,592|标题(240,72,1020,72)|
|1100×720|84/16/72/12|100,152|984,524|页面横向不足时辅助区下移|
|960×640，low|72/16/60/10|88,138|856,458|必须保留可到达主动作和原有窄布局|

设置/表单内容max1040/1180居中；右边空白按(可用宽-max)/2均分。页内列表与编辑器独立滚动；主页/设置等自然流可页面滚动；不让两个嵌套滚动区域同时争夺同一段表单。

### 5.2 导航和状态栏

展开导航左右12，品牌行64；导航项高40、圆角10，图标20，文字13、line-height20，图文gap10；组标题11/16，组间16；子级缩进20。图标栏触发盒44×44水平居中；父项点击仍展开子项，内容可在轨道内滚动，不把父项改业务跳转。长名tooltip，键盘Enter/Space行为保持。底部入口和私有项完全取现有nav payload。

ContextHeader padding左右P；图标20；名称14/20；快速入口32高、图标18，右距P。状态栏28高、左右12，字体11/16，连接状态仍来自原source；版本/时钟/用户chip及其eventFilter用途保留（包含现有隐藏入口，不能因美化删除）。

### 5.3 密度冲突的统一决定

当前field_metrics固定28，而design_system的density_metrics是compact32/32、comfortable36/40；V1把它们混作一个值。V2明确三种用途：

| 角色 | 高度 | 适用与优先级 |
|---|---:|---|
| 已有紧凑业务字段/工具动作compactAction |28|保留已有显式fixedHeight和对应field_metrics；路径/服务器/SQL工作条用这个 |
| 普通通用控件 |32 compact / 36 comfortable|来自既有density_metrics，不另写32/36散落值 |
| 数据行 |32 compact / 40 comfortable|无原页面必须36等特殊约束时采用；原有专用行高保留并在页面类标注 |
| 表头/内容tab |26|layout_metrics既有常量 |
| 日期 |高28，日宽150–160，月128–150|field_metrics来源；不能截断日期箭头 |
| 普通文本/路径 |min160/min200，stretch1|动态选择宽200；封闭枚举按sizeHint夹在现有min/max |

尺寸优先级：可用空间与可访问内容 > 用户字体/密度偏好 > 当前专用控件约束 > 本表通用值。不能用QSS max-height覆盖Qt显式固定高造成裁切。统一变更仅调整展示，保留偏好键和normalization。

## 6. 视觉系统及全状态组件

### 6.1 颜色与字体

| 语义 | 晴空（calm） | 墨黑（black） | 用途 |
|---|---|---|---|
|APP_BG|#F4F3FA|#15151E|窗口客户区|
|SURFACE|#FDFDFF|#1E1E2B|表单/表格实底|
|SURFACE_SOFT|#F0ECFA|#262437|次级区|
|PRIMARY|#6C58D9|#A99AF5|主强调|
|PRIMARY_HOVER|#5D49C5|#BCB0FF|hover|
|PRIMARY_SOFT|#EEE9FF|#322B4D|轻选中|
|TEXT_STRONG|#262438|#ECEAF7|主要文字|
|TEXT_MUTED|#615D73|#AEA9C2|说明|
|BORDER|#E6E2F0|#393548|细边框|
|SUCCESS|#247F75|#74CABB|成功|
|WARNING|#A5772A|#E2B96D|提醒|
|DANGER|#C45371|#F18CA7|失败|
|ON_PRIMARY|#FFFFFF|#191527|按钮文字|

其他既有token从对应语义派生，不能删除TERM_*、CODE_BG、SEARCH_*、FOCUS_RING、STATUS_*、GLASS_*等角色。overlay透明度Qt用0–255、CSS用0–1，通过现有normalizeCssTokenValue统一，不能混用。浅色玻璃alpha236/255、深色238/255；焦点环2px、offset2。代码/终端遵循现有专用token，不把终端强制改白底。

默认字体13、行20；说明12/18；h1 26/34；h2 16/24；h3 14/20；统计30/36；代码13/20等宽。中文沿用系统/现有字体fallback，不嵌入字体。长文本可复制，关键状态不只靠颜色。验收要求正文4.5:1、大字与关键非文本3:1；开发时用最终实际组合测量，色板本身不是对比度合格证明。

### 6.2 组件施工规格

| 组件 | 几何与绘制 | 事件/状态 |
|---|---|---|
| SurfaceCard | radius16、padding20；密集区radius12/padding16；gap16 | QFrame动态属性surfaceRole；不持有业务数据 |
| PageChrome | 正常72/低窗60，标题与说明gap4，动作gap8 | 复用add_primary_action/add_secondary_action；已有按钮原对象 |
| 主/次/危险按钮 | min72宽，左右12，图标18，gap6，圆角8；高按5.3 | enabled/pressed/checked仍由原逻辑；hover100ms，按下可换深色，不改尺寸 |
| IconButton |28×28或32×32；图标16/18 | accessibleName+tooltip，不依赖title文本推断操作 |
| FormRow | label80宽，gap12；控件stretch；行间12；错误下6 | 密码echoMode、校验、默认值/Tab顺序原样 |
| Table | 单元格左右12；表头26；行按密度；选中软紫 | 保留模型/selectionMode/editTriggers/delegate语义/排序；只改绘制 |
| Tree/List | 树缩进16，图标18，项40；长文单行省略tooltip | 不重建model、不丢expanded/scroll/currentIndex |
| Code/Terminal | padding12，等宽13/20，背景不透明 | 原编辑器document、selection、undo、IME、快捷键、滚动原样 |
| StatusPill |高22，左右8，图标12，gap4，radius6 | 状态文字原语义；文本可保持常驻，不假装成功 |
| Menu |建议224宽、item34、padding6、分隔6 | Qt QMenu用于越界菜单；复用QAction同一实例，避免trigger两次 |
| Scrollbar |可视宽8，hover10，handle最短28 | 不隐藏滚动能力；原滚动步长保留 |
| Splitter |handle4，hover强调线2 | 禁止改存储key；宽不足沿用原orientation adapter |
| Tooltip |max320，padding8×10，radius8 | 提供长路径完整信息；错误不能只放tooltip |
| Error/Empty |图标40，标题14，说明12，gap12，max文字480 | 原始错误可展开/复制仅已有动作；不会自动重试 |

不对整个application每次hover执行setStyleSheet；主题变化一次应用，动态属性变化只polish目标控件。QPainter在paintEvent内begin/end成对；不在paint中读文件/数据库、建线程、改布局。

## 7. 首页、动态经典句与悬浮窗

### 7.1 首页布局

工作区C>=1200：右栏R330；1020<=C<1200：R300；C<1020：单栏，原右栏下移。gap16；左L=C-R-16。hero176高；stat区在y192，高108，4列宽(L-36)/4；任务区y316起，头32+筛选32+原有任务行最少64。右栏总览300高、常用工具y316，2列gap8、card64高。所有坐标相对工作区。标题/多行经典句撑高时统计区按流式下移，禁止覆盖。

基准1440展开：L828、R300；icon模式L962、R330。统计在不足700左列时改2×2，高228；任务随之下移。hero内padding24，图形120×120右24居中；文字宽至少L-192。主任务显示规则、排序、完整字段/打开动作沿用生产；不把12条demo当真实任务数量。

### 7.2 动态规范

主图形4.8s ease-in-out，y:0/-5/0，rotate:-5/5/-5度，scale1/1.04/1；装饰不参与布局。统计icon5s一个周期，仅80%–100%期间2px上浮归位，延迟0/.3/.6/.9秒；常用工具hover/focus上浮2px、旋转-6度、200ms。

CSS只animate transform/opacity。Qt对应自绘decorative属性，由一个QVariantAnimation驱动，调用update小区域；不要让每个小图标建立永久timer。保底首页可以只让hero图形动、统计静态，数据与动作必须相同；这是明确性能回退，不得回退到旧布局。

尊重prefers-reduced-motion、PENGTOOLS_DISABLE_MOTION及ui.motion.motion_enabled；前台不可见/页面非首页暂停循环，恢复不补播积压帧。无障碍静态保留完整信息。禁止把业务等待加载与首页装饰动画混作同一状态。

### 7.3 每日经典句

显示位置：日期与星期之后，“9 月 8 日，星期二 · …”。从静态daily-quotes.json读取12条（句子、作者/作品、稳定id），内容可取原型DAILY_QUOTES，不联网、无AI调用、不写业务配置。新增的展示数据文件同时供TS导入和原生页面只读加载；资源打包通过现有resources规则，避免跨业务层读取。

日序按本地year/month/day转UTC日数计算，只用于消除夏令时小时数偏差；不能直接用UTC当天日期。index=dayOrdinal % 12。换一句按钮28×28在句尾，只改当前页面临时offset。最多2行，作者作品通过tooltip+可访问描述。展示timer60s检查跨日；visible恢复检查；卸载清理。日期展示不更改summary.date_line字段及统计月份；原“数据同步”信息保持在状态提示中，不能吞掉错误/警告。

### 7.4 悬浮窗

收起52×52，按钮(4,4,44,44)，图标30，只有一个品牌图标，不加外露抓手/更多/文字。ui/quick_panel.py中已有COMPACT_SIZE/BUTTON_SIZE/BUTTON_MARGIN作为唯一尺寸。点击展开、拖动/跨屏/吸附/收起保留既有eventFilter与_compact_position；品牌中心不漂移。

展开工具宽300，学习360，聊天340×440；head48、foot40、CARD_HEIGHT58、gap8、padding12；工具两列每格134宽。复用已有_apply_toggle_icon、_expanded_size、_rebuild_cards、_place_initially等，不改模式和权限。默认菜单/6入口上限/候选列表来自navigation_model；设置opacity和置顶仍走原路径。

## 8. 模块工作台：最终区域模板

坐标相对第5节工作区(0,0,C,V)。以下结构是目标初始排布，已有用户splitter尺寸优先。C不足时使用模块已有apply_layout_mode，不创造第三套宽度状态；不因重排丢任何现有tab/工具按钮/上下文菜单。每页类和connect清单在附录。

| 入口 | 模板/宽高公式 | 原控件必须保留 |
|---|---|---|
|18 Oracle /19 MySQL /20 OceanBase /21 达梦|C>=1200：树220，主C-522，辅助270，gap16；C<1200：树200+主C-216，AI/对象使用现有窄面板切换；主编辑初始260，结果余V-276|连接/增删测试、结构扫描取消/快照、对象检索、SQL多标签、执行与取消、消息/历史/导出、模型与AI；方言分别不变|
|22 Redis|树240+右C-256；类型值区min280；CLI高200或余高；工具48|前缀树+Key列表、加载更多、各Key类型、fmt、TTL、编辑/重命名/删除、集群扫描/取消和连接模式|
|23 MongoDB|集合240+右C-256；查询头48+过滤96；结果余V-160；Shell在原页签|过滤/重置、limit/sort等原字段、文档/表格、插入/删除/复制/导出、连接和Shell|
|16 聊天|会话240+右C-256；右header48、composer至少120，消息余V-168|模型配置/技能管理、session列表、发送/停止/复制/原渲染与安全提示|
|17 工作|工作空间240+右C-256；上下文沿原展开面板；输入min120，步骤/结果占剩余|工作空间新建、目录绑定、执行模式、项目树、消息/停止与context切换|
|10 需求|左右400/C-416；左min320/max560、右min520；C<936转原窄布局|扫描/更新/粘贴BUG/批删/筛选/月份/列表/文件/SVN/联动；详情仍使用RequirementDialog，不新造侧栏编辑逻辑|
|2 发版联动|左上下文320+右C-336；右输入min240、预览余高；原tabs26|发版材料、SQL页签/生成/导出、日期/系统/环境/选择记忆、提签及设置|
|3 接口文档|文档列表280+右C-296；SQL220、字段变化预览余高|目录选择/刷新/原文档列表右键/双击/SVN拉取、模板、SQL、作者日期、输出目录与文件浏览|
|9 日报|日期树264+右C-280；三正文区各min120，label18/gap6/区间16|今日/日期、昨日计划导入、复制为今天、草稿自动保存、提醒设置、历史管理|
|13 日志|服务器/服务260+右C-276；过滤48、终端V-100、命令52|服务器/分类配置、命令历史、日志文件加载、SSH状态、多机批量导出、停止和原检索控制|
|6 命令库|列表280+右C-296；参数区自然高；预览min240|搜索/分类/自定义命令/风险提示、生成及复制（没有执行）|
|5 加解密|左右各(C-16)/2；参数原折叠区在上；报文区min320|全部国密字段、密钥可见切换、请求/响应、解密、接口排查/XML跳转/复制清空|
|12 接口排查|列表C*.52、右C*.48-16；toolbar48、详情tab26；低宽沿原切换|浏览器/监听/证书同意、session清理、loopback验证、过滤/队列、请求详情与请求测试|
|11 格式工具|tab26+工具48，编辑器双列(C-16)/2，余V-90；compact以下原垂直布局|JSON/XML/SQL/文本及各子操作、打开文件/粘贴/去重/校验/导出、编辑器原model|
|1 证件类型|max1180居中；条件两/三列gap16，结果表min260|个人/单位所有条件联动、区域/年龄/数量范围、生成/复制/导出/清空|
|4 VIN|max1180；条件按sizeHint排、结果表占余高|全部模式/车辆类型/数量/筛选选项、生成/复制/单元格复制/导出|
|8 学习|检索/类别/资料260+右C-276，搜索28、正文行22|解锁可见性、粘贴/导入/新建/编辑/删除、全文搜索补全、表格编辑/隐藏行等现有动作|
|7 设置|max1040；类别180+gap24+内容；行min72，分组gap24|全部现有设置与诊断/重置/模型探测/悬浮入口编辑，保存键和默认保持|
|0 首页|第7节|真实summary/任务打开/新建需求/原生回退均一致|

模块每个按钮的enabled来源不得迁移到CSS假禁用。多选/键盘/右键行为保留。针对原本就有的响应式隐藏面板，保持原入口与toggled关系；不能把重要区域永久折叠来“让截图好看”。

## 9. 弹框、表单、提示与图标

### 9.1 全部子窗口尺寸规则

Qt系统边框不属于内容画布；内容可用QFrame统一surface。保留exec/open模态、parent、return code、accepted/rejected、default button及Esc行为。

| 子窗口 | 目标客户区w/h | 内部区域 |
|---|---|---|
|ConfirmActionDialog/AppNoticeDialog/NextStepDialog|440×自适应，min180，max可用屏高-48|header56/content自然高/footer60，padding24，按钮gap8|
|CloseActionDialog/HttpsCertConsentDialog|现有最小宽高基础上max(560,内容sizeHint)，上限可用屏-48|原选择卡片/复选框/后果说明一项不删；不把取消变成继续|
|ConnectionDialog|720×min(680,可用高-48)|字段两列，左标签80、input stretch；Redis seed列表/认证/OceanBase分支原样；底部测试/确认固定可见|
|RequirementDialog|min(1000,可用宽-48)×min(760,可用高-48)|原tab/源文档/SQL/测试点完整；内容滚动，footer64|
|RequirementAttachmentDialog|860×min(700,可用高-48)|预览表格/搜索/导出Word、原接受逻辑|
|TestPointsDialog|640×min(680,可用高-48)|行min44，checkbox16+命中28，编辑/移除图标28；长文换行|
|TicketSubmitDialog/ConfigDialog|860/720宽，高min(760,可用高-48)|原字段顺序和必填保持，两列表单宽(内容宽-16)/2；预览min160；底部提交始终可达|
|ServerManage/ServerEditor/LogSettings/CategoryManage/CommandHistory|管理860、编辑720、短分类440宽，高自适应max可用高-48|集合列表、表单、密码/测试/批量动作原样|
|CustomCommandDialog/KnowledgeEdit/PasteKnowledge/技能管理/ObjectPick|短表单640，长管理860；高max可用高-48|保留导入、提示词token、选择/复制等已有功能|

上表是目标初始尺寸；系统字体放大时sizeHint优先且滚动内容；最小960×640窗口下弹框按**屏幕可用区域**限制，不按父窗当前大小误裁。所有未知子窗采用同一短表单/管理模板并保留全部字段；附录已列出现有QDialog类，不靠开发者回忆。

### 9.2 提示

成功轻提示只显示真实已完成；失败使用当前错误信息，不统一改成“网络异常”。操作性文字保持（重试、取消、停止等），润色时保留原因/对象/后果。复制失败不能提示成功。加载结束不代表业务成功。

Toast视觉宽320–360、高min44，图标18，padding12×16，radius12，右上24；最多3条视觉排队，不能因此丢失原始错误对话框。**仅已有toast通路使用**；不新建替代所有业务反馈的全局队列。Qt全局浮层由Qt管理，Web只在自身区域展示；不得用z-index试图覆盖Qt原生区域。

### 9.3 图标

24×24 viewBox，stroke1.6、round；导航20、工具18、密集16、品牌16/24/32/48/64。沿用ui.icons角色与加载API；资源保存resources/icons和resources/brand原命名。替换品牌ICO时包含16/24/32/48/64/128/256图层，在实际任务栏/托盘浅深背景验证；沿用既有打包资源路径，不换AppUserModelID或快捷方式身份。

SVG不含外链/script；单色currentColor；主按钮图标用ON_PRIMARY而非固定白色。禁用/选择/hover由原状态映射图标颜色。保留现有高对比任务栏标志回退，不因品牌一致性降低16px辨识度。

## 10. Loading 全场景重设计（本次新增明确要求）

### 10.1 视觉语言

统一“棱镜环 + 克制的光带”：品牌单菱形静置，外围两段细弧缓转；不使用大面积高速扫光、弹跳遮罩、伪百分比或整屏闪白。加载容器沿用所在表面浅紫/深紫，正文始终稳定。成功是静态对勾，失败是静态错误图标，取消是停止标记；禁止结束时放礼花。

| 场景编号 | 组件/位置/大小 | 动画 | 文案与反馈 |
|---|---|---|---|
|LD-01 启动|StartupSplash既有480×280；品牌64在(208,48)，标题居中y132高28，说明y172高20，轨道(48,216,384,4)|品牌外圈900ms线性旋转；无真实进度时轨道片段宽96水平往返1.6s|沿用启动阶段真实文字；不显示“100%”直到已有完成 |
|LD-02 模块准备/长任务|AuroraProgress既有host内浮层，保持固定高62；目标宽320，max host宽-32；右上16；窄host居中|24×24环，描边2，前景90度弧；900ms循环；文字13/20|主行“正在…”取原label；无任务阻断鼠标 |
|LD-03 按钮busy|保持按钮原宽高，原icon盒内16×16环；图文gap6|900ms循环，文字位置不变|原业务控制disabled/停止态；视觉不得擅自disable整页 |
|LD-04 表格查询/刷新|原结果区顶部高32状态带，图标16，左12；首次无结果时可3条骨架，每条高16，行间16，宽70/90/55%|骨架alpha .35→.65→.35，1.6s；只首次空白出现|有旧结果保留，标记“正在查询”；不得清空model、重置scroll或新排序 |
|LD-05 AI首字等待|ThinkingIndicator原消息区内，建议高28、min宽140；三菱点各6×6，间距5，文字起x48|三点opacity .35–1，1.2s周期，相位0/.15/.3s，不上下跳字|“正在等待回复…”；首个内容是否替换由原调用点决定，不冒充模型推理步骤 |
|LD-06 Agent/扫描/导出|原任务区，环24；有原进度事件才显示4px轨道和百分比，label+百分比行20|真实数值到新值最多150ms显示插值，不预测下一步|原步骤/取消/重试/文件结果保留；没有total则不定进度 |
|LD-07 悬浮AI|单图标收起态仅品牌边环32×32；展开在原聊天区内显示LD-05|同900ms，单实例；原组件不可见则停装饰timer|不自动展开、不抢焦点、不改变任务进行状态 |
|LD-08 成功/失败/取消|同容器静态18或24图标；尺寸不跳|成功出现淡入150ms，失败无循环，无shake|只使用原回调结果；无取消回调的操作不添加取消按钮 |

LD-04/按钮busy仅在已有任务状态可观测时接入，使用已有busy信号/字段；禁止为了骨架屏新增网络请求或task状态机。首次空白骨架宽度不代表真实列宽/数据数量。

### 10.2 状态机沿用，样式重做

当前ui/aurora_progress.py公开接口：start_busy(label, immediate=False)->token、set_progress(value,label,token)、finish(label,token)、fail(label,token)、hide_now()/reset()、place_overlay(host)。所有调用点及token语义保留。

- 默认start_busy延迟300ms；<300ms完成不显示。immediate=True仍按原含义立即显示。
- 已显示后的最少可见500ms；finish驻留按原公式remaining_min + success_linger（默认350ms），不是取两者max。
- fail立即显示，默认2200ms驻留；不得被旧的成功timer隐藏。旧token回调忽略的_generation机制保留。
- set_progress现有行为会立即显示，即使尚未满300ms。不得为了统一动画改成延迟。
- hide_now用于原切页/中止清理，立即停止视觉timer；不等同取消业务任务。
- StartupSplash保留300ms显示延迟、550ms最少驻留及原finish时机；不要把任务浮层的500ms强套到启动画面。
- ThinkingIndicator保留start/stop/set_text/is_running及隐藏销毁清理；不能让样式timer改业务is_running。
- 已有show_loading=False的任务维持不展示，不因为“统一设计”对全部短操作弹loading。

可重写paintEvent和装饰_phase到角度的映射；不可重写generation、delay/linger定时语义或task回调。Qt现有28ms绘制tick可以保持，通过单调时间映射视觉900ms周期，不提高线程/定时器数量。失败/成功不需要动态绘制时可停止**纯绘制定时器**，不得取消状态隐藏timer。

### 10.3 多任务、无障碍和性能

一项任务只选一个主要加载反馈，避免按钮/顶部浮层/全屏罩同时转圈。已有多任务只按各自token与host展示，不能共用单一全局busy覆盖彼此。切换页面停止装饰，但后台任务继续按原机制运行，回到页面读当前状态不重新执行。

不新增拦截鼠标的全窗遮罩；AuroraProgress的WA_TransparentForMouseEvents保持。业务原本需要禁用的控件继续禁用，动画不决定。停止按钮可达、键盘焦点不跳到spinner；spinner aria-hidden，状态文字Web role=status/aria-live=polite，百分比只报告真实值。减少动态效果时用静态环和文字，进度更新仍可见，不增加强制最少播放动画时间。

加载不改用户等待时间：500/550ms最少驻留只影响显示，不延迟业务结果应用、按钮原恢复和回调执行。不做假计时99%等待。日志/AI流式文字滚动与渲染节奏完全保持。

### 10.4 Loading 专项用例

LD-T01：100ms任务看不到busy；LD-T02：400ms任务展示后按现有500+linger时间收起，结果已正常应用；LD-T03：A开始、B开始、A迟到finish，B不被隐藏；LD-T04：fail打断pending并显示原错误；LD-T05：set_progress在100ms按原接口立即显示；LD-T06：用户切页/关窗后视觉timer停止，业务取消/继续按原逻辑；LD-T07：有旧结果查询中不被清空；LD-T08：首次AI内容到达的等待状态与原生命周期一致；LD-T09：reduced-motion静态但任务仍可完成；LD-T10：light/black都清楚可读；LD-T11：主窗口、弹框、浮窗分别测试，不抢焦点；LD-T12：启动完成/失败fallback不被动画延迟。

使用现有tests.test_loading_feedback、tests.test_startup_splash、tests.test_ui_motion作为回归入口；对新增纯绘制仅做局部截图与生命周期烟测，不用源码字符串断言当作动画验收。

## 11. 组件拆分和接口：开发可直接照此组织

新增名称为**拟新增展示代码**，不是声明仓库已有这些类。若现有组件已能提供同样能力，扩展原组件，禁止再造同名服务。

| 目标文件/组件 | 数据入口与事件 | 明确职责 |
|---|---|---|
|frontend/src/shared/components/PrismSurface.vue|props tone/padding；default slot|纯容器、零业务逻辑|
|frontend/src/shared/components/PrismIcon.vue|name/size/decorative/label；复用现有sprite|统一图标尺寸与可访问语义|
|frontend/src/dashboard/components/HeroSection.vue|username/greeting/dateText/quote；changeQuote展示事件|hero布局和装饰|
|frontend/src/dashboard/components/StatsGrid.vue|DashboardStats原对象，只读|原数字、null与demo状态显示，不计算新的业务比例|
|frontend/src/dashboard/components/MonthlyTasks.vue|MonthlyReleaseTask[]原对象；open(id)|只展示/打开，不emit完成写入|
|frontend/src/dashboard/components/ReleaseOverview.vue|DashboardRelease/已有统计|原数据状态与视觉环，不新增截止日期规则|
|frontend/src/dashboard/components/QuickTools.vue|DashboardToolItem[]；navigate(i)|导航参数原样传递|
|frontend/src/dashboard/composables/useDailyQuote.ts|本地Date输入；quote/nextQuote；可注入now供测试|本地轮换/可见性timer，onUnmounted清理|
|frontend/src/shared/components/PrismLoading.vue|busy/progress?/label/variant/reducedMotion|展示原状态；不得内建业务任务调度|
|resources/ui/daily-quotes.json|id/text/source 12条；UTF-8|唯一离线句库；TS在构建时导入资源，原生只读静态文件|
|ui/page_chrome.py:ContextHeader（拟新增）|set_context(title,iconRole)；既有quick_action入口|Qt只读全局上下文头，不新增导航/状态模型|
|ui/page_chrome.py:PageChrome（已有）|原add_primary_action/add_secondary_action|保留API，调整内容密度与标题几何|
|ui/design_system.py（已有）|apply_button/tree/table/module_tabs/surface|统一属性与绘制，不重连业务signal|
|ui/aurora_progress.py / thinking_indicator.py / startup_splash.py（已有）|第10节既有API|仅绘制重做，公开签名/状态契约不变|

父级DashboardApp保留props与事件处理，子组件只emit原操作；不要让子组件自行connectBridge。main.ts及connectBridge仍只连接一次，不因重排重复注册signal。多个Web入口的通用CSS放styles/base.css；原生同名token来自ThemeManager，不从CSS反解析。

Qt布局重排步骤：保存原对象引用 -> 从旧layout移除item但不delete原widget -> 加入新布局 -> 恢复sizePolicy/focus链/原splitter状态 -> 验证回调。禁止deleteLater正在执行的panel。原表格model/delegate若承载业务格式化，保持同实例；要换视觉delegate则调用原显示数据与role，不改变edit/checkbox等事件。

JSON句库在Vite中使用静态import，不能生产fetch本地file造成额外权限问题；Python用项目已有资源根路径兼容sys._MEIPASS，不引入新数据目录或写入settings。设计原型仍可保留model.js句库，生产以JSON唯一源为准。

## 12. 不变功能的验证协议

### 12.1 源码基线索引

implementation-source-baseline.json覆盖main_window、panels和ui共60个Python文件，记录文件SHA256、类/方法行号、方法AST摘要、控件构造与connect语句定位。它包含2317条静态构造记录和874条signal连接记录，**不是2317个运行控件，也不是完整运行时功能证明**；动态构造/表达式callback必须回到指示源码行查看。

方法AST不含行号，所以移动函数不影响摘要；若函数同时建UI/接信号，摘要变化是预期，须人工区分布局与业务。非UI工作方法摘要变化要逐项解释，不能以“代码清理”为由改变逻辑。索引不是强制禁止全部函数变化的机械门禁。

### 12.2 每个模块必须交付的前后对照表

`控件标识 | 原signal/receiver | 新位置 | 新signal/receiver | enabled来源 | 输入参数 | 原结果/副作用 | 测试证据`。字段默认值、菜单项、快捷键、可见性权限另列清单。控件索引给出起点，不要求用户再次列功能。

使用QSignalSpy或可控fake服务捕获调用：一次点击恰好调用一次同receiver/业务入口，参数一致；假数据只放测试fixture，不加载用户真实数据。预览失败/取消/空结果时对比错误语义和状态恢复。SQL/SSH/导出测试替换外部服务，不连接真实生产系统。

特别保护：Requirement测试点字段序列化、actual_release_date/online_month；连接payload和Redis集群seed；Mongo过滤/limit/sort；日报draft/auto-save计时；AI execution mode/停止；接口捕获队列刷新/证书同意；SvnWorker和后台线程所有权；私有入口解锁。

### 12.3 接口保持清单

Bridge槽：navigate(int)、openPalette()、createRequirement()、openRequirement(str)、navModel()、homeUsername()、dashboardSummary()、themePayload()、pageReady(page)。信号：navigateRequested、paletteRequested、activeChanged、themeChanged、summaryChanged、createRequirementRequested、openRequirementRequested。方向和参数类型不变；没有completeTask/saveTask之类新增接口。

ThemeManager保留palette/token/render/apply/add_listener/remove_listener；LayoutModeController广播仍来自主窗；nav index到stack映射原样。黑色主题、英文、旧设置文件、Web不可用fallback均视为正式功能，不得遗漏。

## 13. 开发工单与依赖（按序执行，无需另猜实施路线）

| 工单 | 文件/范围 | 输入 -> 产物 | 前置 | 完成条件 |
|---|---|---|---|---|
|UI-P00|只读基线与截图|本文件+源码 -> 模块行为对照表/截图|无|所有模块/子窗有负责人清单；不改业务 |
|UI-P01|theme_manager/layout_metrics/field_metrics/design_system|token与密度表 -> 双主题统一展示基础|P00|Qt/Web同语义，28与32/36用途一致，原设置回归 |
|UI-P02|icons、brand资源|统一SVG/ICO -> 所有尺寸资产|P01|角色覆盖、DPR/托盘16px可读，无外链 |
|UI-P03|page_chrome/responsive/main_window布局|第5节 -> 新客户区骨架+ContextHeader|P01|槽位、原生边框、生命周期和fallback保持 |
|UI-P04|chrome Vue及原生侧栏|原nav payload -> 新导航视觉|P02/P03|父级/底部/私有项/折叠行为全保持 |
|UI-P05|dashboard Vue+原生DashboardPanel+句库|原summary -> 新首页及装饰|P01/P02/P03|数据/打开规则不变，日期轮换不联网，两种renderer一致 |
|UI-P06|AuroraProgress/ThinkingIndicator/StartupSplash|第10节 -> 全加载视觉|P01/P02|全部LD-T回归；原timer/token契约不变 |
|UI-P07|confirm/connection/ticket/其他子窗|第9节 -> 统一表单与反馈|P01/P02|字段/模态/取消/保存返回值不变 |
|UI-P08|quick_panel/floating_shortcuts_editor|原52/44/4 -> 单图标及展开模式|P01/P02/P06|锚点/DPI/多屏/原线程和权限不变 |
|UI-P09A|requirement/test_points/sql/docx/personal日报|第8节 -> 交付管理全页|P03/P07|交付链完整，所有原控件与菜单覆盖 |
|UI-P09B|ai_workbench/Redis/Mongo|第8节 -> 6数据库|P03/P07|编辑器/结果/连接/停止/原窄模式全部通过 |
|UI-P09C|interface_debug/ops_log/ops|第8节 -> 运维与接口|P03/P07|捕获/SSH/命令生成行为等价 |
|UI-P09D|model_chat/agent/learning|第8节 -> AI与学习|P03/P06/P07|首字/流式/停止/上下文/私有解锁等价 |
|UI-P09E|credit/vin/gateway/format/settings|第8节 -> 工具与设置|P03/P07|选项/算法/联动/偏好键保持 |
|UI-P10|集成/构建/验收|全部产物 -> 验收包|P04–P09全部|22入口及全部子窗无旧视觉孤岛，Web构建与原生回归完成 |

P09A–E可由不同开发者在P01–P08稳定后分工；本文是未来工单安排，不要求当前Agent启动子代理。每个工单单独review，禁止一个大提交把业务变更混入视觉。中途遇到已有问题只记录EXISTING_FAILURE，不扩大范围修业务。

不安排数据库/依赖升级、全量代码格式化、启动重构或数据迁移工单。未完成的模块不能靠隐藏入口假装完成。最终必须覆盖附录所有页面类及子窗，不仅主页面截图。

## 14. 验证命令、样本和性能门槛

### 14.1 必须执行的最小检查

- 每次改动：git diff --check；检查暂存/未暂存范围，不含用户数据/配置/凭证。
- Vue：npm --prefix frontend run typecheck；生产资源改动：npm --prefix frontend run build:embedded；npm --prefix frontend run verify:embedded。build:embedded只清理生成目录，禁止在其他目录误用。
- Native公共：python -m unittest tests.test_ui_consistency_buttons tests.test_visual_native_surfaces tests.test_theme_responsive -v。
- Web与回退：python -m unittest tests.test_web_shell tests.test_web_chrome_runtime tests.test_web_dashboard_runtime tests.test_web_dashboard_theme -v。
- 首页业务保护：python -m unittest tests.test_dashboard_summary tests.test_dashboard_release_monthly -v。
- Loading：python -m unittest tests.test_loading_feedback tests.test_startup_splash tests.test_ui_motion -v。
- 悬浮：python -m unittest tests.test_quick_panel_lifecycle -v；启动/DPI只在实际接触对应展示时补相关tests.test_startup_dpi/boot。
- 需求/分栏：tests.test_requirement_splitter、test_requirement_test_points、test_requirement_release_contract、test_requirement_tree_state、test_splitter_prefs；发版test_release_ui。
- 数据库：test_connection_dialog、test_sql_workbench_query、test_redis_panel_layout、test_redis_overview、test_db_connect_redis_mongo、test_nosql_command_guard。
- AI：test_model_chat_harness、test_model_chat_bubbles、test_agent_workbench_layout；接口test_interface_debug、test_interface_fiddler_workbench；格式test_theme_format_tools、test_xml_formatter。

测试文件在本次源码中存在，但以上为未来实施命令，本文编写阶段未声称全部已执行。每工单选择对应最小集合，不默认unittest discover；不要对真实外部服务跑破坏性操作。打包不是每个工单的前置，最终是否产EXE按用户另行发版范围执行。

### 14.2 必备视觉和交互样本

视口：1440×900（展开/收起各一次）、1280×800、1100×720、960×640；DPI100/125/150/200%，两块屏幕及负坐标；主题calm/black；中文/英文与长内容；原字体放大。主操作必须可见或通过已有更多入口可达，表内横滚允许，整个主窗横向溢出不允许。

状态：0项、1项、长列表、超长标题/路径、loading、业务空结果、失败、已禁用、正在执行且切页、取消后恢复、已保存的极端splitter宽度、WebEngine不可用、私有项锁定/解锁。

保留状态样本：SQL输入/选择/撤销栈、树展开/选中、表格列宽/排序、日志滚动位置、聊天草稿/会话、日报未保存内容、连接进行中。调整窗口或切主题不得改变这些数据；动画只影响绘制。

### 14.3 性能与稳定性验收（目标，不冒充已测）

同一机器/相同数据/发布构建前后各3次记录：首个可交互窗口时间中位数不劣于基线10%以上；10分钟空闲动画CPU平均增量<=2个百分点；同一200次菜单/切页过程内存收尾后无单调泄漏；100次浮窗展开收起/关闭后无多余timer；常用输入连续键入不因动画出现可见丢字/卡顿。Web装饰在目标机尽量稳定60fps，若不达标先减少装饰和绘制范围，不影响任务刷新策略。

这些阈值要连同机器/系统/GPU/数据量记录，不能用一张静态截图证明。性能不达标允许关闭Qt保底装饰动画和降低视觉层阴影，不允许减少业务功能或改变查询数据量。标准截图区域边界误差<=2 DIP；字体栅格化不要求跨DPR逐像素一致。

### 14.4 完成审计表

最终报告逐行列：22导航叶子、所有QDialog类、两种首页/侧栏renderer、加载8场景、浮窗3模式、主题2种、窗口4档、行为对照与测试。每行附截图路径/用例命令/结果/未覆盖点。静态索引覆盖!=运行验证；green typecheck!=视觉通过；业务fixture通过!=实际服务集成通过。

## 15. 开发交付与停止边界

交付源代码、正确生成的Web资源、资产、逐工单行为对照、前后图、测试命令/结果和剩余限制。原型和DEMO仅留docs，不混入正式资源。现有业务代码/配置键/API未变须有diff与测试支持，不能只写口头保证。

开发完成定义：所有本期展示面替换完成、行为保持、低宽/双主题/原生fallback与loading全部验收；系统原生非客户区保留是已明确设计选择。若用户未来要求完全自绘系统边框，按第3.2单独实施，不隐瞒为“已像素还原HTML”。

明确当前范围：本次交付需求和实施设计，不开始正式生产代码重写、提交或发版。所有源码事实以基线为准，开发前只需核对分支漂移与新的用户需求，不需要重新做一轮技术选型。文档有未覆盖且会改功能的事项时记录差异，保持原实现，不能自行补新业务。

## 附录 A. 逐页源码施工入口与真实回调样例

下表来自当前源码静态AST。代码行是定位快照；修改后按类名/方法名重新定位。每个类保留所有现有方法，列出的8个回调只是常用入口，完整记录在JSON中。`expression`回调请在源码读取，不能当成空连接。

### panels/agent_workbench_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_WorkbenchBridge|43|见__init__及类内构建函数；worker只读|
|_WorkbenchWorker|91|见__init__及类内构建函数；worker只读|
|AgentWorkbenchPanel|134|_setup_ui:154；apply_layout_mode:523|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|_WorkbenchBridge / self.progressEmitted|self._on_progress|55|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.dir_btn.clicked|self._bind_directory|171|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.model_combo.currentIndexChanged|self._on_model_changed|177|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.exec_mode_combo.currentIndexChanged|self._on_exec_mode_changed|183|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.context_toggle_btn.clicked|self._toggle_context_panel|192|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.new_btn.clicked|self._new_workspace|197|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.new_ws_btn.clicked|self._new_workspace|223|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.space_tree.customContextMenuRequested|self._on_space_tree_menu|232|原接收方法和触发次数保持|
|AgentWorkbenchPanel / self.space_tree.itemClicked|self._on_space_clicked|233|原接收方法和触发次数保持|

### panels/ai_token_edit.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|AiPromptEdit|39|见__init__及类内构建函数；worker只读|
|ObjectPickDialog|244|见__init__及类内构建函数；worker只读|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|AiPromptEdit / self.customContextMenuRequested|self._show_menu|51|原接收方法和触发次数保持|
|AiPromptEdit / self.textChanged|self._sync_tokens_from_document|52|原接收方法和触发次数保持|
|AiPromptEdit / view.triggered|self._view_tokens|137|原接收方法和触发次数保持|
|AiPromptEdit / clear.triggered|self._clear_tokens_keep_text|138|原接收方法和触发次数保持|
|AiPromptEdit / paste.triggered|self.paste|151|原接收方法和触发次数保持|
|AiPromptEdit / select.triggered|self.selectAll|154|原接收方法和触发次数保持|
|AiPromptEdit / copy.triggered|self.copy|148|原接收方法和触发次数保持|
|ObjectPickDialog / self.search.textChanged|self._fill_objects|260|原接收方法和触发次数保持|
|ObjectPickDialog / self.obj_list.currentItemChanged|self._on_object|263|原接收方法和触发次数保持|
|ObjectPickDialog / self.field_list.itemSelectionChanged|self._refresh_ok|269|原接收方法和触发次数保持|
|ObjectPickDialog / cancel.clicked|self.reject|287|原接收方法和触发次数保持|
|ObjectPickDialog / self.ok.clicked|self.accept|290|原接收方法和触发次数保持|
|ObjectPickDialog / self.field_search.textChanged|self._fill_fields|274|原接收方法和触发次数保持|

### panels/ai_workbench_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_SchemaSearchPopup|63|见__init__及类内构建函数；worker只读|
|_DbWorker|137|见__init__及类内构建函数；worker只读|
|_AiWorker|198|见__init__及类内构建函数；worker只读|
|_SqlTab|220|见__init__及类内构建函数；worker只读|
|AiWorkbenchPanel|237|_setup_ui:278；apply_layout_mode:768|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|_SqlTab / self.editor.textChanged|self._mark_dirty|230|原接收方法和触发次数保持|
|AiWorkbenchPanel / self._search_timer.timeout|self._on_search_timer|252|原接收方法和触发次数保持|
|AiWorkbenchPanel / self._agent_timer.timeout|self._tick_agent_stage|268|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.new_tab_btn.clicked|self._new_sql_tab|289|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.conn_combo.currentIndexChanged|self._on_connection_changed|310|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.conn_del_btn.clicked|self._delete_connection|321|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.scan_cancel_btn.clicked|self._cancel_scan|330|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.save_draft_btn.clicked|self._save_draft|334|原接收方法和触发次数保持|
|AiWorkbenchPanel / self.view_snap_btn.clicked|self._view_snapshot|337|原接收方法和触发次数保持|

### panels/credit_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|CreditCodePanel|25|_setup_ui:61；apply_layout_mode:621|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|CreditCodePanel / self.category_tabs.currentChanged|self._on_category_tab_changed|81|原接收方法和触发次数保持|
|CreditCodePanel / self.table.itemDoubleClicked|self._copy_cell|112|原接收方法和触发次数保持|
|CreditCodePanel / self.copy_btn.clicked|self._copy_all|123|原接收方法和触发次数保持|
|CreditCodePanel / self.export_btn.clicked|self._export_csv|127|原接收方法和触发次数保持|
|CreditCodePanel / self.clear_btn.clicked|self._clear|131|原接收方法和触发次数保持|
|CreditCodePanel / self.personal_type.currentIndexChanged|self._on_personal_type_changed|164|原接收方法和触发次数保持|
|CreditCodePanel / self.personal_mode.currentIndexChanged|self._on_personal_mode_changed|172|原接收方法和触发次数保持|
|CreditCodePanel / self.personal_generate.clicked|self._generate_personal|181|原接收方法和触发次数保持|

### panels/dashboard_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|SectionHeader|63|见__init__及类内构建函数；worker只读|
|TaskRow|110|见__init__及类内构建函数；worker只读|
|DashboardPanel|233|apply_layout_mode:431|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|DashboardPanel / self.recent_more.clicked|self.open_requirements.emit|301|原接收方法和触发次数保持|
|DashboardPanel / self.release_more.clicked|self.open_sql.emit|336|原接收方法和触发次数保持|
|DashboardPanel / self.release_month_combo.currentIndexChanged|self._on_release_month_changed|342|原接收方法和触发次数保持|
|DashboardPanel / self.release_target_clear.clicked|self._clear_release_target|355|原接收方法和触发次数保持|
|DashboardPanel / row.clicked|self._on_requirement_clicked|619|原接收方法和触发次数保持|
|DashboardPanel / header.toggled|self._toggle_completed_section|728|原接收方法和触发次数保持|
|DashboardPanel / row.clicked|self._on_requirement_clicked|781|原接收方法和触发次数保持|

### panels/db_mongodb_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_MongoWorker|35|见__init__及类内构建函数；worker只读|
|MongoDBWorkbenchPanel|106|_setup_ui:124；apply_layout_mode:359|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|MongoDBWorkbenchPanel / self.conn_combo.currentIndexChanged|self._on_connection_changed|139|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.conn_del_btn.clicked|self._delete_connection|148|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.test_btn.clicked|self._test_connection|151|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.refresh_btn.clicked|self._refresh_collections|154|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.coll_filter.textChanged|self._on_filter_changed|173|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.coll_tree.itemClicked|self._on_coll_clicked|178|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.query_btn.clicked|self._run_query|207|原接收方法和触发次数保持|
|MongoDBWorkbenchPanel / self.reset_btn.clicked|self._reset_query|210|原接收方法和触发次数保持|

### panels/db_redis_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_RedisWorker|59|见__init__及类内构建函数；worker只读|
|RedisWorkbenchPanel|142|_setup_ui:167；apply_layout_mode:562|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|RedisWorkbenchPanel / self._search_timer.timeout|self._start_search_scan|160|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.conn_combo.currentIndexChanged|self._on_connection_changed|177|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.conn_del_btn.clicked|self._delete_connection|186|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.test_btn.clicked|self._test_connection|189|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.refresh_btn.clicked|self._refresh_keys|192|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.key_filter.textChanged|self._on_filter_changed|225|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.key_tree.itemClicked|self._on_prefix_clicked|244|原接收方法和触发次数保持|
|RedisWorkbenchPanel / self.clear_prefix_btn.clicked|self._clear_selected_prefix|266|原接收方法和触发次数保持|

### panels/docx_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|DocxUpdateWorker|34|见__init__及类内构建函数；worker只读|
|DocxUpdatePanel|62|_setup_ui:75；apply_layout_mode:384|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|DocxUpdatePanel / self.update_btn.clicked|self._update_document|83|原接收方法和触发次数保持|
|DocxUpdatePanel / self.folder_browse.clicked|self._choose_folder|101|原接收方法和触发次数保持|
|DocxUpdatePanel / self.folder_refresh.clicked|self._refresh_folder_docs|104|原接收方法和触发次数保持|
|DocxUpdatePanel / self.doc_list.itemSelectionChanged|self._on_doc_selected|119|原接收方法和触发次数保持|
|DocxUpdatePanel / self.doc_list.itemDoubleClicked|self._open_list_item|120|原接收方法和触发次数保持|
|DocxUpdatePanel / self.doc_list.customContextMenuRequested|self._show_doc_list_menu|122|原接收方法和触发次数保持|
|DocxUpdatePanel / self.docx_browse.clicked|self._choose_docx|131|原接收方法和触发次数保持|
|DocxUpdatePanel / self.docx_path.textChanged|self._on_docx_path_changed|132|原接收方法和触发次数保持|

### panels/format_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_SqlFormatTab|52|_setup_ui:61|
|_TextDevHelpersTab|261|_setup_ui:272|
|FormatToolsPanel|436|apply_layout_mode:446；_setup_ui:475|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|_SqlFormatTab / self.paste_btn.clicked|self._paste|70|原接收方法和触发次数保持|
|_SqlFormatTab / self.open_btn.clicked|self._open_file|74|原接收方法和触发次数保持|
|_SqlFormatTab / self.format_btn.clicked|self._format|78|原接收方法和触发次数保持|
|_SqlFormatTab / self.compact_btn.clicked|self._compact|82|原接收方法和触发次数保持|
|_SqlFormatTab / self.dedupe_btn.clicked|self._dedupe|86|原接收方法和触发次数保持|
|_SqlFormatTab / self.validate_btn.clicked|self._validate|90|原接收方法和触发次数保持|
|_SqlFormatTab / self.copy_btn.clicked|self._copy|95|原接收方法和触发次数保持|
|_SqlFormatTab / self.export_btn.clicked|self._export|99|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.mode_combo.currentIndexChanged|self._on_mode|283|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.encode_btn.clicked|self._encode|289|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.decode_btn.clicked|self._decode|293|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.convert_btn.clicked|self._convert|297|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.copy_btn.clicked|self._copy_out|302|原接收方法和触发次数保持|
|_TextDevHelpersTab / self.clear_btn.clicked|self._clear|306|原接收方法和触发次数保持|

### panels/gateway_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|GatewayDecodePanel|39|_setup_ui:53；apply_layout_mode:297|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|GatewayDecodePanel / self.to_iface_btn.clicked|self.open_interface_debug.emit|77|原接收方法和触发次数保持|
|GatewayDecodePanel / self.clear_btn.clicked|self._clear|81|原接收方法和触发次数保持|
|GatewayDecodePanel / self.copy_btn.clicked|self._copy|85|原接收方法和触发次数保持|
|GatewayDecodePanel / self.key_reveal_cb.toggled|self._toggle_key_visibility|159|原接收方法和触发次数保持|
|GatewayDecodePanel / self.to_format_xml_btn.clicked|self._send_plain_to_format_xml|237|原接收方法和触发次数保持|

### panels/interface_debug_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_HintLabel|84|见__init__及类内构建函数；worker只读|
|_LaunchBrowserWorker|118|见__init__及类内构建函数；worker只读|
|_RequestTestWorker|138|见__init__及类内构建函数；worker只读|
|_FilterChip|176|见__init__及类内构建函数；worker只读|
|InterfaceDebugPanel|186|_setup_ui:289；apply_layout_mode:4569|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|InterfaceDebugPanel / self._search_timer.timeout|self._rebuild_table|251|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._ingest_flush_timer.timeout|self._flush_ingest_ui|256|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._wait_hint_timer.timeout|self._on_wait_hint|262|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._status_tick.timeout|self._refresh_live_status|265|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._width_save_timer.timeout|self._save_column_widths_to_prefs|268|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._sig_capture_record|self._on_capture_record|271|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._sig_capture_error|self._on_capture_error|272|原接收方法和触发次数保持|
|InterfaceDebugPanel / self._sig_capture_stopped|self._on_capture_stopped|273|原接收方法和触发次数保持|

### panels/model_chat_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_ChatWorker|61|见__init__及类内构建函数；worker只读|
|_PingWorker|84|见__init__及类内构建函数；worker只读|
|ModelChatPanel|95|_setup_ui:114|
|_SkillManagerDialog|912|_setup_ui:924|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|ModelChatPanel / self.banner_close.clicked|self._dismiss_banner|133|原接收方法和触发次数保持|
|ModelChatPanel / self.model_combo.currentIndexChanged|self._on_model_changed|141|原接收方法和触发次数保持|
|ModelChatPanel / self.ping_btn.clicked|self._ping_current|144|原接收方法和触发次数保持|
|ModelChatPanel / self.skill_btn.clicked|self._open_skill_manager|147|原接收方法和触发次数保持|
|ModelChatPanel / self.search.textChanged|self._reload_sessions|167|原接收方法和触发次数保持|
|ModelChatPanel / self.new_btn.clicked|self._new_session|170|原接收方法和触发次数保持|
|ModelChatPanel / self.session_list.currentItemChanged|self._on_session_changed|175|原接收方法和触发次数保持|
|ModelChatPanel / self.rename_btn.clicked|self._rename_session|180|原接收方法和触发次数保持|
|_SkillManagerDialog / self.list_widget.currentItemChanged|self._on_selection_changed|930|原接收方法和触发次数保持|
|_SkillManagerDialog / self.add_btn.clicked|self._add|936|原接收方法和触发次数保持|
|_SkillManagerDialog / self.edit_btn.clicked|self._edit|939|原接收方法和触发次数保持|
|_SkillManagerDialog / self.toggle_btn.clicked|self._toggle|942|原接收方法和触发次数保持|
|_SkillManagerDialog / self.delete_btn.clicked|self._delete|945|原接收方法和触发次数保持|
|_SkillManagerDialog / self.close_btn.clicked|self.accept|948|原接收方法和触发次数保持|

### panels/ops_log_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_LinuxQueryWorker|45|见__init__及类内构建函数；worker只读|
|_SshTestWorker|63|见__init__及类内构建函数；worker只读|
|PasswordLineEdit|89|见__init__及类内构建函数；worker只读|
|_ExportBridge|105|见__init__及类内构建函数；worker只读|
|_Worker|111|见__init__及类内构建函数；worker只读|
|CategoryManageDialog|128|见__init__及类内构建函数；worker只读|
|ServerEditorDialog|258|见__init__及类内构建函数；worker只读|
|LogSettingsDialog|636|见__init__及类内构建函数；worker只读|
|ServerManageDialog|693|见__init__及类内构建函数；worker只读|
|CommandHistoryDialog|885|见__init__及类内构建函数；worker只读|
|OpsLogPanel|1028|_setup_ui:1719；apply_layout_mode:2721|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|CategoryManageDialog / self.add_btn.clicked|self._add|154|原接收方法和触发次数保持|
|CategoryManageDialog / self.rename_btn.clicked|self._rename|155|原接收方法和触发次数保持|
|CategoryManageDialog / self.del_btn.clicked|self._delete|156|原接收方法和触发次数保持|
|CategoryManageDialog / buttons.accepted|self.accept|164|原接收方法和触发次数保持|
|CategoryManageDialog / buttons.rejected|self.reject|165|原接收方法和触发次数保持|
|ServerEditorDialog / self.password_edit.textChanged|self._on_password_changed|310|原接收方法和触发次数保持|
|ServerEditorDialog / self.add_svc_btn.clicked|self._add_service_row|352|原接收方法和触发次数保持|
|ServerEditorDialog / self.del_svc_btn.clicked|self._del_service_row|355|原接收方法和触发次数保持|
|ServerEditorDialog / self.new_cat_btn.clicked|self._quick_new_category|378|原接收方法和触发次数保持|
|ServerEditorDialog / self.test_btn.clicked|self._test|411|原接收方法和触发次数保持|
|ServerEditorDialog / buttons.accepted|self._accept|412|原接收方法和触发次数保持|
|ServerEditorDialog / buttons.rejected|self.reject|413|原接收方法和触发次数保持|
|ServerEditorDialog / worker.finished_ok|self._on_test_ok|536|原接收方法和触发次数保持|
|LogSettingsDialog / buttons.accepted|self.accept|677|原接收方法和触发次数保持|
|LogSettingsDialog / buttons.rejected|self.reject|678|原接收方法和触发次数保持|
|ServerManageDialog / self.category_filter.currentIndexChanged|self._reload|725|原接收方法和触发次数保持|
|ServerManageDialog / self.manage_cat_btn.clicked|self._manage_categories|729|原接收方法和触发次数保持|
|ServerManageDialog / self.add_btn.clicked|self._add|742|原接收方法和触发次数保持|
|ServerManageDialog / self.edit_btn.clicked|self._edit|745|原接收方法和触发次数保持|
|ServerManageDialog / self.test_btn.clicked|self._test|748|原接收方法和触发次数保持|
|ServerManageDialog / self.del_btn.clicked|self._delete|751|原接收方法和触发次数保持|
|ServerManageDialog / close_btn.clicked|self.accept|757|原接收方法和触发次数保持|
|CommandHistoryDialog / self.list.customContextMenuRequested|self._menu|921|原接收方法和触发次数保持|
|CommandHistoryDialog / self.list.itemDoubleClicked|self._on_double|922|原接收方法和触发次数保持|
|CommandHistoryDialog / fill_btn.clicked|self._emit_insert|936|原接收方法和触发次数保持|
|CommandHistoryDialog / send_btn.clicked|self._emit_send|940|原接收方法和触发次数保持|
|CommandHistoryDialog / clear_btn.clicked|self._clear|944|原接收方法和触发次数保持|
|CommandHistoryDialog / close_btn.clicked|self.accept|948|原接收方法和触发次数保持|
|OpsLogPanel / self._bridge.result_ready|self._on_result_row|1044|原接收方法和触发次数保持|
|OpsLogPanel / self._bridge.finished|self._on_export_finished|1045|原接收方法和触发次数保持|
|OpsLogPanel / self._bridge.failed|self._on_export_failed|1046|原接收方法和触发次数保持|
|OpsLogPanel / self._query_worker.completed|self._on_linux_query_ok|1506|原接收方法和触发次数保持|
|OpsLogPanel / self._query_worker.failed|self._on_linux_query_fail|1507|原接收方法和触发次数保持|
|OpsLogPanel / self._query_worker.finished|self._refresh_linux_query_buttons|1508|原接收方法和触发次数保持|
|OpsLogPanel / dlg.insert_requested|self._set_cmd_bar_text|1566|原接收方法和触发次数保持|
|OpsLogPanel / self.cmd_send_btn.clicked|self._send_cmd_bar|1730|原接收方法和触发次数保持|

### panels/ops_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|CustomCommandDialog|20|见__init__及类内构建函数；worker只读|
|OpsPanel|105|_setup_ui:121；apply_layout_mode:316|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|CustomCommandDialog / buttons.accepted|self._accept_checked|64|原接收方法和触发次数保持|
|CustomCommandDialog / buttons.rejected|self.reject|65|原接收方法和触发次数保持|
|OpsPanel / self._copy_feedback_timer.timeout|self._restore_copy_button_text|116|原接收方法和触发次数保持|
|OpsPanel / self._search_debounce.timeout|self._refresh_results|146|原接收方法和触发次数保持|
|OpsPanel / self._completer.activated|self._apply_completion|153|原接收方法和触发次数保持|
|OpsPanel / self.category_combo.currentIndexChanged|self._refresh_results|158|原接收方法和触发次数保持|
|OpsPanel / self.add_btn.clicked|self._add_custom_command|161|原接收方法和触发次数保持|
|OpsPanel / self.safety_dismiss.clicked|self._dismiss_safety|179|原接收方法和触发次数保持|
|OpsPanel / self.command_list.customContextMenuRequested|self._show_command_menu|204|原接收方法和触发次数保持|
|OpsPanel / self.command_list.currentItemChanged|self._show_command|205|原接收方法和触发次数保持|

### panels/personal_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_KeepQueryCompleter|39|见__init__及类内构建函数；worker只读|
|PasteKnowledgeDialog|56|见__init__及类内构建函数；worker只读|
|KnowledgeEditDialog|88|见__init__及类内构建函数；worker只读|
|KnowledgeTab|138|_setup_ui:153；apply_layout_mode:348|
|DailyReportTab|1063|_setup_ui:1092；apply_layout_mode:1273|
|PersonalPanel|1887|apply_layout_mode:1916|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|PasteKnowledgeDialog / buttons.accepted|self._accept_checked|74|原接收方法和触发次数保持|
|PasteKnowledgeDialog / buttons.rejected|self.reject|75|原接收方法和触发次数保持|
|KnowledgeEditDialog / buttons.accepted|self._accept_checked|119|原接收方法和触发次数保持|
|KnowledgeEditDialog / buttons.rejected|self.reject|120|原接收方法和触发次数保持|
|KnowledgeTab / self._completer.activated[str]|self._on_suggestion_activated|169|原接收方法和触发次数保持|
|KnowledgeTab / self.search_edit.textChanged|self._on_search_changed|175|原接收方法和触发次数保持|
|KnowledgeTab / self._search_debounce.timeout|self._do_search|179|原接收方法和触发次数保持|
|KnowledgeTab / self.category_combo.currentIndexChanged|self._on_category_changed|185|原接收方法和触发次数保持|
|KnowledgeTab / self.paste_btn.clicked|self._paste_content|189|原接收方法和触发次数保持|
|KnowledgeTab / self.import_btn.clicked|self._import_documents|192|原接收方法和触发次数保持|
|KnowledgeTab / self.add_btn.clicked|self._add_entry|195|原接收方法和触发次数保持|
|KnowledgeTab / self.entry_list.customContextMenuRequested|self._show_entry_menu|223|原接收方法和触发次数保持|
|DailyReportTab / self._timer.timeout|self._check_reminder|1081|原接收方法和触发次数保持|
|DailyReportTab / self._draft_timer.timeout|self._persist_drafts_quiet|1086|原接收方法和触发次数保持|
|DailyReportTab / self._autosave_timer.timeout|self._autosave_tick|1088|原接收方法和触发次数保持|
|DailyReportTab / self.reminder_settings_btn.clicked|self._open_reminder_settings|1111|原接收方法和触发次数保持|
|DailyReportTab / self.date_tree.customContextMenuRequested|self._show_date_menu|1168|原接收方法和触发次数保持|
|DailyReportTab / self.date_tree.currentItemChanged|self._select_history|1169|原接收方法和触发次数保持|
|DailyReportTab / self.date_tree.itemExpanded|self._on_month_expand_changed|1170|原接收方法和触发次数保持|
|DailyReportTab / self.date_tree.itemCollapsed|self._on_month_expand_changed|1171|原接收方法和触发次数保持|
|PersonalPanel / self.daily_tab.reminder_due|self.reminder_due.emit|1910|原接收方法和触发次数保持|

### panels/requirement_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_ElideTextDelegate|39|见__init__及类内构建函数；worker只读|
|_WrapTextDelegate|81|见__init__及类内构建函数；worker只读|
|RequirementTree|261|见__init__及类内构建函数；worker只读|
|MonthPickerDialog|299|见__init__及类内构建函数；worker只读|
|MonthSelect|342|见__init__及类内构建函数；worker只读|
|DateInput|373|见__init__及类内构建函数；worker只读|
|RequirementAttachmentDialog|454|见__init__及类内构建函数；worker只读|
|SvnWorker|627|见__init__及类内构建函数；worker只读|
|SvnCheckoutDialog|643|见__init__及类内构建函数；worker只读|
|_SystemPickWidget|704|见__init__及类内构建函数；worker只读|
|RequirementDialog|740|见__init__及类内构建函数；worker只读|
|RequirementPanel|1415|_setup_ui:1453；apply_layout_mode:2122|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|MonthPickerDialog / buttons.accepted|self.accept|334|原接收方法和触发次数保持|
|MonthPickerDialog / buttons.rejected|self.reject|335|原接收方法和触发次数保持|
|DateInput / self.button.clicked|self._choose_date|385|原接收方法和触发次数保持|
|RequirementAttachmentDialog / buttons.accepted|self._accept|474|原接收方法和触发次数保持|
|RequirementAttachmentDialog / buttons.rejected|self.reject|474|原接收方法和触发次数保持|
|RequirementAttachmentDialog / export_btn.clicked|self._export_word|468|原接收方法和触发次数保持|
|RequirementAttachmentDialog / self.search.textChanged|self._filter_excel|485|原接收方法和触发次数保持|
|RequirementAttachmentDialog / self.table.cellDoubleClicked|self._copy_cell|507|原接收方法和触发次数保持|
|SvnCheckoutDialog / browse.clicked|self._browse|667|原接收方法和触发次数保持|
|SvnCheckoutDialog / buttons.accepted|self._accept_checked|674|原接收方法和触发次数保持|
|SvnCheckoutDialog / buttons.rejected|self.reject|674|原接收方法和触发次数保持|
|_SystemPickWidget / box.toggled|self.changed.emit|722|原接收方法和触发次数保持|
|RequirementDialog / self.system_pick.changed|self._rebuild_system_bindings|775|原接收方法和触发次数保持|
|RequirementDialog / self._standalone_dev_btn.clicked|self._pick_dev_local_path|798|原接收方法和触发次数保持|
|RequirementDialog / self._standalone_dev_clear.clicked|self.dev_local_path_edit.clear|804|原接收方法和触发次数保持|
|RequirementDialog / bind_folder_btn.clicked|self._bind_local_folder|810|原接收方法和触发次数保持|
|RequirementDialog / clear_folder_btn.clicked|self._clear_local_folder|811|原接收方法和触发次数保持|
|RequirementDialog / classify_btn.clicked|self._classify|884|原接收方法和触发次数保持|
|RequirementDialog / self.description_edit.textChanged|self._sync_test_points_description|899|原接收方法和触发次数保持|
|RequirementDialog / source_btn.clicked|self._load_documents|905|原接收方法和触发次数保持|
|RequirementPanel / self.scan_btn.clicked|self._scan_folder|1490|原接收方法和触发次数保持|
|RequirementPanel / self.update_all_btn.clicked|self._update_all|1491|原接收方法和触发次数保持|
|RequirementPanel / self.bug_btn.clicked|self._paste_bug|1492|原接收方法和触发次数保持|
|RequirementPanel / self.toolbar_more_menu.aboutToShow|self._sync_toolbar_more_menu|1499|原接收方法和触发次数保持|
|RequirementPanel / self._req_search_timer.timeout|self._refresh|1532|原接收方法和触发次数保持|
|RequirementPanel / self.status_filter.currentIndexChanged|self._on_filter_changed|1534|原接收方法和触发次数保持|
|RequirementPanel / self.kind_filter.currentIndexChanged|self._on_filter_changed|1535|原接收方法和触发次数保持|
|RequirementPanel / self.system_filter.currentIndexChanged|self._on_filter_changed|1536|原接收方法和触发次数保持|

### panels/settings_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|_AiProbeWorker|39|见__init__及类内构建函数；worker只读|
|ThemePreviewWidget|55|见__init__及类内构建函数；worker只读|
|ThemeCard|127|见__init__及类内构建函数；worker只读|
|SettingsPanel|193|_setup_ui:213；apply_layout_mode:692|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|SettingsPanel / self.reset_layout_btn.clicked|self._reset_layout_prefs|301|原接收方法和触发次数保持|
|SettingsPanel / self.opacity.valueChanged|self._preview_opacity|316|原接收方法和触发次数保持|
|SettingsPanel / self.reset_position_btn.clicked|self.reset_floating_position.emit|331|原接收方法和触发次数保持|
|SettingsPanel / self.edit_shortcuts_btn.clicked|self.edit_floating_shortcuts.emit|342|原接收方法和触发次数保持|
|SettingsPanel / self.reminder_save_btn.clicked|self._save_reminder_settings|374|原接收方法和触发次数保持|
|SettingsPanel / self.close_ask.toggled|self._refresh_close_behavior_hint|406|原接收方法和触发次数保持|
|SettingsPanel / self.close_default_action.currentIndexChanged|self._refresh_close_behavior_hint|407|原接收方法和触发次数保持|
|SettingsPanel / self.oracle_home_browse.clicked|self._browse_oracle_home|459|原接收方法和触发次数保持|

### panels/sql_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|SqlDraftWorker|39|见__init__及类内构建函数；worker只读|
|SqlExportWorker|57|见__init__及类内构建函数；worker只读|
|SqlToolPanel|78|_setup_ui:111；apply_layout_mode:887|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|SqlToolPanel / self._release_reload_timer.timeout|self._load_release_candidates|103|原接收方法和触发次数保持|
|SqlToolPanel / self.tabs.currentChanged|self._on_sql_tab_changed|108|原接收方法和触发次数保持|
|SqlToolPanel / self.release_date.dateChanged|self._release_date_changed|174|原接收方法和触发次数保持|
|SqlToolPanel / self.refresh_release_btn.clicked|self._load_release_candidates|178|原接收方法和触发次数保持|
|SqlToolPanel / self.release_context_toggle.toggled|self._toggle_release_context|197|原接收方法和触发次数保持|
|SqlToolPanel / choose_root.clicked|self._choose_release_root|237|原接收方法和触发次数保持|
|SqlToolPanel / self.release_generate.clicked|self._generate_release_materials|318|原接收方法和触发次数保持|
|SqlToolPanel / self.load_btn.clicked|self._load_file|607|原接收方法和触发次数保持|

### panels/test_points_editor.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|TestPointRow|25|见__init__及类内构建函数；worker只读|
|TestPointsEditor|102|见__init__及类内构建函数；worker只读|
|TestPointsDialog|364|见__init__及类内构建函数；worker只读|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|TestPointRow / self.check.toggled|self._on_toggled|41|原接收方法和触发次数保持|
|TestPointRow / self.text_edit.editingFinished|self._commit_edit|52|原接收方法和触发次数保持|
|TestPointRow / self.text_edit.returnPressed|self._commit_edit|53|原接收方法和触发次数保持|
|TestPointRow / self.delete_btn.clicked|self._on_delete|57|原接收方法和触发次数保持|
|TestPointsEditor / self.commit_seed_btn.clicked|self._commit_seed|143|原接收方法和触发次数保持|
|TestPointsEditor / self.add_edit.returnPressed|self._add_point|192|原接收方法和触发次数保持|
|TestPointsEditor / self.add_btn.clicked|self._add_point|196|原接收方法和触发次数保持|
|TestPointsEditor / self.extract_btn.clicked|self._extract_from_description|200|原接收方法和触发次数保持|
|TestPointsEditor / row.toggled|self._on_toggled|258|原接收方法和触发次数保持|
|TestPointsEditor / row.edited|self._on_edited|259|原接收方法和触发次数保持|
|TestPointsEditor / row.removed|self._on_removed|260|原接收方法和触发次数保持|
|TestPointsDialog / self.editor.changed|self._on_changed|398|原接收方法和触发次数保持|
|TestPointsDialog / close_btn.clicked|self.accept|406|原接收方法和触发次数保持|

### panels/ticket_submit_dialog.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|TicketSubmitConfigDialog|57|见__init__及类内构建函数；worker只读|
|TicketSubmitDialog|283|见__init__及类内构建函数；worker只读|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|TicketSubmitConfigDialog / self.list.currentRowChanged|self._show_profile|73|原接收方法和触发次数保持|
|TicketSubmitConfigDialog / seed_btn.clicked|self._pick_seed|136|原接收方法和触发次数保持|
|TicketSubmitConfigDialog / add_btn.clicked|self._add_profile|159|原接收方法和触发次数保持|
|TicketSubmitConfigDialog / del_btn.clicked|self._delete_profile|160|原接收方法和触发次数保持|
|TicketSubmitConfigDialog / buttons.rejected|self.reject|170|原接收方法和触发次数保持|
|TicketSubmitConfigDialog / buttons.accepted|self._save|171|原接收方法和触发次数保持|
|TicketSubmitDialog / cfg.clicked|self._open_config|361|原接收方法和触发次数保持|
|TicketSubmitDialog / buttons.rejected|self.reject|374|原接收方法和触发次数保持|
|TicketSubmitDialog / buttons.accepted|self._submit|375|原接收方法和触发次数保持|
|TicketSubmitDialog / self.profile_combo.currentIndexChanged|self._reload_requirements|377|原接收方法和触发次数保持|
|TicketSubmitDialog / self.env_combo.currentIndexChanged|self._on_env_changed|378|原接收方法和触发次数保持|
|TicketSubmitDialog / self.slot_combo.currentIndexChanged|self._refresh_preview|379|原接收方法和触发次数保持|

### panels/vin_panel.py

| 类 | 行号 | UI构建/适配位置 |
|---|---:|---|
|VinPanel|23|_setup_ui:32；apply_layout_mode:175|

| 所属类/现有signal | 当前receiver | 源行 | 实施约束 |
|---|---|---:|---|
|VinPanel / self.generate_btn.clicked|self._generate|38|原接收方法和触发次数保持|
|VinPanel / self.mode_combo.currentIndexChanged|self._on_mode_changed|54|原接收方法和触发次数保持|
|VinPanel / self.category_combo.currentIndexChanged|self._refresh_kind_options|108|原接收方法和触发次数保持|
|VinPanel / self.table.itemDoubleClicked|self._copy_cell|157|原接收方法和触发次数保持|
|VinPanel / self.copy_btn.clicked|self._copy|167|原接收方法和触发次数保持|
|VinPanel / self.export_btn.clicked|self._export|171|原接收方法和触发次数保持|

## 附录 B. 本次文档验证记录

- 当前Python解释器3.12.3及四个PyQt/Qt runtime包版本经importlib.metadata核对；未启动业务软件或访问数据库。
- requirements、package.json、Vite双入口、MainWindow宿主、Bridge、主题、布局、原生加载组件与模块AST已检查。
- 静态索引只保存代码标识/行号/哈希，不包含表单值、凭据、配置或业务载荷。
- 本文新增的是施工规格与loading视觉演示；没有修改正式Python/Vue逻辑或依赖。
- 技术和尺寸审阅完成；正式实现、性能基线采集、业务对照及全部视觉验收均属于后续开发执行，不在本次文档交付中冒充完成。

基线补注：收尾检查时工作区已切至review/final-test-gate-fix且若干tests文件有改动，本任务未执行分支切换或修改它们。60个已索引的main_window/panels/ui文件哈希仍一致，因此以上源码定位仍适用；文档开头的提交是本次采集基线，非强制开发分支。
