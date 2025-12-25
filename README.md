# 🟢 Astrbot Plugin Online Status | 在线状态

<div align="center">

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-purple?style=flat-square)](https://github.com/Soulter/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)
[![Version](https://img.shields.io/badge/Version-0.0.4-orange?style=flat-square)]()

** 让闲置的状态显示发挥它应有的作用 **
<br>
*极客  优雅  更多😊*

</div>

---

## 📖 简介
* 定时日程、LLM工具、指令切换Bot在线状态
* 提示词注入实现在线状态感知（自身、对话对象）
<br>进度：主要功能 √

## ✔ 计划清单
* 配置-优先级
* 配置-状态时长（针对预设或随机）
* 指令-日程管理
* 指令-预设管理
* 功能-电量变化
* 功能-输入状态
* 功能-节假日感知
* 正式的说明文档

## 🧱 依赖
AstrBot >= 4.0.0
<br>Napcat
<br>pydantic

## 🌳 目录结构
```
astrbot_plugin_online_status/
│
├── services/                 # [应用层] 编排业务流程
│   ├── __init__.py
│   ├── generator.py              # 调用LLM生成每日日程JSON
│   ├── manager.py                # 状态管理器，处理优先级和状态过期逻辑
│   ├── scheduler.py              # 封装APScheduler，执行定时任务
│   └── resource.py               # 资源调度&持久化日志&缓存清理
│
├── domain/                   # [领域层] 业务规则 & 数据模型
│   ├── __init__.py               
│   ├── constants.py              # 常量值设定（部分日后会做成配置项）
│   ├── schemas.py                # 定义数据结构
│   └── factory.py                # 数据工厂: 规则校验、清洗、默认值
│
├── adapters/                 # [通信层] 接口适配
│   ├── __init__.py 
│   ├── base.py                   # [抽象基类] 定义接口
│   ├── astr.py                   # AstrBot 方法层
│   └── napcat.py                 # napcat 方法层
│
├── utils/                    # [工具层] 辅助方法
│   ├── __init__.py
│   ├── config.py                 # 数据&配置读写&转换（如json）
│   └── views.py                  # [视图层]
│
├──_conf_schema.json          # 插件配置模板（含Prompt模板、映射表在内的各种配置项）
└── main.py                   # →→→插件入口←←← 指令路由、依赖注入、LLM工具、事件钩子
```

---

<div align="center">
🔔 Merry Christmas~<br>
Made with 😊 by LilDawn
</div>