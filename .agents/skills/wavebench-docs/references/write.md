# Write 模式

> 加载时机：新增页面，或按已确定职责重写一篇具体页面时加载。

## 写前合同

开始正文前回答：

- 谁会来看？
- 当前要完成什么？
- 读完后能做到什么？
- 哪些陈述来自哪些 canonical source？
- 页面主类型是什么？相关细节应链接到哪里？

无法回答时先做局部 audit。不要把一篇混合页面原样换个标题。

## 按类型写作

### Tutorial

固定一条可成功的学习路线。列出 prerequisites；每个关键步骤给可观察的预期结果；解释只保留完成路线所需内容，深层原因链接 Concept。

### How-to

从明确任务开始。给必要条件、真实硬件风险、最短可靠步骤、Verification 和常见失败；参数全集链接 Reference。

### Reference

覆盖 synopsis、syntax/schema、inputs、outputs、exact behavior、side effects、errors 和 compatibility/capability requirements。可从代码生成的表不手工复制。

### Concept

解释 problem、model、how it works、rationale 和 trade-offs；链接相关 Guide 与 Reference，不把操作步骤或发布流水账塞进来。

## WaveBench 特有检查

- 示例命令先与当前 `--help`、`run schema` 或实现核对。
- 涉及真实设备的步骤明确区分离线、连接读取和设备写入。
- 不写入真实 IP、序列号、串口、凭据、本地实验目录或私有证据。
- 型号级 SCPI、profile、quirk 和验证状态链接 instrument plugin 仓库。
- Current、Experimental、Proposed 和 Historical 使用明确标签。
- README 只保留 landing page 内容，不展开内部合同。

## 中文表达层

页面结构、事实源和技术边界确定后，再使用 `tech-doc-style-chinese` 完成中文措辞与排版：直角引号、克制语气、术语与中西文留白、机器字面量保护等由该 Skill 负责。

不要复制那套通用规则到本 Skill，也不要加载其与 WaveBench 无关的 `Project-Overrides.md`。若 WaveBench 以后需要术语覆盖，应在仓库内建立并评审自己的规则。

## 完成条件

- reader outcome 可验证；
- 事实均能追溯到 canonical source；
- 示例与路径可执行或明确标注未执行原因；
- 导航入口和 related pages 已更新；
- 文档 audit 无新增错误；
- 未验证、Experimental 或未来内容没有伪装成当前能力。
