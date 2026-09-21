# Single Agent vs 1+4 workflow

Dataset cases: 20

| Workflow | Schema | Hard constraints | Citations | Success | Latency ms | Cost microunits | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| single_agent | 90.0% | 80.0% | 75.0% | 80.0% | 480.0 | 85.0 | FAIL |
| one_plus_four | 100.0% | 100.0% | 100.0% | 100.0% | 760.0 | 145.0 | PASS |

此报告由纯合成 Fixture 评测器生成，用于验证评测管线和发布闸门，不代表任何云模型的线上性能承诺。结果显示 1+4 在该固定集上提高了结构、硬约束、引用和任务成功率，同时增加了延迟与成本；因此发布决策看绝对质量阈值，而不是只看相对提升。
