# 已核实事实

- 当前公众号正文为自有微信兼容渲染，不是微信官方渲染器；固定数学模板与预览外壳分层。
- 原compact全局折叠空白会破坏代码；436临时补丁删除外站href，会让标签链接丢失完整URL。
- 官方draft/update描述正文少于20000字符、小于1MiB；本地预算必须对最终导出HTML计算。
- 官方当前digest描述120字，旧writer120 UTF8字节、schema/publisher128字符口径冲突。
- 新封面需2.35_1与1_1两个独立裁剪区域；同media_id不能证明两种显示均完整。
- 复用doocs/md的导出阶段与URL脚注机制、wenyan的裁剪透传思路；现有Python生命周期和素材缓存继续使用。
- 内容质量与交付保真分开判断，采用知识页《自动化开发范式与智能体协作》对应段落。

公开来源：[官方新增草稿](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html)、[官方更新草稿](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_update.html)、[doocs/md](https://github.com/doocs/md)、[wenyan-core](https://github.com/caol64/wenyan-core)。本地审计快照保存在私有artifact，不提交账号素材。
