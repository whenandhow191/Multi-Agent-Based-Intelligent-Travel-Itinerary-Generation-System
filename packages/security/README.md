# Security

这里集中实现跨 Provider 共用的安全边界：

- 外部 URL 必须使用 HTTPS、命中精确域名 allowlist，且不能包含用户信息或直接 IP；每次重定向都要重新校验。
- 外部网页、POI 描述和用户粘贴内容先包装为“不可信数据”，Prompt Injection 信号只影响风险标记，不能改变系统规则。
- 文本进入日志、异常或审计输出前先执行密钥模式脱敏。

工具权限仍由 `ToolGateway` 的 Agent allowlist 强制执行；安全检测不能替代该确定性权限边界。
