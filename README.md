进度：骨架 基础概念√
0.1.0 前不保证配置文件兼容

astrbot_plugin_online_status/
│
├── services/                 # [应用层] 编排业务流程
│   ├── __init__.py
│   ├── generator.py              # 负责构建Prompt，调用LLM生成每日日程JSON
│   ├── manager.py                # 状态管理器，处理优先级和状态过期逻辑
│   ├── scheduler.py              # 封装APScheduler，执行定时任务
│   └── resource.py               # 资源调度&持久化日志&缓存清理
│
├── domain/                   # [领域层] 业务规则 & 数据模型
│   ├── __init__.py               # 定义 dataclass
│   └── schemas.py                # 定义 StatusEvent, DailySchedule 等数据结构
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
│   └── views.py                  # [视图层] WIP
│
├──_conf_schema.json          # 插件配置模板（含System Prompt模板在内的各种配置项）
└── main.py                   # →→→插件入口←←← 负责指令路由、依赖注入 (DI)、注册function call