"""
聚合支付适配器（骨架/预留）——面向无执照小微商户的收款流水通道。

候选服务商（任选其一接入，均有成熟流水查询 API）：
- 收钱吧（shouqianba.com）：个体户/小微可办，收款码支持微信+支付宝+云闪付
- 付呗 / 商米 / 付桥：同类型聚合收单服务商
- 美团/抖音等平台商户后台导出的流水

对接方式（以收钱吧为例）：
  1. 商户注册 → 获取终端号(terminal_sn) 与 API 密钥
  2. 按日拉取「交易流水」接口 → 解析金额/时间/单号
  3. 复用 payment._import_txns 写入账本（source='aggregate'）

TODO:
- [ ] 确定服务商后，把 fetch_aggregate_bill 实现为真实 API 调用
- [ ] 在 payment.run_sync 中把 stub 替换为真实实现
"""
import logging

log = logging.getLogger("aggregate_pay")


def fetch_aggregate_bill(cfg, bill_date):
    """聚合支付渠道流水（未接入时抛错提示）。"""
    raise NotImplementedError(
        "聚合支付通道待接入（收钱吧/付桥等）。当前请使用："
        "① 微信支付商户号（有执照） ② mchid=DEMO 演示模式体验流水同步")


# 供 payment 模块引用的占位别名（保持调用点统一）
stub = fetch_aggregate_bill