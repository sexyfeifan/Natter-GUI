# 运行状态与生效标准

参考上游 [natter.py](https://github.com/MikeWang000000/Natter/blob/master/natter.py) 和 [NatterCheck](https://github.com/MikeWang000000/Natter/tree/master/natter-check)。GUI 不重写 NAT 算法和 WAN 检查。

## 独立状态

| 状态 | 来源 | 含义 |
|---|---|---|
| starting / running / restarting / stopped | 管理器及实际子进程 | 运行生命周期，不等于端口生效 |
| mapping | 原版 `-e` 通知 | 获取到外部地址，不等于外网可达 |
| LAN OPEN / CLOSED / UNKNOWN | 原版日志 | 原版从内部检查对应地址的结果 |
| WAN OPEN | 原版 PortTest | 原版外部检查认为端口可连接 |
| WAN CLOSED | 原版 PortTest | 原版外部检查认为端口关闭 |
| WAN UNKNOWN | 原版 PortTest | 外部检查不能判定，可能检查服务不可用 |
| NOT_CHECKED | GUI 表示“尚无原版检查输出” | 不是成功或失败；UDP 无原版 WAN 检查 |
| 人工验证 | 用户记录 | 独立的外网连接事实，不覆盖原版结果 |

原版 WAN 判定依次请求两个检查服务；任意一个明确开放则 OPEN；两者均明确关闭则 CLOSED；其他组合为 UNKNOWN。测试直接使用上游 `PortTest.test_wan`，验证提取后的结果与原版一致。

上游会依据目标端口与公网检查输出 `Target port is closed`、`Hole punching failed`、`You may be behind a firewall` 等提示。GUI 原文显示，不自行改变判断优先级。

当核心报告新 route 时，旧 LAN / WAN 结果清空，等待新的检查。核心进程重启时清除旧映射快照。进程停止时不把历史检查计入“外网 OPEN”服务数量。

## 人工验证

人工记录保存验证时间、结果、备注和当时的映射。公网 IP / 端口、目标地址、协议变化或服务停止时，记录标为历史；编辑、停止和重启服务会清除之前的验证。

客户端必须从真正外网测试，例如关闭手机 Wi-Fi。局域网内对公网 IP 的回环失败与原版 WAN UNKNOWN，都不能推导外网必然不可达。

## NAT 检测

面板直接启动原版 NatterCheck，显示原始 `OK / FAIL / NA` 和类型信息。该任务最长运行 150 秒；超时仅标记 GUI 任务错误，保留已经输出的日志，不构造虚假的 NAT 类型。

通用 NAT 类型检测使用独立临时端口。某个 Natter 服务经 UPnP 开放固定端口后，可能与通用 NAT 类型检测的结果不同；两者分别保留。

## 速度

首版没有内置测速。外网 OPEN 证明端口可达，不证明播放、上传或 BT 下载提速。比较速度必须控制客户端、网络、目标任务和应用设置。
