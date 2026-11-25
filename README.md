# cov-sum

零依赖 Python CLI：汇总 JaCoCo、Cobertura、Clover 和 lcov 覆盖率报告。看总体行/分支/方法覆盖率、低于阈值的文件，以及未覆盖的行号。全部在本机完成，不上传任何文件。

## 做什么

CI 跑完之后，覆盖率报告往往又长又碎：JaCoCo XML、Cobertura `coverage.xml`、Clover、lcov.info 结构各不相同，直接翻原始文件很难看出「哪几个文件把阈值拉下去了」。`cov-sum` 在本机解析报告，压成一页摘要。

它会：

- 自动识别 JaCoCo XML（含 `DOCTYPE` 和 XML 命名空间）、Cobertura XML、Clover / OpenClover XML、lcov / gcov 的 `SF:` / `DA:` 记录
- 统计行、分支、方法覆盖率；JaCoCo 还会给出指令（instruction）覆盖率
- 按文件列出覆盖率，默认先看最差的文件
- `--min 80` 检查所选指标是否达标，不达标以状态码 1 退出，方便放进 CI
- `--uncovered` 列出未覆盖的行号区间（例如 `21-23, 40`）
- `--metric line|branch|method|instruction` 决定排序和阈值用哪一项
- 扫描目录下的常见文件名（`jacoco.xml`、`coverage.xml`、`clover.xml`、`lcov.info` 等）；`-r` 递归
- 支持 stdin（`-` 或管道）；`--json` 便于脚本处理；`--strict` 在读失败或没有数据时以状态码 1 退出

同一路径出现在多份报告里时，按行号做并集：某一行在任一报告里被命中，就视为已覆盖。

## 要求

- Python 3.8+（只需标准库）

## 怎么安装 / 打开

克隆后直接运行，无需安装依赖：

```bash
git clone https://github.com/temptation2017/cov-sum.git
cd cov-sum
python3 cov_sum.py --help
```

也可以拷贝 `cov_sum.py` 到 `PATH` 里的目录，并改名为 `cov-sum`：

```bash
chmod +x cov_sum.py
cp cov_sum.py ~/.local/bin/cov-sum
```

## 怎么用

```bash
python3 cov_sum.py target/site/jacoco/jacoco.xml
python3 cov_sum.py coverage.xml --min 80 --uncovered
python3 cov_sum.py lcov.info --metric branch --top 10
python3 cov_sum.py reports -r --json
cat clover.xml | python3 cov_sum.py -
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--min PCT` | 所选指标低于该百分比时退出码为 1 |
| `--metric` | 排序和 `--min` 使用的指标，默认 `line` |
| `--uncovered` | 在文件下列出未覆盖行号 |
| `--top N` | 列表显示多少个文件，`0` 表示全部，默认 15 |
| `-r` | 递归扫描目录 |
| `--format` | 强制指定格式，默认自动识别 |
| `--json` | 输出 JSON |
| `--strict` | 读失败或没有数据时退出码为 1 |

## 示例

仓库里的 `examples/jacoco.xml` 是一份虚构的商店服务报告（`com.example.shop`，不是真实项目数据）：

```text
$ python3 cov_sum.py examples/jacoco.xml --min 80 --uncovered
Coverage  jacoco  examples/jacoco.xml

  Lines              30/44   68.2%  [########....]
  Branches            7/14   50.0%  [######......]
  Methods             8/11   72.7%  [#########...]
  Instructions      84/130   64.6%  [########....]

Threshold  line >= 80.0%  FAIL

Below threshold (2)
   33.3%        5/15  [###.....]  com/example/shop/web/OrderController.java
           uncovered: 13-22
   71.4%       10/14  [######..]  com/example/shop/OrderService.java
           uncovered: 21-23, 40

Other files
  100.0%       10/10  [########]  com/example/shop/Cart.java
  100.0%         5/5  [########]  com/example/shop/util/Ids.java
```

退出码为 1，因为行覆盖率 68.2% 低于 80%。同样的数据在 `examples/coverage.xml`（Cobertura）、`examples/clover.xml` 和 `examples/lcov.info` 里也能解析。

运行测试：

```bash
python3 test_cov_sum.py
```

## 限制

- 只认 JaCoCo / Cobertura / Clover XML 和 lcov 文本，不解析 HTML 报告、JaCoCo CSV、istanbul `coverage-final.json` 或 IDE 的二进制索引
- 路径按报告里的写法展示，不会自动把 `com/example/shop/A.java` 和 `src/main/java/com/example/shop/A.java` 当成同一个文件
- 合并多份报告时，行覆盖按行号并集；分支和方法计数是相加，同一套件被算两次时可能会偏高
- 没有行号明细时，退回使用报告里的汇总计数，此时 `--uncovered` 列不出具体行
- 单文件超过 50 MB 会跳过

## 后续可改进

- 对比两次报告，标出覆盖率下降的文件和新增的未覆盖行
- 识别 istanbul / nyc 的 JSON，以及 OpenCover、dotCover
- 按包（package）再汇总一层，并支持把不同前缀的路径规范成同一相对路径

## 许可

MIT。见 [LICENSE](LICENSE)。
