# 晴空棱镜代码实施检查点

更新：2026-09-14。这是进行中的实施记录，不是完整验收结论。

## 2026-09-14 实际截图源码追溯

本批基于 `1527ce4dc363590d4e1bda013e82db990ea08c4b`，只改诊断与图集。新主窗口采样记录source_head及选定UI源码路径的Git状态（包括未跟踪文件），不读取或写出文件内容；Git不可用时记录null。图集标明源码基准或“未记录源码版本”，含UI未提交修改时提示查看记录。旧图不补猜测SHA。该状态仅覆盖run/main_window、panels/ui/frontend及指定样式/网页/图标路径，不是整个运行环境或业务代码的内容快照。

重新运行并查看1440×900首页，隔离运行退出0，source_head为上述SHA、ui_source_changes为空，窗口尺寸与网页选中检查通过；图中数据为软件隔离环境的DEMO。图集仍64张，其浏览器交互未因本次静态生成而自动获得验证。提交后截图记录保留采样时的源码基准，不冒充生成记录的后续提交。完整UI与性能验收、NoSQL线程决策仍未结束。

## 2026-09-14 启动页减弱动效刷新

本批基于 `f4c666dd41de22b7fa18e9583e793a2a73de5b59`。新增回归复现StartupSplash在减弱动效下仍启动33ms装饰计时器。现在展示/重显仅在允许动效时启动，运行中的tick发现减弱动效则停止；原show_status、延迟、最短展示、finish流程及业务启动逻辑未改。

Windows隔离 `tests.test_prism_loading` 19项与 `tests.test_loading_feedback` 23项通过，共42项，退出0。新用例修复前失败，修复后验证静态仍可见、无装饰timer、允许动效后重显可启动、再次减弱停止且状态文字仍更新。此检查不代表实际CPU或启动耗时达标。外观与上次静态预览相同，未重复生成图。NoSQL线程范围仍待确认，其他完整验收继续保留。

## 2026-09-14 NoSQL宽窗口与连接空态

本批基于 `5ebb493fa3b1e5a2f244cf109e24618a56fb997f`。实际检查Redis/MongoDB各1440×900展开侧栏、1280×800折叠侧栏，四次通过网页加载/bridge就绪、实际侧栏选中、目标窗口尺寸核验。初始四图均查看，未发现结果区与分页/控制台新增重叠；RedisOverview保留内部滚动。发现无连接占位文字在下拉框截断，简化为“无 Redis 连接 / 无 MongoDB 连接”，创建说明置于该项ToolTipRole。占位UserRole仍None，连接列表及回调不变。

修改后重新运行上述四种实际窗口，均退出0，最新MongoDB折叠图已再次查看，空态文字完整。`tests.test_prism_database_layout` 4项通过。图集更新至64张：[Redis宽窗](shell/nav-22-1440-900.png)、[MongoDB宽窗](shell/nav-23-1440-900.png)、[Redis折叠](shell/nav-22-1280-800-collapsed.png)、[MongoDB折叠](shell/nav-23-1280-800-collapsed.png)。所有诊断使用临时配置、无网络连接，实际DPR为1.5。

这是两个入口的初始空态与两种窗口组合证据，不是完整尺寸矩阵、数据库业务或AI等待动效验收。NoSQL线程调整仍待用户对已准备需求的确认，没有将自动继续消息当作批准。其余不依赖线程决策的UI工作继续进行。

## 2026-09-14 启动卡片玻璃色解析

本批基于 `02fbd195b3faa9fcaabc758f8cfa3452d029777b`。检查发现StartupSplash直接使用QColor解析GLASS_BORDER与SHADOW的rgba字符串；实际执行QColor('rgba(38, 36, 56, 45)').isValid()为False。现在这两处绘制调用已有theme_manager.parse_color，保留边框(230,226,240,200)和阴影(38,36,56,45)的完整RGBA。未更改启动时序、延迟、窗口归属或动画策略。

Windows隔离 `tests.test_prism_loading` 18项通过，新检查核对calm边框/阴影透明度。隔离原生预览增加splash入口，生成并查看[480×280启动卡片](splash-glass.png)，offscreen运行退出0，DEMO状态仅用于画面检查。图片是静态离屏渲染，不证明Windows桌面合成与真实启动耗时。完整验收继续保留。

## 2026-09-14 NoSQL AI等待调用链审计

本批基于 `9649fd44d6efe159a6d4ddfba6ad217b130e26e6`。源码确认Redis/MongoDB的_ai_send直接同步调用chat_completions，模型方法同步等待_request；单纯加入定时绘图不能解决网络等待占用界面线程。未连接模型、不改请求或线程，实际阻塞时长未测。

新增 [后续需求草案](../../project/NOSQL_AI_WAITING_HANDOFF.md)，并从需求AI提示词链接，列出真实调用链、不变契约、线程范围批准条件、实施提案和验收。用户要求本轮不改变功能逻辑，项目规则要求线程所有权变化明确范围，因此此项保留未完成，不能把其他AI页面动画通过替代这两个入口的真实等待验收。本批只有文档，完成差异检查，没有运行无关测试。

## 2026-09-14 悬浮回归跨测试平台复验

本批基于 `8cceede8a6fcb963dd8618a0fd1e803392ceac20`。offscreen复验发现动画用例依赖Windows默认动效开启，以及英文Workspace在该字体度量下需要130宽但仅87。正常动画用例现在显式开启测试动效并清理；底栏用例自行设置calm/16字号，避免依赖其他用例的字体状态。英文底栏改为Home / Ticket / Edit，原完整tooltip和回调保持，中文不变。

Windows与offscreen隔离运行 `tests.test_quick_panel_lifecycle` 最终各16项通过、退出0，覆盖三个底部入口、动画和生命周期检查。没有改变生产动效默认设置、字体策略、提签配置或执行逻辑。未以两种Qt平台测试代替实际不同机器、物理屏幕及完整UI视觉验收。

## 2026-09-14 提签启用后的悬浮底栏

本批基于 `3497ec0289bd216100c2d7cb371d0d655215907c`。模拟已有提签配置时，底部三个入口同时出现，原“打开完整工作台”被压缩至96而sizeHint为106。改为“工作台 / 一键提签 / 快捷入口”，英文Workspace / Ticket / Shortcuts，完整说明保留在tooltip；固定40底栏与原按钮、回调和可见规则不变。

Windows隔离 `tests.test_quick_panel_lifecycle` 16项通过。新用例先复现中文压缩，再校验中英文全部按钮宽度/边界及40高度；实际点击提签仍调用原owner.open_ticket_submit(compact=True)，执行端为Mock，不提交任何内容。初次英文Submit ticket仍差1像素，缩短为Ticket后通过。生成并查看[三个底部入口](floating/mode-8-font-16.png)，offscreen退出0；旧mode1～7图片保留为历史状态，其底部文字已由本批更新。没有真实配置、网络请求或提签操作，完整验收仍未结束。

## 2026-09-14 减弱动效停止菱点刷新

本批基于 `132788fef25e76de97f681956e094d9c2da71dee`。新增检查复现ThinkingIndicator在减弱动效下画面静止但40ms计时器仍运行。现在start/show仅在允许动效时启动；运行中的tick发现动效关闭则停止计时器并绘制静态状态。is_running仍表示原任务等待状态，不取消任务、不改调用方契约。

Windows隔离验证：`tests.test_prism_loading` 17项和 `tests.test_quick_panel_lifecycle` 15项通过。新增减弱动效断言修复前失败、修复后通过，并检查隐藏后重显仍无计时器。要求正常动画启动的两项用例显式开启测试动效，避免依赖平台默认值；随后offscreen加载17项也通过。没有用这些计时器检查声称实际CPU改善多少，完整性能验收仍未完成。静态绘制外观没有变化，未重复生成图片。

## 2026-09-14 加载组件像素断言修正

本批基于 `528fe14a39a7b47dfb64ecb375b249fedd263d5f`。上一批Windows DPR1.5下的两项失败来自测试把QImage/QPixmap物理宽高直接等同逻辑尺寸。现在ThinkingIndicator仍断言控件高28，截图按28×实际DPR核对；StartupSplash保持480×280控件尺寸，Logo按deviceIndependentSize核对64×64，截图按逻辑尺寸×实际DPR核对。未修改正式UI、启动或显示策略。

同一隔离入口分别以QT_QPA_PLATFORM=windows与offscreen运行 `tests.test_prism_loading`，两次各17项全部通过、退出0，更新此前该组Windows有两项失败的历史状态。该组包含加载延迟、任务token、失败/隐藏计时器及基本绘图契约；不是全部加载调用点、真实双屏或帧率/CPU验收。LD-T08现有用例只抓单帧，不能据其名称声称已证明所有时间点无位移。业务与用户数据保持不变。

## 2026-09-13 收起态品牌等待边环

本批基于 `072a19d77f52983b83efd13ec44619fbc1475c08`，补齐上一节缺失的LD-07收起态。新增仅展示的BrandWaitRing，作为原44×44按钮的32×32居中子控件，1.6细弧、900ms周期；中心品牌图标和52×52外框保持。原聊天running回调控制同一个边环，展开后随原按钮隐藏而暂停，收起时恢复；完成/失败/停止/关闭时清除。鼠标透明属性让原按钮继续接收事件，不改变拖动事件过滤器、焦点、请求或线程逻辑。

Windows隔离 `tests.test_quick_panel_lifecycle` 15项通过。检查收起/展开时菱点与边环计时器交替、原尺寸和完成清除；新增实际绘图检查以两个受控时间点抓图，正常动效帧不同，减弱动效帧相同且timer停止；确认鼠标透明属性、未自动展开与52宽。原100次展开窗口/计时器集合检查继续通过。该绘图测试验证相位变化，不代表实测帧率或GPU性能。

新增并查看[收起等待态](floating/mode-7-font-16.png)，隔离offscreen静态预览退出0，52×52，仅品牌加细弧，没有文字或额外操作。没有向模型发送请求。真实Windows透明合成与跨屏、全部业务加载场景、性能仍未完成。无新依赖，构建信息及五份报告继续排除。

## 2026-09-13 悬浮AI等待动画接入

本批基于 `fa14302058ae66fbd3d4882d533a5d89392a6f35`。发现QuickPanel原等待状态仅切换发送/停止文字，未接LD-07展开态要求的LD-05。现在在原聊天区复用单个ThinkingIndicator，显示三菱点和“正在等待回复…”；通过原_sync_chat_running_state联动完成、失败、停止及关闭，不增加请求或更改线程/消息契约。收起时依靠原组件hideEvent暂停，展开恢复。修正ThinkingIndicator.start在父级不可见时仍启动timer的问题：只有实际可见才启动，逻辑running状态保留。

Windows隔离 `tests.test_quick_panel_lifecycle` 最终14项通过，新增完成/失败/停止按钮、收起恢复、关闭及隐藏父级start计时器检查。`tests.test_prism_loading` 在原定offscreen环境17项通过；Windows原生DPR1.5运行时有2项旧像素断言失败（图高42对28、Logo物理96对64），本批未改这两个断言，也不宣称Windows整组通过。共31项在各自说明的环境通过。

生成并查看[悬浮聊天等待态](floating/mode-6-font-16.png)，356×456外框保持，底部发送/停止与完整聊天入口可见。诊断tab6只设置DEMO等待状态，不发模型请求。该静态截图不单独证明动画逐帧变化；实际定时器启停由回归验证。LD-07收起态的32×32品牌边环尚未接入，仍属未完成项，完整DPI、性能及视觉验收继续保留。

## 2026-09-13 悬浮结果预览复核

本批基于 `8c611ce29527d1f2cd312e089b419fb2b7300f61`，仅补充诊断、测试与图片，未改产品源码。隔离预览支持floating tab4证件/tab5 VIN，均以16字号运行并退出0；逐一查看[证件](floating/mode-4-font-16.png)与[VIN](floating/mode-5-font-16.png)空结果态，返回、标题、生成按钮均可完整显示，截图包含透明外边距为316×396。

`tests.test_quick_panel_lifecycle` 最终13项通过。新增实际结果预览用例检查内容位于工具容器内、标题及可见按钮不被压缩、按钮位于预览范围；点击三个生成按钮仍分别传personal/unit/vin给原生成入口，测试在入口处替代执行，不生成实际数据。点击原返回按钮恢复工具网格。所有验证在临时配置中完成。

本批支持中文16字号空结果态与回调路由结论，不证明生成业务、填充结果、英文长文本、真实屏幕透明合成或完整性能验收。上述剩余状态继续保留。

## 2026-09-13 悬浮窗实际头尾尺寸落实

本批基于 `318ba3be3080dd2df592516b59b95df367698083`。核对7.4发现QuickPanel虽然用48/40常量计算总高，实际头部和底部仍是无高度约束的布局，且上下padding为10。现在头部与底部分别放进48/40高容器，四边padding统一12；工具总高度补足两处8间距及网格上下边距。聊天隐藏工具底部容器，仍保持340×440内容尺寸，学习仍360×520。原按钮、信号、拖动事件安装对象与模式规则保留。

`tests.test_quick_panel_lifecycle` 12项通过，新增实际头尾高度和上下padding断言、聊天底部隐藏检查；原134×58卡片/8间距、边界与100次展开用例全部通过。隔离预览新增可选font参数及学习tab3入口；生成并逐一查看16字号下[工具](floating/mode-1-font-16.png)、[聊天](floating/mode-2-font-16.png)、[学习](floating/mode-3-font-16.png)，三次退出0。包含外侧8透明边距的截图尺寸分别316×276、356×456、376×536；学习内容来自隔离临时配置的默认示例，不是用户数据。

图片是原生offscreen渲染，不能证明Windows透明合成、真实跨屏或所有大字体状态通过；部分悬浮控件按原QSS保留固定字体。完整验收继续进行。本批不增加依赖，不改业务逻辑，构建信息与五份阶段报告保留。

## 2026-09-13 悬浮窗边界与100次展开收起

本批基于 `b4ff3ef563f788752ee7c2464eaf698c6a74cba0`，仅补验证，不改正式代码。按规范7.4复查QuickPanel：已有用例覆盖52×52单图标、300工具宽/134卡片/8间距、340×440聊天、360学习，以及隐藏构造、偏好应用和关闭生命周期。

新增两项实际Qt控件检查：通过替代screenAt返回的可用矩形，模拟(0,0,960,640)与(-1280,-200,1280,800)两组屏幕，在四角分别展开工具/聊天共16组，展开矩形全部位于可用范围，收起回到原52×52锚点。另一用例在显示的隔离窗口连续100次展开/收起，每次核对原winId、位置、子QTimer对象集合，最终未发送草稿仍保留、学习搜索防抖计时器停止。

隔离运行 `tests.test_quick_panel_lifecycle` 最终12项通过，退出0。初次新用例把QLineEdit当作多行输入调用失败，修正测试使用原setText/text后重跑通过；没有为此改产品控件。没有使用真实用户会话或网络服务。

这证明所测工具模式反复展开没有增加Qt子计时器，不证明进程内存无泄漏或10分钟CPU指标；屏幕矩形为模拟，不代表物理双屏拖动、运行中DPI变化或屏幕移除。学习模式的尺寸已有检查，其四角定位和模式循环性能仍未由本批覆盖。完整视觉及性能验收保持未完成。

## 2026-09-13 Redis详情操作自动换行

本批基于 `03e609e5c4fef110a4fad8ca56cee3e2947c0c9c`。详情底部六个操作原来强制单行；英文16字号、856×528页面检查测得按钮被压缩为98宽，小于其99的sizeHint。改用已有WrapLayout按实际宽度换行，仍在详情滚动区内，不改任何操作回调或数据处理。

`tests.test_prism_database_layout` 4项通过，新增用例修复前失败、修复后通过：大字体英文详情无横向滚动，每个操作保持完整建议宽度，逐个滚动后完整进入视口；通过原_render_value渲染Hash/List/Set/ZSet示例，检查原页签选择、行内容及280值区高度。`tests.test_redis_panel_layout` 2项通过，原分栏及控制台层级保持。共6项通过，均退出0；仅在隔离配置中使用DEMO值，没有连接或修改数据库。

新增 [16字号实际窗口](shell/nav-22-960-640-tab-1-sample-font-16.png)，主窗口尺寸、实际页签和侧栏选中验证通过，已查看图片。图中详情操作位于当前视口下方，可达性由实际控件测试证明；不把此初始态截图当成按钮区的目视验收。图集现60张。其余长内容、编码/错误状态、跨屏和性能检查仍未全部完成。无新依赖，原构建信息与五份阶段报告未纳入。

## 2026-09-13 MongoDB文档页窄窗修复

本批基于 `646becf8749647cd72bed8d5fbec2be5001bf8ab`。检查原960×640实际截图发现分页按钮挤入结果表格底部；隔离回归同时测得JSON视图只有114高。文档页现在使用页签内部滚动，表格与JSON结果都保留至少120高度，分页与文档操作位于结果下方。原查询、重置、视图切换、分页、插入、删除、复制信号及数据逻辑未改，AI与Shell页签不变。

`tests.test_prism_database_layout` 3项通过：新增856×528页面中的表格/JSON真实按钮切换、查询与DEMO内容保留、结果高度、分页无重叠、滚动后复制按钮完整进入视口及窗口尺寸检查。修复前新增用例因JSON高度114不足120失败，修复后通过。`tests.test_nosql_command_guard` 2项通过。通过隔离诊断运行，均退出0，不连接真实数据库。

重新生成并查看 [MongoDB实际窗口](shell/nav-23-960-640.png)，分页与表格已分离，底部操作需要滚动到达；窗口大小与网页侧栏加载/选中记录通过。旧DPR图片为历史版本，不代表本次布局的全DPI验收。其他数据状态、子窗、真实跨屏及性能验证仍未全部完成。原构建信息修改和五份阶段报告继续排除。

## 2026-09-12 Redis详情滚动与MongoDB Shell复核

本批基于 `e216da7db8227de17981a12a8d3cf89c3b0cd6b7`。960×640的Redis详情页复现值区与底部操作重叠：值页签要求最小280，但右侧上半区更矮，父布局强行压缩后按钮绘入值区。现在Overview、Key详情和AI内容各自在原页签内滚动，保留原页签顺序与右下控制台；值区仍至少280，操作可通过滚动到达。左右两条垂直分隔统一16细线，Key列表接入圆角/选中样式。所有原按钮信号、编码切换、TTL与命令规则保持。

隔离测试：`tests.test_prism_database_layout` 2项通过，新增50行示例值、值区高度、按钮与值区不重叠、滚动后复制按钮完全进入视口、AI/详情切换后命令和文本保留；原测试覆盖Redis与MongoDB的页签和输入保留。`tests.test_redis_panel_layout` 2项通过，确认右下控制台及主分栏层级保留；`tests.test_redis_overview` 12项通过；`tests.test_nosql_command_guard` 2项通过。共18项通过，均退出0，不连接真实数据库。

最新图：[Redis总览](shell/nav-22-960-640.png)、[空详情](shell/nav-22-960-640-tab-1.png)、[示例值](shell/nav-22-960-640-tab-1-sample.png)、[MongoDB Shell](shell/nav-23-960-640-tab-2.png)。示例值和Shell已查看，主窗口大小、实际侧栏选中和目标页签记录通过；示例数据只用于隔离诊断。详情操作在内容底部，需要向下滚动，已由实际控件回归证明可达。

MongoDB本批没有改正式代码。Redis已有DPR截图属于本修复前的历史检查，不能代表新详情滚动布局的DPI验收。完整目标中的其他数据类型、长内容/错误/执行取消、实际跨屏及性能检查继续保留。`resources/build_info.json`及五份原阶段报告未纳入本批。

## 2026-09-12 四档DPR模拟检查

本批基于 `37edb53005ea3f6ba5a22da3c8cd5bb3dedaa2a9`，仅增加诊断、测试适配和截图，不改正式启动或显示策略。新增 `scripts/diagnostics/prism_dpi_matrix.py`：先在子进程测量本机原始DPR（本次1.5），再按目标/原始比例设置仅该子进程的 `QT_SCALE_FACTOR`。Qt文档说明该变量适合测试，其结果会乘上原生DPR，因此不能简单把变量值当作最终缩放比例。[Qt High DPI测试说明](https://doc.qt.io/qt-6/highdpi.html#testing)

已取得首页(0)、提醒设置(7/tab2)、Oracle(18)、Redis(22)、MongoDB(23)各DPR 1/1.25/1.5/2、字体16的20组960×640逻辑窗口PNG与JSON。独立读取全部JSON及PNG文件头验证：实际DPR与目标一致、窗口尺寸一致、侧栏选中一致，物理截图分别为960×640、1200×800、1440×960、1920×1280。设置四次运行退出0；后续批处理的会话句柄已失效、系统中无对应进程，本轮以20份已写出的记录与PNG交叉校验为证，不补称已取到丢失的最终退出码。

探针记录 `window_dpr`、`image_pixels`、`font_size`、`expected_dpr`；新增后运行的记录同时保存 `qt_scale_factor`。不符合目标DPR或像素尺寸时退出非0。图集已按DPR与字体区分，现有56张记录。已查看设置/首页/MongoDB的200%及Oracle的125%图片；未把记录完整等同于全部画面状态目视通过。

`tests.test_startup_dpi`最终2项通过：将过时的双主题卡片断言替换为当前单主题展示、18px字体、窗口尺寸和保存按钮边界检查；仍保留生产run.py不得设置旧高DPI环境变量的测试。

这批是Qt进程缩放模拟，不是修改Windows显示设置后的硬件验收。真实双屏、负坐标、运行中跨屏、其余页面/子窗大字体状态及性能仍待完成。没有使用这些结果替代完整UI验收。工作区外部变更 `resources/build_info.json` 保留，不纳入本批。

## 2026-09-11 设置模型列表与提醒时间

本批基于 `d60448b167a2350ce93ea63ea7ffcb8f30c59896`。模型配置列表接入统一圆角、行高、选中/悬停底色和禁用底色；长模型名称增加完整tooltip。提醒时间保留原QTimeEdit，整理内部编辑框和上下步进区，使用本地主题染色箭头，避免原生小方框与无可见箭头的混杂呈现。新增chevron-up资源遵守24×24、1.6线宽、currentColor规则，由ThemeManager注入QSS；不引入外部资源或新依赖。

实际回归：`tests.test_prism_settings_navigation` 2项通过，其中新用例在13/16字体下点击真实步进区验证17:30→17:31→17:30，禁用时点击不改变值，两个示例模型的选择仍填入原表单；`tests.test_theme_responsive` 52项通过；`tests.test_prism_icons_brand` 最终16项通过。合计70项通过。初次图标检查指出硬编码颜色/线宽不符，已按规范修正后重跑通过。

最新图：[提醒时间](shell/nav-7-960-640-tab-2.png)、[模型列表示例](shell/nav-7-960-640-tab-5-sample.png)。样例使用仅存在于诊断临时配置的两个禁用模型，不含URL/Token，不探测或保存真实配置。图片已查看，侧栏加载和选中DOM检查通过。图集已更新并区分示例数据。

本批仅修改展示属性、QSS/图标及诊断/测试，没有改变模型配置切换、提醒保存、时间规则或执行逻辑。剩余工作台状态、子窗口、DPI与性能验收继续保留在完整目标中。

## 2026-09-11 侧栏采样根因与22入口重新核验

本批基于 `32e480d625448a3be6d9676ce0e302f54fac0881`。已确认上一批诊断的等待条件不正确：`main_shell_renderer == web`只表示容器选中了网页控件，不能证明loadFinished和pageReady已经完成。探针可能提前导航，导致截图侧栏空白或初始首页选中。此次仅修改诊断脚本，等待既有 `_web_health.is_ready()` 的两页面loadFinished与bridge ready全部完成，然后导航、读取真实侧栏DOM，再保存截图。

现在JSON记录 `loaded_pages`、`bridge_ready_pages`、`chrome_dom.sidebar`、实际选中菜单文字及侧栏文档宽度。探针在选中项与原生上下文名称不一致、DOM缺少侧栏或横向溢出时退出非0，不再仅凭renderer值宣称已就绪。

已逐个运行全部22个叶子入口（0～13、16～23）的960×640主窗口，全部退出0；独立复查22份记录，窗口尺寸、目标导航与DOM选中项均一致，侧栏文档宽度与视口同为72。另复测格式XML页签与设置悬浮工具栏分区，均通过。最新XML、设置、Redis和MongoDB截图已目视确认侧栏绘出。此证据解决已复现的诊断等待缺陷，不证明所有启动时序、DPI及显示器环境均通过。

新增 [实际窗口检查图集](shell/index.html)，由 `scripts/diagnostics/prism_shell_gallery.py` 根据PNG与JSON生成，包含35张记录，默认仅显示24张已核对网页就绪/选中的图，旧采样可手动显示。图集为本地静态文件，不加载远程资源。内置浏览器的URL策略拒绝打开该file地址，未绕过限制，因此图集本身的浏览器交互验收未完成；本轮已核对图片文件与运行记录。用户可在本地浏览器手动查看。

本批不改正式UI、WebEngine策略、启动或导航代码，不连接真实数据库/模型服务，不改五份原有阶段报告。全入口的初始态导航检查不替代长内容、执行/取消、弹窗、DPI和性能验收。图集中的Redis/MongoDB等工作台仍需按剩余状态清单核对。

## 2026-09-11 格式工具、文档与设置逐页检查

本批基于 `44a9316fe1062a9d475c7ef0bad406de4c991840`。检查格式工具四页签、设置六分区和接口文档的960×640客户区，并直接修正发现的展示偏差：XML和SQL格式页的工具栏按实际按钮宽度换行，避免文字挤压；XML、文本辅助、接口文档编辑分栏统一16间隔和细线；Oracle客户端模式选择器加宽，完整显示原选项。未改转换算法、SQL执行/校验规则、文档更新流程或设置保存键。

隔离回归：`tests.test_prism_workbench_overflow` 7项通过，新增格式四页签宽窄切换保留长文本及SQL、工具栏完整按钮宽度/无重叠、XML原格式化回调、Base64编码解码还原原文、文档SQL与作者保留及原多文件选择回调；`tests.test_prism_settings_navigation` 1项通过，遍历六分区并检查修改值/固定保存入口；`tests.test_theme_format_tools` 7项通过；`tests.test_xml_formatter` 12项通过。合计27项，均退出0。

主窗口探针新增 `--tab`，记录请求页签与实际页签并校验一致。当前图包括 [接口文档](shell/nav-3-960-640.png)、格式工具 [JSON](shell/nav-11-960-640.png)/[XML](shell/nav-11-960-640-tab-1.png)/[SQL](shell/nav-11-960-640-tab-2.png)/[文本辅助](shell/nav-11-960-640-tab-3.png)，以及设置 [外观](shell/nav-7-960-640.png)/[悬浮工具栏](shell/nav-7-960-640-tab-1.png)/[提醒](shell/nav-7-960-640-tab-2.png)/[安全](shell/nav-7-960-640-tab-3.png)/[Oracle](shell/nav-7-960-640-tab-4.png)/[模型](shell/nav-7-960-640-tab-5.png)。原生页面区域已逐图查看。

证据边界：部分Qt客户区抓图中WebEngine侧栏呈白色或仍显示首页选中态，即使JSON renderer为web；这些图只支持本轮原生页面布局核对，不能当成侧栏绘制和选中同步通过的证据。延长导航后采样到1000ms未彻底消除，后续需要区分采样限制与真实绘制/同步问题。没有因此修改生产WebEngine策略、导航或Bridge契约。设置模型列表和时间控件的视觉一致性仍待细查；各页长内容、错误/禁用状态及全DPI/性能验收仍按完整目标继续。

## 2026-09-10 发版验证适配问题已解决

本批基于 `3101f58b8f7fc346b1671118d69f66d4987cd20b`，只改测试和隔离诊断，不改正式业务源码。上一节历史记录中的两项未通过结果由本节更新：

- SQL导入按钮的固定两级parent断言改为检查按钮确实属于原SQL页签和滚动工作区；增加点击按钮到原多文件选择器的回调检查，模拟取消，不打开或导入真实文件。页签顺序、日期候选自动加载及生成前日期同步仍按原用例验证。
- 诊断的 `local_data_dir` 替代函数接受原参数。默认应用路径仍指向临时目录；显式exe路径参数交给原函数进行纯路径计算（已检查原实现不访问文件系统）。升级测试继续检查同目录不同exe共用data，并增加不同安装目录不应得到同一路径的反例，避免把恒定临时路径误当成升级兼容证明。

最终执行 `QT_QPA_PLATFORM=windows PYTHONUTF8=1 python -u scripts/diagnostics/prism_web_runtime.py --test-module tests.test_release_ui`：28项通过，退出0，无跳过。初次追加回调检查误用了单文件选择器补丁，已停止该独立测试进程并改为原代码实际调用的多文件选择器后重新运行；不采用中断运行作为通过证据。数据路径、SQL导入、候选加载和发版生成的正式实现保持不变。完整UI的其余视觉、DPI及性能验收仍未完成。

## 2026-09-10 Agent、命令库与需求目录续修

本批基于 `49105c915af257d71a25f56a9934ba5c558c536a`。检查960×640真实主窗口后修正以下展示偏差：

- 新创建页面和侧栏折叠时沿用 `LayoutModeController.low_height`，不再固定传入False。低高度下打开Agent等页面可立即应用已有紧凑规则；只修正展示状态传递，不改变导航索引、面板创建顺序或启动策略。
- Agent输入区最小高度由100调整为规格120，消息/输入与文件树/预览两个垂直分栏采用16间隔及细线呈现。项目文件开关和原草稿保持。
- 命令库预览区由125恢复规格240，仍在既有右侧滚动区中；原固定底部复制入口可见，生成/复制业务未改。
- 需求目录全选、删除、提签、展开、折叠的原控件按可用宽度换行，消除窄栏按钮重叠；目录/详情分隔统一16。工作区增加无边框滚动容器，低高度时详情和文件区内容完整可达，不依赖窗口外的裁切区域。

最新真实主窗口图：[Agent](shell/nav-17-960-640.png)、[命令库](shell/nav-6-960-640.png)、[需求管理](shell/nav-10-960-640.png)，均960×640逻辑客户区，探针退出0。Agent和需求图已检查对应修复；截图仅展示当前滚动位置，不是整个内容拼图。

隔离验证：`tests.test_prism_workbench_overflow` 5项通过（包括新增的Agent输入/项目文件开关保留草稿、需求目录按钮不重叠与原展开/折叠行为、命令预览高度及复制入口可达）；`tests.test_prism_main_shell` 19项通过（新增低高度创建页面、侧栏折叠和恢复大窗）；`tests.test_agent_workbench_layout` 10项通过。

`tests.test_release_ui` 实际运行28项，26项通过、1项失败、1项错误，不能报告整组通过：`test_release_page_is_first_and_date_auto_loads_candidates`仍直接断言SQL页面按钮两级parent等于页签，与已有滚动容器层级不符；`test_upgrade_reuses_data_directory_and_accepts_legacy_requirement`对local_data_dir传入两个参数，与隔离脚本的无参替代函数不兼容。本批未修改SQL发版源文件或这份测试文件，已用Git确认二者与基线相同。未通过项保留记录，未更改正式数据目录逻辑来让测试通过。合计本批60项通过、2项未通过；无真实模型执行、SVN提交或生产数据操作。

## 2026-09-10 工作台窄窗重叠修复

本批基于 `168dce13805b0ce31a3baa69f9a7b57cdcce27c0`。真实960×640主窗口检查发现：SQL连接栏虽然未撑大窗口，固定宽连接选择器与目标提示/按钮仍发生重叠；接口排查上下布局的空列表提示越过左侧容器，压住详情页签。宽窗切回窄窗的SQL定向测试还确认工作区最小高度将独立页面从528撑到764。

- 四种SQL工作台的连接工具栏使用按控件实际尺寸换行的布局。保留原控件、信号绑定、显隐和启用状态；长连接提示允许换行。工作区放入无边框滚动容器，高度不足时保留完整编辑器/结果区及原分栏。
- 接口排查保留原上下/左右分栏和原请求测试页签，在上下布局时按子区域实际最小高度为工作区留空间，由外层滚动容器承担低高度滚动，空列表提示不再覆盖详情页签。恢复宽窗时清除上下布局专用最小高度。
- 主窗口诊断改为等待WebChannel就绪后再导航，避免截图中页面已切换但初始化侧栏仍选中首页。此修改仅在诊断脚本，不改变正式导航或启动策略。

当前图：[SQL 960×640](shell/nav-18-960-640.png)、[SQL 1440×900](shell/nav-18-1440-900.png)、[接口排查960×640](shell/nav-12-960-640.png)、[聊天960×640](shell/nav-16-960-640.png)。低高度的工作区需要滚动，截图不是完整滚动内容的拼接。SQL与接口截图已检查对应重叠问题；聊天本轮仅补主窗口运行记录。

定向验证均使用临时配置隔离运行：`tests.test_prism_workbench_overflow` 2项通过（四方言、中英文、13/16字体、宽窄切换、控件边界与相互重叠、SQL输入保留和原测试连接回调、接口请求体保留与滚动可达）；`tests.test_sql_workbench_query` 21项通过；`tests.test_interface_fiddler_workbench` 31项通过；`tests.test_splitter_prefs` 14项通过。合计68项通过，均退出0。四份最新主窗口探针退出0。

未连接实际数据库或启动实际抓包。本批没有更改查询、连接、过滤、监听、代理、分页或数据保存逻辑。其余页面状态、全部子窗口、DPI和性能证据仍按后文清单继续，不以此宣布完整UI验收。

## 2026-09-10 主窗口组合预览检查点

本批基于 `836321d5c21fbbbc85ed974f7e378ad1865037c1`，只补充隔离诊断与截图，不改正式软件代码。`scripts/diagnostics/prism_web_runtime.py --shell` 使用真实 MainWindow、正式 WebEngine 侧栏和首页；配置重定向到临时目录，禁用本次诊断进程的托盘/全局快捷键服务及 Python 外部连接。首页数据为明确标记的示例数据。

| 逻辑窗口尺寸 | 侧栏初始偏好 | 页面实际尺寸 | 截图 |
|---|---|---|---|
| 1440×900 | 展开 | 1144×772 | [整体预览](shell/nav-0-1440-900.png) |
| 1440×900 | 折叠 | 1308×772 | [折叠预览](shell/nav-0-1440-900-collapsed.png) |
| 1280×800 | 展开 | 1020×680 | [整体预览](shell/nav-0-1280-800.png) |
| 1100×720 | 展开，窄窗自动收缩 | 984×608 | [整体预览](shell/nav-0-1100-720.png) |
| 960×640 | 展开，窄窗自动收缩 | 856×528 | [整体预览](shell/nav-0-960-640.png) |

五份 JSON 均记录窗口未被内部最小尺寸撑大、当前导航为首页、侧栏和首页 renderer 均为 web、上下文栏52和状态栏28。`collapsed` 字段表示传入的初始偏好，不代表窄窗最终有效侧栏宽度。截图为 Qt 客户区抓图，像素尺寸受当前屏幕缩放影响，不证明其他 DPI 或系统非客户区效果。已查看1440展开及960截图；其余截图已生成，仍需视觉核对。

本轮重新执行隔离的 `tests.test_prism_main_shell`：18项通过，退出0。1440展开/折叠、1280、1100的真实主窗口探针均退出0；960已有相同格式的运行记录。未执行真实数据库、网络连接或完整业务流程，未以本次首页组合检查替代全部页面验收。

## 2026-09-10 子窗口与消息显示续修

本批基于 `2a9cb230f286c1d6ed78aa5ae985d0835b6e2b23`。可打开 [子窗口检查图集](dialogs/index.html)，在13/16像素全局字体设置间切换，点击查看原图。

- 修复确认框在布局建立前按空sizeHint确定180高度，导致短正文被压缩的问题。现在按实际带样式的文字高度确定初始大小；超长内容进入滚动区，固定取消与确认按钮不被挤走。
- 通知与后续操作窗口采用相同的长正文保留/滚动策略；原 accepted/rejected、确认默认焦点、Esc取消、selected_action返回值未改。回归验证了长正文、实际可滚动范围、操作按钮与返回结果。
- 修复确认/标准弹窗按钮的QSS最小高度叠加padding后超过32的问题；实际带主题渲染的按钮尺寸检查通过。
- 分类、技能、对象/字段选择、提签配置与需求列表采用统一白色圆角列表、选中底色与行间距。只增加展示属性和QSS，不改列表数据、选择规则或回调。

`scripts/diagnostics/prism_dialog_preview.py` 在临时配置和禁止Python外部连接的条件下生成真实Qt客户区截图；显式装入本机中文字体。模拟可用屏960×640，最终24类窗口加3个长文场景，各以13/16像素字体运行，共54份PNG及对应JSON。全部退出0，尺寸未超过可用屏减48，固定按钮未越过窗口或父容器；这不代表所有滚动内容、业务状态、真实多显示器和DPI已经验收。渲染探针不执行提交、连接、检出或删除。

定向测试使用 `PYTHONUTF8=1 python scripts/diagnostics/prism_web_runtime.py --test-module <模块>`：`tests.test_prism_dialogs` 13项通过；`tests.test_connection_dialog` 13项通过；`tests.test_ai_object_tokens` 7项通过；`tests.test_ticket_submit` 11项通过、2项跳过（临时配置下无ECIF样例签）。合计44项通过、2项跳过，不将跳过项计为通过。不使用生产模板补齐测试。

图集覆盖各类初始状态，连接认证分支和对象选择由上述定向测试补充部分交互证据。整窗集成、其余状态和附录尚未覆盖子窗仍在完整目标内，未宣布全UI验收完成。

## 2026-09-10 学习、日报和聊天续修

本批基于已推送的 `edc0c50647d39f49e61f6c63ba92d44485ba6718`。以下证据补充并更新下方历史表中的对应待查项，不代替其余模块的完整验收。

| 页面/组件 | 实际修复与证据 | 当前图 |
|---|---|---|
| 日报 | 原日期行按钮超出视口；日期、辅助操作分行，保存/复制/删除固定底部。低高度下历史与编辑区不再把共享页面撑高。问题与计划编辑区最小120，保留今日完成优先伸展。窗口切换保留草稿，点击保存仍调用原保存方法；昨日计划、富文本和图片回归通过 | [864×520](daily-verified-864.png) |
| 学习 | 检索与分类移入260资料栏；16分隔；保留实际既有左右布局。搜索防抖、选择结果和拖拽分栏在布局变化后保留，搜索测试使用隔离样例 | [1144×740](learning-verified-1144.png) |
| 聊天 | 低高度时收缩可滚动消息区，输入框仍至少120；原风险提示不隐藏。25行样例的完整文本、内容高度、滚动和复制、未发送草稿、发送按钮可达均通过 | [864×520](chat-verified-864.png) |
| SQL/通用分隔 | 四SQL工作台上下/左右改16分隔；带prismGutter的垂直分隔改为16命中区域中的细线，避免原粗横条。实际控件测量横向宽16、垂直高16；日志/发版/数据库布局回归通过 | [Oracle 1440×740](oracle-verified-1440.png) |

最终代码下定向验证共51项通过：`tests.test_daily_report_upgrade` 12、`tests.test_p09a_delivery_panels` 6、`tests.test_p09b_database_panels` 5、`tests.test_splitter_prefs` 14、`tests.test_model_chat_bubbles` 10、`tests.test_prism_learning_layout` 1、`tests.test_prism_release_layout` 1、`tests.test_prism_log_layout` 1、`tests.test_prism_database_layout` 1。分别使用 `PYTHONUTF8=1 python scripts/diagnostics/prism_web_runtime.py --test-module <模块>` 在临时配置下运行，均退出0。

两个旧测试预期已按批准规格更新：日报不再要求所有按钮挤在同一行，改为可达性及真实保存回调验证；旧black主题在ThemeManager中必须归一到calm，不恢复双主题。纯调色函数的显式色板输入测试仍保留。

四张当前截图实际渲染尺寸均等于请求尺寸，未报告可见控件横向溢出。截图使用原生控件、临时配置和演示内容，无数据库/模型连接。`*-audit-before.png` 是故障对照。其余阶段图不因本次修复自动成为最新证据。

仍需完成：其他页面的原动作/长内容/状态矩阵、全部子窗口、整窗集成及DPI/性能要求。以上进展不代表整个UI目标完成。

当前基础提交：`80861f0bccba22eb1d36a3e09a497d8949ba3ebf`，分支 `ui/prism-v1`。用户已明确要求将本轮修复和交接材料提交并推送，故本次作为可读取的实施检查点交付。包含本记录的提交才是本次交付，完整 SHA 以 `git log -1 --format=%H -- docs/ui/prism-implementation-2026-09/STATUS.md` 为准；上述基础 SHA 不包含本次新增改动。此次发布不代表全套 UI 已通过最终视觉验收。

## 本次提交前复核（2026-09-09）

- 远端 `ui/prism-v1` 与基础提交一致，无需合并。
- 使用 `python scripts/diagnostics/prism_web_runtime.py --test-module <模块>` 隔离运行：`test_p09a_delivery_panels` 6项、`test_p09b_database_panels` 5项、`test_prism_sidebar_chrome` 11项、`test_visual_native_surfaces` 21项、`test_splitter_prefs` 14项、`test_prism_settings_navigation` 1项、`test_prism_compact_tools` 2项、`test_dashboard_release_monthly` 12项、`test_prism_database_layout` 1项、`test_redis_panel_layout` 2项、`test_prism_release_layout` 1项、`test_prism_log_layout` 1项，均退出0。模块名均以 `tests.` 为前缀。这些检查包含源码契约和实际控件检查，不全部属于视觉检查。
- 最新正式资源下，Windows 平台 `python -u scripts/diagnostics/prism_web_runtime.py --integration-tests` 3项通过。以上合计80项，不含更早批次的重复计数。
- 本次 `npm --prefix frontend run typecheck` 和 `verify:embedded` 均通过；正式资源已由上一轮 `build:embedded` 生成。
- 最新动效修复：Vue scoped 样式的完整后代选择器移入 `:global(...)`，避免编译后丢失后代部分。此前运行采样确认正常动画变化、隐藏时暂停、减弱动态时静止。三种样例视口864×520、1020×592、1440×740已取得无横向溢出的DOM记录，见同目录 `web-runtime-*.json`。
- 当前材料优先查看网页首页 `web-dashboard-1144-740.png`、三种尺寸的 `web-dashboard-*.png`、原生首页 `home-sample.png`、实际原生动效 `native-motion.gif`。其他图片与下表对应；带 before/current 的图片是历史对照，不能冒充最终效果。

业务边界：本次 `tools/` 仅调整接口排查的默认显示分栏比例；主窗口仅为原生首页快捷入口接入既有导航方法。原业务计算、数据库操作、SSH会话及生产启动策略不在本次修改中。完整状态覆盖和人工视觉确认仍按下文待验收清单执行。

## 本轮已经落实的代码与证据

| 展示面 | 当前改动 | 已取得证据 | 仍需检查 |
|---|---|---|---|
| 原生首页 | 左侧欢迎/统计/任务、右侧上线总览/工具；窄窗堆叠；短任务列表自然高度；快捷入口中英切换 | `home-sample.png`，实际1144×740；月份、完成同步及列表行为测试 | 窄窗、长文本、英文完整页面；动效运行证据 |
| 设置 | 180侧栏、六类分区、1040居中、窄窗选择器、固定保存操作 | 真实控件导航测试，保留输入、语言、保存按钮可见 | 所有分区最终截图 |
| 聊天、Agent、命令 | 初始侧栏与间距；保留用户分栏；聊天/Agent输入区高度 | 分栏基础行为回归；部分页面截图 | 最终截图、聊天长消息、上下文开关和窄窗 |
| SQL发版 | 左320上下文，右输入/预览；窄窗上下排列且页内滚动；导出固定底部；恢复窄窗清空/检查/预览入口 | `release-sql-after.png`，1144×740；`release-narrow.png`，680×740；两尺寸渲染回归通过 | 原按钮与字段交互、切换模式保留输入 |
| 接口排查 | 请求测试表单页内滚动；52/48默认比例；16分栏间距 | `interface-after.png`，1144×740，实际左右52/48 | 原有偏好及各页签交互、窄窗 |
| 日志排查 | 左260；服务器/抓取/目录/导出按钮重排；导出结果表自然高度 | `logs-after.png`、`logs-export.png`，1144×740；`logs-narrow.png`，680×740；三种隔离渲染均无控件横向溢出 | 长路径、样例结果、多会话保留与低高度 |
| Oracle/MySQL/OceanBase/达梦 | 结果/字段表局部最小高度修复，AI按钮两列排列 | 四引擎从936恢复740高；`oracle-1440.png`检查三列工作区 | 样例查询/对象状态、AI取消显示、窄窗、最终间距 |
| 接口文档 | 左侧文档选择/输出树，右侧编辑；修正文案 | `documents-after.png`，1144×740 | 最终文案截图、窄窗与文件选择回归 |
| Redis | 左240；局部表格最小高度修复；值区仍min280；命令区保留右下 | `redis-after.png`，1144×740；页签、输入保留、侧栏几何测试 | Key类型样例/长内容、低高度与窄窗 |
| MongoDB | 左240；过滤96；结果自然伸展；原Shell控件放入工作区页签 | `mongodb-after.png`、`mongodb-shell.png`，1144×740；页签、输入保留、侧栏几何测试 | 过滤96的最终截图；查询/JSON与Shell模拟回调验证 |
| 证件、VIN、加解密、格式化 | 小高度表格下限、VIN两行筛选、加解密/格式化页内滚动 | 864×520渲染；VIN指定条件不撑大窗口、参数继续进入原生成方法的测试通过 | 长文本、各格式页签、复制和导出动作复核 |
| Vue首页 | 浅色Hero与系统字体；常用工具两列最小宽修复；正式资源重建 | typecheck/build/verify通过；Windows实际绘制 `web-dashboard.png`；3项真实WebEngine集成测试通过 | 多宽度样例状态及网页动效证据 |
| 原生动效 | 使用既有首页棱镜、AuroraProgress、ThinkingIndicator真实动画 | `native-motion.gif`，60帧，各组件60个不同帧；隐藏后暂停；减弱动态下两次间隔180ms截图完全一致；40项加载状态检查通过 | 全局减弱动态设置与各业务调用位置的集成核对 |
| 悬浮框 | 复核既有展开与聊天展示，无本轮业务修改 | `floating-0.png`：52×52；`floating-1.png`：含阴影316×260，内容宽300；`floating-2.png`：含阴影356×456，内容340×440；无横向溢出 | 学习搜索、快捷入口、屏幕边缘锚点交互 |
| 后续AI协作 | 完整需求AI提示词，包含开发任务与提交后代码复核循环 | `../../project/AI_REQUIREMENTS_PROMPT.md` | 最终交付SHA及材料索引补齐 |

本目录 `*-before`、`*-current` 以及部分较早 `*-after` 图片是过程材料，不应一起作为最终验收截图。最终交付需清理/整理为明确的当前图集。所有新截图由 `scripts/diagnostics/prism_preview.py` 使用临时配置与数据生成，并禁止外部连接；样例任务和对话不是生产数据。该脚本关闭动画，所以 PNG 不证明动态效果。

## 最近运行的验证

续轮新增：`python -m unittest tests.test_prism_log_layout tests.test_splitter_prefs tests.test_prism_database_layout tests.test_dashboard_release_monthly -v`：28项通过，退出0。共享分栏修复了“构造期被最小宽度挤压后，实际显示仍沿用失真比例”的问题；新增回归在构造宽100、最终宽1144时验证保存52/48恢复正确。四SQL工作台修改后又跑14项分栏测试，通过。

本次继续验证：`python -m unittest tests.test_prism_loading tests.test_loading_feedback -v`：40项通过；`python -m unittest tests.test_prism_release_layout tests.test_prism_log_layout -v`：2项通过，含5个实际页面/尺寸场景。`scripts/diagnostics/prism_motion_preview.py`正常退出，使用Qt实际计时器采样并验证动态/暂停/静态三种行为。它不启动业务流程，也不伪造业务进度。

WebEngine诊断已取得新证据：单事件循环探针最初退出1是因测试使用 `QApplication([])`，Qt明确报告缺少程序名；探针修正为非空参数后可运行。offscreen仍有GPU绘制错误与旧集成测试访问冲突，因此实际绘制验收使用Windows平台，未修改生产启动、GPU或sandbox策略。绘制在pageReady后等待1000ms采样，早先150ms采样为空白，不作为有效图片。Windows真实截图现已取得。

`QT_QPA_PLATFORM=windows PYTHONUTF8=1 python -u -X faulthandler scripts/diagnostics/prism_web_runtime.py --integration-tests`：3项通过，退出0。测试运行前重定向配置常量和目录到临时目录、禁止Python外部连接。覆盖MainWindow+需求面板自然信号同步、navModel权威，以及生产HTML+WebChannel+Vue与DOM点击链路。

实际DOM探针曾发现常用工具按钮使文档宽1307超过视口1144；修复QuickTools网格minmax及按钮min-width后，最终文档宽与视口同为1144，溢出元素为空。截图还揭示欢迎区错误使用浓色品牌渐变及未声明全局字体，现已按浅色玻璃风格修复。

- `python -m unittest tests.test_dashboard_release_monthly tests.test_prism_settings_navigation tests.test_splitter_prefs -v`：26项通过，退出0。
- `python -m unittest tests.test_prism_database_layout tests.test_redis_panel_layout -v`：3项通过，退出0。覆盖实际窗口尺寸、页签切换、命令输入保留及240侧栏；不连接数据库。
- `npm --prefix frontend run typecheck`：通过。
- `npm --prefix frontend run build:embedded`：通过。Vite提示未来默认配置加载方式的兼容警告，本次构建未失败。
- `npm --prefix frontend run verify:embedded`：通过，双入口资源与WebChannel契约检查通过。
- `git diff --check`：通过；仅有Git换行转换提示。

这些结果不能证明全部22个叶子页面、子窗口、动效与业务合同已完成验收。

## 下一步按此收尾，不能缩减目标

1. 复核4种SQL工作台、需求、学习/日报、格式化、证件/VIN、命令、聊天和Agent的规格、原有动作与窄窗。未实际核验的模块保持待验收。共享分栏算法修复后，旧截图需按最终代码更新。
2. 补齐SQL分栏切换与输入保留、接口排查偏好与各页签交互；检查文档、数据库、日志页的长内容和空/错误/禁用状态。新增UI缺陷由本轮修复，不能推给后续AI当新需求。
3. 核对全局菜单、主窗、图标、弹窗、悬浮框和所有加载呈现。核对既有源码与V2.1中的尺寸、暂停、减弱动态、隐藏与销毁要求，补动态证据。
4. WebEngine的Windows绘制和3项集成检查已通过；继续验证网页多视口样例状态和动效。offscreen GPU异常作为测试环境限制记录，不改生产策略去绕过。
5. 最终以逐条需求矩阵审计代码、动作合同、运行证据和图集；检查后续AI提示词及索引；检查业务层未意外改动。
6. 按用户本次明确指令先发布实施检查点，后续完整验收仍需补齐上述证据。每次提交前fetch并核对远端；保留原有五份未跟踪阶段报告，不加入本任务提交。

原有五份报告：`scripts/p09a_fix1_report.txt`、`p09a_fix2_report.txt`、`p09b_fix1_report.txt`、`p09b_fix2_report.txt`、`p09b_report.txt`。
