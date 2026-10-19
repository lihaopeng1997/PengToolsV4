# 下一步：Redis受控类型值编辑（原始7.3）

当前浏览页/游标已经接入并验证，完整目标仍active，值编辑尚未实现。现有 `RedisValuePage.context` 固定 workspace target、原始bytes key和selection_id；data是不可变bytes/tuple，表格每cell的UserRole是原始页row index（支持排序）。不要从显示文本或旧 `_selected_key` 反编码写入。

## 必须交付到产品

- String修改，Hash字段新增/修改/删除，List固定index修改，Set成员新增/移除，ZSet成员新增/改分数/移除；Stream保持明确只读，已有浏览和导出。原需求没有授权批量恢复或脚本后门。
- 纯预览先冻结操作/selector/old/new与完整目标，UI明确确认后才发送。二进制UTF8/Base64输入必须保真；changed输入使旧预览失效；无变化不写。显示成功/冲突/未知，无自动重试/SQL式回滚承诺。
- String页只是64KiB当前块，**不能把页前缀当完整值SET**。需要独立owner分块读取完整编辑状态（建议4MiB编辑预算），超过上限保持明确只读/导出；现有页的total/position不能被忽略。
- 应用前重新核对原连接版本/DB/Key/type与被改旧值。WATCH/MULTI/EXEC单次写，旧值变化或WatchError应阻止覆盖；若EXEC发送后断线，结果未知。停止请求不能撤销已生效修改，保留owner至真实结束。只读Agent工具集保持分离。
- 沿现有 `_run_worker(...on_created=...)` 注入dialog worker，完成/失败只缓存结果，finished后才解锁。大型String加载应在实际owner关闭后打开编辑器，切Key/Tab的旧load不得弹到新目标。

## 已核验SDK事实，避免重做大范围调查

本机redis8.1.0源码：Redis/Pipeline/RedisCluster/ClusterPipeline均提供公开 `set_retry`。ClusterPipeline的TransactionStrategy在创建时复制 `pipe.retry`，RedisCluster.pipeline传入`retry=self.retry`。因此需要在**创建pipeline前**对本任务专用client调用 `set_retry(Retry(NoBackoff(),0))`，不能只在创建后改pipe属性而放任策略保留默认重试。Worker每次有独立client/transport owner，不改变其他用户session或保存的配置。

Pipeline.execute使用conn.retry；`_disconnect_raise_on_watching`会在连接故障时抛WatchError，当前实现未显式`raise ... from`，原异常在异常上下文中。因此不要把所有WatchError当作确定未写的普通冲突；EXEC开始后的连接故障需报告未知，不自动重放。为这些分支写实际边界回归，不用纯字符串断言。

String保留TTL可用SET KEEPTTL（Redis6.0开始，官方 https://redis.io/docs/latest/commands/set/ 已核验）。不要静默回落为会清除TTL的SET；内网服务端版本未知。也不要用8.4新增条件选项假定内网兼容。其余HSET/LSET/SADD/ZADD等维持原key类型/TTL，新增不存在Key的意图应独立明确。

## 当前证据与限制

浏览增量：result.json.redis_bounded_value_browser_oct16；12相关通过，4 AST/whitespace，后续语言/陈旧标志相关病例通过。原loading反馈大脚本在精确dirty-before版本已失败于无效profile/draft的command dispatch；原测试文件字节未改，不以它绿灯证明新功能。新fixture实际扫描、固定draft和原始Key，运行真实生产Qt回调/worker。

无真实Redis/用户窗口/全量suite/打包。R8旧包没变。用户此前Esc停止GUI，仍不要操作其窗口。达梦延后；root亲自处理，不委派。
