# 历史行业标签

`market_data_platform.industry_history.expand_effective_industry_to_panel_dates` 将带有
`effective_date` 和可选 `end_date` 的行业标签展开到面板交易日。函数会统一证券列、日期列和
配置映射，按证券执行向后匹配，并过滤标签有效期之外的日期。

该变换只处理数据资产的时间有效性，不包含策略选择或模型逻辑。调用方负责加载行业文件和
面板数据，再把结果交给自己的特征或组合流程。
