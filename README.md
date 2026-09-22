# 二元等温闪蒸核算服务（Binary Isothermal Flash）

常驻运行的相平衡核算服务：给定两组分体系的温度、压力、进料总组成与饱和蒸汽压
信息（Antoine 或直给），逐点求解汽化率 β、液相组成 x、汽相组成 y，并判定
**两相 / 泡点以下液相单相 / 露点以上汽相单相**。仅经 HTTP 对外，以「核算作业」
为单位组织，物性定义可独立登记、跨作业复用；SQLite 持久化，重启不丢。

- 模型：理想溶液 + 理想汽相（Raoult 定律），`K_i = P_i^sat(T) / P`
- 汽化率：**Rachford–Rice 方程，全程只用二分法**在开区间 (0,1) 求根
- 技术栈：Python 3.12 + FastAPI + SQLite（WAL），容器一条 compose 拉起

## 求解方法与收敛判据（唯一一套）

Rachford–Rice 目标函数：

```
g(β) = Σ_i z_i (K_i − 1) / (1 + β(K_i − 1)) = 0,   β ∈ (0, 1)
```

- **方法：二分法（bisection）**，初始括号 `[0, 1]`。两相区内 `g(0)>0`、
  `g(1)<0`，且 g 对 β 严格单调递减，括号恒异号、必定收敛；不使用牛顿迭代。
- 收敛判据（满足任一即停）：括号宽度 `hi − lo ≤ 1e-10`，或迭代达到
  `200` 次上限。
- 收敛后额外校验 `|g(β)| ≤ 1e-8`（RR 残差），超差直接报错，不返回近似解。
- 相区判定：`g(0) ≤ 0` 为泡点以下液相单相；`g(1) ≥ 0` 为露点以上汽相单相；
  否则两相。所有 `K_i<1` / 所有 `K_i>1` 的情况自然分别落入这两侧，
  返回单相理由，β 为 `null`，绝不硬解出区间外的假汽化率。

容差：相组成求和与物料衡算 `z = (1−β)x + βy` 的闭合容差均为 `1e-8`
（进料组成归一化容差为 `1e-6`）。

## 组分下标约定（全服务唯一一份）

组分 0/1 的顺序**以物性定义 `components` 列表顺序为准**。Antoine 系数、
直给饱和蒸汽压、点级蒸汽压覆盖、进料 `feed`、液相 `liquid`、汽相 `vapor`、
平衡常数 `K` 全部按同一组下标对齐；K 值由 `app/thermo/antoine.py` 统一下算，
闪蒸内核不再单独维护任何组分顺序。

## Antoine 形式与单位

自然对数形式：`ln(P) = A − B/(T + C)`。登记物性时声明 `temperature_unit`
（`K`/`C`）与 `pressure_unit`（`kPa`/`bar`/`MPa`/`atm`/`mmHg`/`Pa`），
内部统一换算为 K、kPa。C 系数与所声明温标绑定（用 °C 登记时内部按
`C − 273.15` 平移到 K）。分母 `T + C` 接近零（`< 1e-6`）时按
`ANTOINE_DENOMINATOR_ZERO` 拒绝。

### 预置示范物性（可手工核对）

内置 `ps-demo-pentane-hexane`：正戊烷 / 正己烷，`ln(P/kPa) = A − B/(T_K + C)`：

| 组分 | A | B | C |
|---|---|---|---|
| n-pentane | 13.818327 | 2477.0750 | −39.9450 |
| n-hexane | 13.897213 | 2739.2473 | −46.8700 |

由常用 log10(mmHg, °C) 数据（戊烷 6.87632/1075.780/233.205，
己烷 6.91058/1189.640/226.280）换算。40 °C（313.15 K）下：

```
P_pent^sat ≈ 115.77 kPa,  P_hex^sat ≈ 36.97 kPa
等摩尔 z=(0.5,0.5)：泡点 P_b ≈ 76.37 kPa，露点 P_d ≈ 56.04 kPa
```

启动时同时预置示范作业 `demo-40C-sweep`（三个点：85 kPa 液相单相、
70 kPa 两相 β≈0.29496、50 kPa 汽相单相）。40 °C / 70 kPa 的 β 可由
二元解析式手算核对（RR 化为关于 β 的一次式），结果与二分一致。

## 构建与运行

```bash
docker compose up --build -d          # 服务 + 持久化卷一起拉起
curl http://localhost:8000/health     # {"status":"ok"}
# 交互式文档： http://localhost:8000/docs
```

数据落在命名卷 `flash-data`（容器内 `/data/flash.db`）。环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `FLASH_DB_PATH` | `/data/flash.db` | SQLite 文件，`:memory:` 为内存库 |
| `FLASH_SEED_DEMO` | `1` | 启动时幂等预置示范物性与作业 |
| `FLASH_HOST` / `FLASH_PORT` | `0.0.0.0` / `8000` | 直跑 `python -m app.main` 时用 |

本地无容器时：`pip install -r requirements.txt`，
`FLASH_DB_PATH=./data/flash.db uvicorn app.main:app --reload`。

## HTTP 接口

错误统一为 `4xx/5xx + {"error": {"type", "message", "details"}}`。

### 物性定义

- `POST /property-sets` — 登记物性（Antoine 模式或 direct 直给模式）
- `GET /property-sets` / `GET /property-sets/{id}`

```bash
curl -sX POST localhost:8000/property-sets -H 'Content-Type: application/json' -d '{
  "name": "pentane-hexane",
  "pressure_unit": "kPa", "temperature_unit": "K",
  "components": [
    {"name": "n-pentane", "antoine": {"a": 13.818327, "b": 2477.075, "c": -39.945}},
    {"name": "n-hexane",  "antoine": {"a": 13.897213, "b": 2739.2473, "c": -46.87}}
  ]}'
```

直给模式：两组分都不给 `antoine`，顶层给 `"psat": [P0, P1]`（顺序与
components 对齐）。不允许一组分 Antoine、另一组分直给（`MIXED_PROPERTY_SOURCE`）。

### 作业

- `POST /jobs` — 提交作业（引用 `property_set_id`，或内联 `property_set` 临时给出）
- `GET /jobs` / `GET /jobs/{id}` — 列表 / 取回整单全部点结果
- `GET /jobs/{id}/points/{index}` — 只取某一个工况点（点序号从 0 开始）
- `GET /jobs/{id}/points` — 该作业全部点

```bash
curl -sX POST localhost:8000/jobs -H 'Content-Type: application/json' -d '{
  "name": "run-1", "property_set_id": "ps-demo-pentane-hexane",
  "points": [
    {"temperature": 313.15, "pressure": 70.0, "feed": [0.5, 0.5]},
    {"temperature": 313.15, "pressure": 90.0, "feed": [0.5, 0.5],
     "psat": [115.77, 36.97]}
  ]}'
```

点级 `psat` 可覆盖本点蒸汽压（Antoine / direct 均可），便于灵敏度核算。
两点结果字段含 `is_two_phase`、`regime`、`reason_code`、`reason`、
`beta`、`liquid`、`vapor`、`K`、`psat`（kPa）、`temperature_k`、
`pressure_kpa` 与 `solver`（method/容差/迭代次数/g0/g1/残差）。
任一工况点非法则整单 `422 JOB_VALIDATION_FAILED`、不落库，
`details.errors` 逐点给出带类型的原因。

## 非法输入与错误类型

温度/压力/进料组成非正（`NON_POSITIVE_*`）、进料和偏离 1 超容差
（`FEED_SUM_NOT_ONE`）、Antoine 系数缺失或混合来源（`MIXED_PROPERTY_SOURCE`、
`MISSING_SATURATION_PRESSURE`）、Antoine 分母为零
（`ANTOINE_DENOMINATOR_ZERO`）、直给蒸汽压非正
（`NON_POSITIVE_SATURATION_PRESSURE`）、资源不存在（`*_NOT_FOUND`）等，
均返回带类型错误，服务不崩溃、不给近似解。

## 模块结构

```
app/
  main.py                    # 应用工厂、异常处理、路由装配
  config.py  errors.py  schemas.py  seed.py
  thermo/
    flash.py                 # RR 二分求解内核、相区判定、硬关系自检
    antoine.py               # Antoine 蒸汽压与 K_i（组分下标唯一来源）
    units.py                 # 压力/温度单位换算
  persistence/
    database.py              # SQLite 连接/WAL/建表/写锁
    property_repository.py   # 物性定义持久化
    job_repository.py        # 作业与工况点持久化
  services/
    property_service.py      # 物性校验、登记、复用
    job_service.py           # 作业受理、逐点求解、取回
  routers/
    properties.py  jobs.py   # HTTP 路由
tests/                       # 内核/热力学/API/非法输入/并发持久化
```

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

覆盖：两相组成求和与物料衡算闭合、相平衡关系、单相（含全 K 同侧）判定
不返回假 β、解析手算对照、**升压 β 不上升的单调性**、从两相侧逼近泡点
β→0 / 逼近露点 β→1、Antoine 单位换算与分母为零、各类非法输入带类型拒绝、
多作业并发提交不串号不覆盖、落库后重启仍可取回。

范围仅限二元等温闪蒸核算；不含前端、账户与精馏逐板计算。
