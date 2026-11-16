# DMDP · Duck Minecraft Downloader for Plain Craft Launcher

一个仿 PCL2 风格的 Minecraft 下载器：**原版 + Mod 加载器一次装好**，装完 PCL 直接能启动。

- 界面：PyQt6，蓝色主题、标题栏内嵌标签页、折叠版本列表
- 引擎：自研 `dmdp_engine.py`，多文件并发 + 单文件分片 + 断点续传 + 字节级进度 + SHA1 校验
- 镜像：游戏文件、资源、**Forge / NeoForge / Fabric / Quilt 元数据与安装包都走 BMCLAPI**
- 路径：默认把 `.minecraft` 放在程序旁边（和 `PCL.exe` 同级），PCL 打开就能识别

---

## 一、快速开始

### 方式 A：直接用现成的 exe（推荐给玩家）

1. 从 Releases 下载 `DMDP.exe`
2. 把它丢到 PCL 所在文件夹（和 `PCL.exe` 放一起）
3. 双击运行 → 选版本 → 需要的话顺手选个 Mod 加载器 → 开始下载

下载完成后目录长这样：

```
你的文件夹/
├─ PCL.exe
├─ DMDP.exe
└─ .minecraft/
   ├─ versions/1.20.1/
   ├─ versions/1.20.1-forge-47.4.10/
   ├─ libraries/
   └─ assets/
```

### 方式 B：从源码跑

```bash
pip install -r requirements.txt
python dmdp.py
```

只想验证引擎是否正常：

```bash
python dmdp_engine.py --plan 1.20.1          # 只算清单，不下载
python dmdp_engine.py --install 1.20.1 --dir ./test-mc
python dmdp_engine.py --list-loaders fabric --mc 1.20.1
```

---

## 二、游戏目录：为什么默认在程序旁边

PCL 的习惯是「启动器在哪，`.minecraft` 就在哪」。所以 DMDP 的默认值是：

| 运行方式 | 默认 `.minecraft` |
|---|---|
| 打包后的 exe | `exe 所在目录/.minecraft` |
| 源码运行 | `dmdp.py 所在目录/.minecraft` |

规则细节：

- 设置里「游戏目录」**留空 = 跟随程序目录**，这是默认。
- 以前版本存下来的旧默认值（`%APPDATA%\.minecraft`）会被自动当成「跟随程序目录」，不用手动改。
- 如果程序装在 `Program Files` 这类没有写权限的地方，会自动回退到用户目录下的 `.minecraft`，并在设置页显示实际路径。
- 也可以手动指定任意目录；设置页会实时显示「当前实际使用」的路径。

### 安装版怎么找到 PCL2

安装包默认装在 `%LOCALAPPDATA%\Programs\DMDP`，这里不是 PCL2 的目录，所以程序提供了两种找法：

1. **首次启动自动探测**：还没有设置文件时，后台扫描程序目录、桌面、下载、我的文档和各盘根目录（最多 5 秒），
   找到 `PCL.exe` / `PCL2.exe` 就弹窗问你要不要用它的 `.minecraft`；选「是」就写进设置。
2. **设置页「查找 PCL2」按钮**：深度扫描（最多 15 秒，最多 4 层），找到后直接把「游戏目录」填成
   `<PCL2 目录>\.minecraft`；有多个结果时取第一个，其余数量会在提示里显示。

扫描会跳过 `Windows`、`Program Files`、`AppData`、回收站等系统目录，并用 `realpath` 去重
（junction/软链接不会重复列出）。命令行也能查：`DMDP.exe --find-pcl` 或
`python dmdp.py --find-pcl`，每行输出一个目录。

---

## 三、Mod 加载器（内置，不再需要去 PCL 里折腾）

下载页的详情面板里有一行 **Mod 加载器**：

```
Mod 加载器  [不安装（仅原版）▾]  [自动（最新版）▾]   共 137 个版本，默认取最新
```

支持四种：

| 加载器 | 版本来源（BMCLAPI） | 说明 |
|---|---|---|
| Fabric | `/fabric-meta/v2/versions/loader/{mc}` | 官方 meta 镜像 |
| Quilt | `/quilt-meta/v3/versions/game`；per-game 接口镜像缺失时自动回退官方 `meta.quiltmc.org` | |
| Forge | `/maven/net/minecraftforge/forge/maven-metadata.xml` | 按 `{mc}-` 前缀过滤，数字倒序 |
| NeoForge | `/maven/net/neoforged/neoforge/maven-metadata.xml` | 版本号归一化成 `1.x.y` 再匹配 |

安装流程：

1. 先装原版（version json、client jar、libraries、assets、log_config）
2. 找 Java：设置里的 Java 路径 → Mojang 已安装运行时 → 系统 Java（优先精确匹配主版本，其次取最小的更高版本）
3. 都没有且开了「自动下载 Java 运行库」→ 从 Mojang 下载对应 runtime（1.20.1 约 58 MB）
4. 调 `minecraft-launcher-lib` 的加载器安装器（Forge/NeoForge 会跑 processors，Fabric/Quilt 跑 installer jar）
5. 生成 `versions/{加载器}-{版本}-{mc}/`，PCL 里直接多出一个可启动版本

几个要注意的点：

- **Forge / NeoForge 必须有 Java 才能装**（安装器本身是 jar）。Fabric / Quilt 同样要 Java 跑 installer。
- 老版本 Forge 在镜像上偶发 429/503，程序会退避重试，重试仍失败会自动切到官方源（资源文件 → `resources.download.minecraft.net`，Maven → `libraries.minecraft.net`）。
- NeoForge 不支持 1.20.1 这类老版本，下拉框会显示「没有可用的加载器版本」，属正常。
- 装加载器失败不会破坏已装好的原版，可以在下载页对同一版本再点一次，只补加载器。

---

## 四、多线程到底怎么分工

一句话：**有多个文件时，不同文件并行；只剩单个大文件时，把这个文件切成若干段并行；两种情况下总连接数都不超过「多线程数」。**

### 1. 多文件阶段（绝大多数时间）

安装一个 1.20.1 需要 **3631 个文件**（52 个 library、3577 个 asset 对象、1 个 log_config、1 个 client jar）。引擎的做法：

- 先把清单**去重**（Forge 的 libraries 里存在重复条目），每个文件一个任务；
- 任务丢进文件池（`max(4, 线程数 + 2)` 个工作线程）；
- 每个任务真正发请求前，要先拿到一个**全局令牌**（`BoundedSemaphore(线程数)`）。

所以「多线程数 = 8」意味着：不管池子里排了多少任务，同一时刻最多 8 条 HTTP 连接。这样既能把小文件流水线跑满，又不会把镜像打挂。

### 2. 单文件阶段（client jar、大 library、Java 运行时压缩包）

当某个文件同时满足：

- 体积 ≥ 「分片阈值」（默认 8 MB）
- 体积 ≥ 1 MB × 分片数（避免切出一堆几百 KB 的碎片）

就把它的字节区间按 HTTP `Range` 切成「分片数」段（默认 4 段），每段一个任务：

```
client.jar  23.0 MB
├─ bytes 0        ~ 5.7 MB   ─┐
├─ bytes 5.7 MB   ~ 11.5 MB   ├─ 4 个分片任务并行
├─ bytes 11.5 MB  ~ 17.2 MB   │
└─ bytes 17.2 MB  ~ 23.0 MB  ─┘
         ↓ 全部写完后按顺序拼接
   client.jar（临时名 .dmdp-part → 校验 SHA1 → 原子改名）
```

关键点：

- 分片任务也要抢同一个全局令牌，所以**总连接数不会翻倍**（8 个文件线程 + 每文件 4 分片 ≠ 32 条连接）。
- 服务器不支持 Range（返回 200）时，立刻退回单连接整包下载，不会卡死。
- 分片文件是 `xxx.part0`、`xxx.part1`…，中断后重来会**从已有长度继续**（断点续传），不是从头下。
- 下完先拼成 `.dmdp-part`，通过大小与 SHA1 校验后才 `os.replace` 成正式文件——中途失败不会留下半个坏 jar。

### 3. 队列是串行的

界面上可以连续点多个版本，但**同一时刻只跑一个安装任务**，其余显示「排队中」。原因：

- 镜像的限速是按 IP 的，两个任务同时跑只会互相拖慢、更容易触发 429；
- 进度、速度、当前文件这些统计是全局的，多任务并行会串台。

单任务内部才是并发的（多文件 + 分片）。

### 4. 额外的小机制

| 机制 | 作用 |
|---|---|
| 按路径加锁 | `minecraft-launcher-lib` 的库列表有重复项，两个线程可能同时写同一个 jar；加锁后同路径串行 |
| 429/5xx 全局冷却 | 任何线程收到 429/503，全体暂停 1.5–10 秒（尊重 `Retry-After`），避免继续打镜像 |
| 可选文件 | 老版本 lwjgl natives 的 classifier 可能 404，这类文件失败不算整体失败 |
| 未知大小 | 先发 `Range: bytes=0-0` 探测总大小与是否支持分片 |
| 限速 | 全局令牌桶，按 MB/s 限制，多线程共享同一个额度 |

---

## 五、设置项

| 设置 | 默认 | 说明 |
|---|---|---|
| 下载源 | BMCLAPI | 切到 Mojang 官方源后，加载器元数据也走官方 |
| 多线程数 | 8 | 同时下载的文件数，也是总连接上限 |
| 单文件分片数 | 4 | 大文件切几段 |
| 分片阈值 | 8 MB | 小于它的文件不分片 |
| 失败重试次数 | 3 | 指数退避，镜像 503 时很关键 |
| 速度限制 | 0 | 0 = 不限 |
| 校验文件完整性 | 开 | SHA1 不匹配则删掉重下 |
| 游戏目录 | 空 | 空 = 程序同目录 `.minecraft` |
| Java 路径 | 空 | 空 = 自动检测 |
| 自动下载 Java 运行库 | 开 | 仅安装加载器时需要 |

---

## 六、打包（GitHub Actions，本地不用装依赖）

本机没有 Windows 环境时，不要本地打包。把仓库推上 GitHub，让 Actions 出 exe：

1. 新建仓库，把整个项目传上去（注意 `.github/workflows/build.yml` 是两级目录）。
2. 打开 **Actions**，流水线会自动：
   - 打一个带控制台的 `DMDP-selftest.exe`；
   - 运行 `DMDP-selftest.exe --selftest`（验证 PyQt6、引擎、界面能加载）；
   - 再打正式的 `DMDP.exe`；
   - 输出 `SHA256.txt`；
   - 上传 `DMDP-win64` 产物。
3. 需要正式发版：**Releases → Create a new release**，tag 打 `v1.1.0`，构建完 exe 会自动挂到 Release。

本地（有对应平台时）：

```bash
pip install -r requirements.txt pyinstaller
python build.py              # dist/DMDP.exe
python build.py --console    # dist/DMDP-selftest.exe，可跑 --selftest
```

---

## 七、常见问题

**Q：下载完 PCL 里没有新版本？**
确认 PCL 的 `.minecraft` 和 DMDP 的「实际使用目录」是同一个。设置页会直接显示当前路径；把 DMDP.exe 放到 PCL.exe 旁边通常就一致了。

**Q：卡在某个 library 一直重试？**
镜像偶发 429/503。程序会冷却 + 退避 + 自动切官方源。仍失败时，把「失败重试次数」调到 5，或临时切到 Mojang 官方源。

**Q：Forge 安装很慢 / 失败？**
Forge 除了下载，还要用 Java 跑 processors（编译 patches），CPU 和磁盘都会忙一阵。确认 Java 17+ 可用；1.20.1 建议直接让它自动下载 `java-runtime-gamma`。

**Q：为什么不用多任务同时下载多个版本？**
见上文「队列是串行的」：镜像按 IP 限速，多任务只会互相拖慢，而且统计会串台。任务内部已经是多线程 + 分片。

**Q：下载中断了要重下吗？**
不用。分片有 `.partN` 续传，单文件有 `.dmdp-part` 续传；已校验通过的文件会直接跳过。

**Q：为什么双击 exe 会提示「未知发布者」/ SmartScreen 拦截？**
因为发布包没有代码签名。签名证书需要向 CA 申请：OV 证书要企业资质和年费，EV 更贵且要硬件 token；
个人项目基本只有两条路——申请 Certum 的 Open Source Developer 免费证书（面向开源项目），
或者用自签名证书（只能保证文件没被篡改，**不会**消除 SmartScreen 提示，除非用户手动把证书装进「受信任的根证书颁发机构」）。
不签也可以：把 `SHA256.txt` 一起发出去，让用户核对哈希。Linux 下签名可用 `osslsigncode`：

```bash
osslsigncode sign -pkcs12 my.pfx -pass 密码 -in DMDP-1.1.0-setup.exe -out DMDP-signed.exe
```

---

## 八、目录结构

```
dmdp.py            界面（PyQt6）
dmdp_engine.py     下载引擎（镜像 / 并发 / 分片 / 加载器 / Java）
build.py           PyInstaller 打包脚本
requirements.txt   运行依赖
.github/workflows/build.yml   GitHub Actions 自动构建
```

## 更新检查（GitHub 优先，Gitee 兜底）

查询顺序：

1. `https://api.github.com/repos/AoLanXiang/DMDP/releases/latest`（超时 8 秒）
2. 拿不到（被墙、超时、仓库还没建）就退到 `https://gitee.com/api/v5/repos/AoLanXiang/DMDP/releases/latest`（超时 15 秒）

仓库路径写在 `dmdp.py` 的 `UpdateChecker.REPO`，可用环境变量 `DMDP_REPO` 覆盖。
两个源都不通时只报错误、不误报「已是最新」。

tag 用 `v1.1.0` 没问题：代码会剥掉 `v`/`V`，也认 `release-1.1.0`，并区分 `1.1.0-beta.1` 与正式版。

> 注意：GitHub 优先意味着国内网络下通常会先卡 8 秒再落到 Gitee。如果嫌慢，
> 把 `SOURCES` 里两条顺序对调即可，一行改动。

## 关于发版

源码包只包含程序本身（`dmdp.py`、`dmdp_engine.py`、`build.py`、`packaging/`、文档与许可）。
发版用的上传脚本和给接手者看的内部说明**不随源码分发**，由维护者单独保存。

发版时请确保 release 附件里有一个准确命名为 `DMDP.exe` 的文件 —— 程序自更新按这个名字找直链。
