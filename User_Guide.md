# PDF Extractor User Guide

本文档面向规则编写者和工具使用者，详细说明 PDF Extractor 的 CLI、规则字段、定位逻辑、提取类型、表格策略和输出格式。

## 1. 适用范围

PDF Extractor 用于从可直接提取文本的 PDF 中抽取结构化内容。典型场景包括：

- 财务报告中的数值、百分比、日期、时间和表格。
- 年报、审计报告、业务报告中的指定段落。
- 根据章节、页面标题、关键词和表格行列定位目标字段。

当前不支持：

- OCR。
- 纯扫描件 PDF。
- 图片中的文字识别。
- Web UI。
- 复杂规则 DSL。

## 2. 安装和运行

创建虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

运行示例：

```bash
python examples/generate_sample_pdf.py

python examples/run_extract.py \
  --pdf examples/sample.pdf \
  --rules examples/sample_rule.json \
  --output examples/output.json
```

处理自己的 PDF：

```bash
python examples/run_extract.py \
  --pdf /path/to/report.pdf \
  --rules /path/to/rules.json \
  --output /path/to/output.json
```

CLI 参数：

| 参数 | 说明 |
| --- | --- |
| `--pdf PDF` | 输入 PDF 路径 |
| `--rules RULES` | JSON 规则文件路径 |
| `--output OUTPUT` | 输出 JSON 路径 |
| `--llm-table-fallback` | 启用 OpenAI 多模态表格能力 |
| `--table-llm-model MODEL` | 多模态表格使用的模型 |

### 2.1 查看 PDF 实际目录

当阅读器中显示的页面标题与 PDF 内嵌目录标题不一致时，可以直接打印
outline/bookmarks 中保存的完整目录：

```bash
python -m pdf_extractor.utils.show_toc /path/to/report.pdf
```

输出按目录层级缩进，并显示每个条目的目标页码。该工具只显示 PDF 内嵌目录；
没有内嵌目录时不会根据页面文本生成推测结果。

## 3. 规则文件结构

规则文件必须是一个包含 `rules` 数组的 JSON 对象：

```json
{
  "rules": [
    {
      "id": "net_income_value",
      "name": "提取净收入金额",
      "scope": "Financial statements",
      "within_heading": "Consolidated income statement",
      "keywords": ["Net interest income"],
      "extract_type": "number",
      "target": "Net interest income",
      "priority": 10
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 规则唯一标识，同一文件内不可重复 |
| `name` | 是 | 便于阅读的规则名称 |
| `extract_type` | 是 | `text`、`value`、`percentage`、`number`、`date`、`time` 或 `table` |
| `target` | 是 | 需要提取的业务字段，用于候选选择 |
| `scope` | 否 | 限制章节范围。省略或设为 `null` 时搜索整个 PDF |
| `within_heading` | 否 | 在章节内或全文中定位页面标题、表标题等锚点 |
| `keywords` | 否 | 用于定位候选段落的关键词列表。可省略或设为 `[]` |
| `table_selector` | 否 | 在候选表格中选择特定行列单元格。不能与 `extract_type: "table"` 同用 |
| `normalization` | 否 | 仅支持 `number`、`percentage`、`date`、`time` |
| `table_strategy` | 否 | 仅支持 `extract_type: "table"`。`auto`、`local` 或 `llm`，默认 `auto` |
| `llm_input` | 否 | 仅支持 `extract_type: "table"` 且 `table_strategy` 非 `local`。`page_image` 或 `text`，默认 `page_image` |
| `priority` | 否 | 执行优先级，数值越小越先执行，默认为 `0` |

## 4. 定位逻辑

规则按以下顺序逐级缩小候选范围：

1. `scope`：章节范围。
2. `within_heading`：章节内或全文中的页面标题/表标题锚点。
3. `keywords`：在前两级范围内用关键词定位段落。
4. `table` / `table_selector`：在候选段落所在页及相邻页定位整表或表格单元格。

这些层级都可以缺失。常见组合：

- 只有 `scope`：在整个章节内提取。
- `scope + within_heading`，但 `keywords: []`：直接使用 heading 后 3 页作为候选区域。
- `within_heading + table_selector`，但没有 `keywords`：从 heading 附近的表格中按行列抽单元格。
- 只有 `table_selector`：从全文候选表格中按 `table_index`、行、列抽单元格。

只有在 `keywords` 非空时，执行器才会调用全文索引；否则不会返回 `keywords_not_found`。

### 4.1 Scope

章节标题唯一时，可以直接使用标题：

```json
{
  "scope": "第三节 财务表现"
}
```

章节标题重复时，应使用完整路径：

```json
{
  "scope": "第二章 > 第三节 财务表现"
}
```

路径分隔符支持 `>`、`/` 和 `::`。标题重复但 scope 不完整时，工具不会猜测章节，而是返回 `scope_ambiguous` diagnostics。

`scope` 匹配会忽略标题首尾或内部多余的空白，以及 PDF 中常见的不可断空格、
零宽空格等不可见字符。诊断和提取结果仍保留 PDF 中的原始章节标题。

提取结果会同时返回 `section_title` 和 `section_path`，便于核对实际命中的章节层级。

### 4.2 Within Heading

当 PDF 目录只包含上层章节，而目标内容是页面标题或表标题时，可以用 `within_heading` 作为锚点：

```json
{
  "scope": "Financial statements",
  "within_heading": "Consolidated income statement",
  "keywords": ["Net interest income"]
}
```

执行器会优先在 `scope` 对应章节内找 heading。如果章节边界来自 PDF TOC 且过短，会退到该 scope 起始页之后继续找更像标题的段落。关键词搜索结果只保留 heading 之后 3 页内的段落，用于避开同名字段在前文摘要、回顾章节或目录中的命中。

## 5. 提取类型

### 5.1 Text

用于返回匹配段落原文：

```json
{
  "id": "risk_text",
  "name": "提取风险提示段落",
  "scope": "风险因素",
  "keywords": ["流动性风险"],
  "extract_type": "text",
  "target": "流动性风险说明"
}
```

文本结果使用段落 bbox。

### 5.2 Value

`value` 是兼容型泛数值，会根据 `target` 在金额、百分比、数量和普通数字之间选择候选：

```json
{
  "id": "net_income_value",
  "name": "提取净收入金额",
  "scope": "第二章 > 第三节 财务表现",
  "keywords": ["净收入"],
  "extract_type": "value",
  "target": "净收入金额"
}
```

`value` 只返回 PDF 原始字符串，不参与标准化。也就是说 `normalization` 对 `value` 无效，并且规则校验会拒绝这种配置。

支持示例：

```text
净收入为 12.5 亿元
营业利润达到 RMB 3,200 million
净利润为 $3,200
净利润为 (1,234 万元)
同比增长 8.6%
资本充足率提高 25 bps
员工人数为 120 人
测量值为 2.5e6
```

### 5.3 Number 和 Percentage

需要避免类型混淆时，优先使用更明确的细分类型：

```json
{
  "id": "growth_rate",
  "name": "提取同比增长率",
  "keywords": ["同比增长"],
  "extract_type": "percentage",
  "target": "同比增长率"
}
```

```json
{
  "id": "customer_count",
  "name": "提取客户数量",
  "keywords": ["客户数量"],
  "extract_type": "number",
  "target": "客户数量"
}
```

`percentage` 只返回百分比、百分点和 bps 等比例类候选。`number` 只返回不带货币、单位、日期、时间和百分号的普通数字。

简单值结果会返回：

- `value`：PDF 原文字符串，例如 `5,955`、`(1,234)`、`8.6%`。
- `normalized_value`：仅当 `extract_type` 为 `number`、`percentage`、`date` 或 `time` 时返回净化值。

数字和百分比净化会移除千分位和百分号。括号数值默认按会计口径转为负数，例如 `(1,234)` -> `-1234`。如果业务语义不是负数，可以用 `normalization.parentheses` 配置：

```json
{
  "id": "note_number",
  "name": "提取括号编号",
  "keywords": ["编号"],
  "extract_type": "number",
  "target": "编号",
  "normalization": {
    "parentheses": "positive"
  }
}
```

`parentheses` 可选值：

| 值 | 说明 |
| --- | --- |
| `negative` | 默认，`(1,234)` -> `-1234` |
| `positive` | `(1,234)` -> `1234` |
| `preserve` | `normalized_value` 保留 `(1,234)` |

### 5.4 Date 和 Time

日期和时间可以使用独立类型：

```json
{
  "id": "report_date",
  "name": "提取报告日期",
  "keywords": ["报告日期"],
  "extract_type": "date",
  "target": "报告日期"
}
```

```json
{
  "id": "meeting_time",
  "name": "提取会议时间",
  "keywords": ["会议时间"],
  "extract_type": "time",
  "target": "会议时间"
}
```

第一版只做空白标准化，不强制转换为 ISO 格式。

## 6. 表格提取

### 6.1 整表提取

```json
{
  "id": "income_table",
  "name": "提取利润表",
  "scope": "第二章 > 第三节 财务表现",
  "keywords": ["净收入", "利润"],
  "extract_type": "table",
  "target": "利润表"
}
```

本地表格处理顺序：

1. 使用 pdfplumber 提取有边框表格。
2. 如果没有候选表格，使用文本布局策略提取无边框表格。
3. 检查命中页和相邻续页，按表头、列数和位置拼接跨页表格。
4. 修复常见合并表头空白、首列纵向合并空白和纯空行。
5. 如果本地方法没有结果，再按需调用多模态 LLM fallback。

跨页表格结果会额外返回 `page_numbers` 和每页对应的 `bboxes`。

### 6.2 表格单元格提取

如果目标值位于某个表格的特定行列，可以在简单类型规则上增加 `table_selector`。此时 `extract_type` 不能是 `table`，只能是 `text`、`value`、`percentage`、`number`、`date`、`time` 等简单类型。

有表格标题、行标题和列标题时：

```json
{
  "id": "net_income_growth",
  "name": "提取净收入同比增长率",
  "scope": "Financial statements",
  "within_heading": "Consolidated income statement",
  "keywords": [],
  "extract_type": "percentage",
  "target": "Net interest income growth",
  "table_selector": {
    "table_title": "Consolidated income statement",
    "row_header": "Net interest income",
    "column_header": "YoY"
  }
}
```

没有表格标题或行列标题时，可以使用 1-based 序号：

```json
{
  "id": "meeting_time",
  "name": "提取会议时间",
  "keywords": ["Schedule table"],
  "extract_type": "time",
  "target": "Meeting time",
  "table_selector": {
    "table_index": 2,
    "row_index": 1,
    "column_index": 2
  }
}
```

`table_selector` 字段说明：

| 字段 | 说明 |
| --- | --- |
| `table_title` | 可选。匹配表格内容或同页表格上方标题段落 |
| `table_index` | 可选。候选表格序号，1-based，默认 `1` |
| `row_header` | 按行标题匹配行。若表格 rows 中缺失行名，会尝试从表格左侧同 y 文本恢复 |
| `row_index` | 按行序号选择行，1-based |
| `column_header` | 按首行列标题匹配列。若表格 rows 中缺失列名，会尝试从表格上方或内部同 x 文本恢复 |
| `column_index` | 按列序号选择列，1-based |

行定位必须提供 `row_header` 或 `row_index`；列定位必须提供 `column_header` 或 `column_index`。单元格结果的 `bbox_source` 为 `table_cell`，bbox 使用表格 bbox 按行列网格近似出的单元格坐标。

配置 `table_selector` 前，可以打印指定页面中本地识别到的表格结构：

```bash
python -m pdf_extractor.utils.show_table_structure \
  /path/to/report.pdf \
  --page 12
```

工具优先把 `--page` 解释为 PDF page label（印刷页码），再回退到物理页序号。
输出只包含每个逻辑表格的 `row_headers` 和 `column_headers`，可直接选择其中的
文本写入规则的 `row_header` 和 `column_header`。识别时会结合表格 rows 和页面
word 坐标，恢复 pdfplumber 可能遗漏在数值区域外的行标题。

### 6.3 表格策略

表格规则可以用 `table_strategy` 控制提取方式：

| 值 | 说明 |
| --- | --- |
| `auto` | 默认。先用本地方法，失败后在 CLI 开启 LLM 时调用 LLM |
| `local` | 只用本地方法，不调用 LLM |
| `llm` | 跳过本地表格解析，直接调用 LLM 重建表格 |

完全依赖 LLM 的表格规则示例：

```json
{
  "id": "income_table_llm",
  "name": "用 LLM 提取利润表",
  "scope": "Financial statements",
  "within_heading": "Consolidated income statement",
  "keywords": [],
  "extract_type": "table",
  "target": "Consolidated income statement",
  "table_strategy": "llm",
  "llm_input": "page_image"
}
```

`llm_input` 可选值：

| 值 | 说明 |
| --- | --- |
| `page_image` | 将候选页 PNG 图片发给多模态模型，适合复杂版式和无边框表格 |
| `text` | 只发送候选页已解析段落文本，成本更低，但依赖 PDF 文本层顺序 |

启用 LLM 表格能力：

```bash
export OPENAI_API_KEY="your-api-key"

python examples/run_extract.py \
  --pdf /path/to/report.pdf \
  --rules /path/to/rules.json \
  --output /path/to/output.json \
  --llm-table-fallback \
  --table-llm-model gpt-4.1-mini
```

LLM 只返回结构化 `rows`，不负责生成坐标。结果 bbox 使用本地关键词段落、`within_heading` 锚点或候选区域坐标，并用 `bbox_source: "table_llm"` 标记。

## 7. 输出格式

输出 JSON 包含：

- `file_path`：输入 PDF 路径。
- `results`：成功提取的结构化结果。
- `diagnostics`：每条规则的执行状态、候选段落数量和结果数量。

示例：

```json
{
  "file_path": "examples/sample.pdf",
  "results": [
    {
      "rule_id": "net_income_value",
      "value": "5,955",
      "source_text": "5,955",
      "page_number": 324,
      "bbox": {
        "x0": 425.19,
        "y0": 128.77,
        "x1": 481.88,
        "y1": 136.26
      },
      "confidence": 0.8,
      "rule_name": "Extract net interest income",
      "extract_type": "number",
      "target": "Net interest income",
      "normalized_value": "5955",
      "section_title": "Financial statements",
      "section_path": ["Financial statements"],
      "bbox_source": "table_cell"
    }
  ],
  "diagnostics": [
    {
      "rule_id": "net_income_value",
      "status": "success",
      "message": "Extraction completed successfully.",
      "candidate_count": 32,
      "result_count": 1
    }
  ]
}
```

`bbox_source` 常见值：

| 值 | 说明 |
| --- | --- |
| `span` | 数值使用 word 级坐标 |
| `paragraph` | 使用关键词段落坐标 |
| `table` | 有边框表格坐标 |
| `table_text` | 无边框文本布局表格坐标 |
| `table_cross_page` | 跨页表格，每页坐标见 `bboxes` |
| `table_llm` | LLM 重建表格，坐标来自本地候选区域 |
| `table_cell` | 表格单元格坐标，按行列网格从表格 bbox 近似计算 |

## 8. Diagnostics

| 状态 | 说明 |
| --- | --- |
| `success` | 规则成功提取结果 |
| `scope_not_found` | scope 未匹配到章节 |
| `scope_ambiguous` | scope 匹配到多个章节，需要使用完整路径 |
| `within_heading_not_found` | within_heading 未匹配到页面标题或表标题锚点 |
| `location_not_found` | 未配置 keywords，且 scope/within_heading 后没有候选段落 |
| `keywords_not_found` | 没有段落命中关键词 |
| `value_not_found` | 命中段落，但没有提取到数值 |
| `percentage_not_found` | 命中段落，但没有提取到百分比 |
| `number_not_found` | 命中段落，但没有提取到普通数字 |
| `date_not_found` | 命中段落，但没有提取到日期 |
| `time_not_found` | 命中段落，但没有提取到时间 |
| `table_not_found` | 命中段落，但没有提取到表格 |
| `table_row_not_found` | 表格已定位，但没有匹配到行 |
| `table_column_not_found` | 表格和行已定位，但没有匹配到列 |
| `table_cell_empty` | 表格行列已定位，但单元格为空 |
| `table_cell_type_not_found` | 单元格已定位，但内容不符合 `extract_type` |
| `text_not_found` | 命中段落，但没有提取到文本 |

CLI 退出码：

| 退出码 | 说明 |
| --- | --- |
| `0` | 至少提取到一项结果 |
| `1` | 文件、规则格式或 PDF 解析失败 |
| `2` | 执行完成，但所有规则均无结果。仍会写出 diagnostics JSON |

## 9. Python API

也可以在 Python 代码中直接调用：

```python
from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.parser.pdf_parser import PDFParser
from pdf_extractor.rules.rule_executor import RuleExecutor
from pdf_extractor.rules.rule_loader import RuleLoader

document = PDFParser().parse("report.pdf")
rules = RuleLoader().load("rules.json")

with FTSIndexer() as indexer:
    indexer.build(document)
    report = RuleExecutor(indexer).execute_with_diagnostics(document, rules)

for result in report.results:
    print(result.to_dict())

for diagnostic in report.diagnostics:
    print(diagnostic.to_dict())
```

## 10. 测试

执行完整测试并生成详细报告：

```bash
python -m pytest -vv \
  --junitxml=reports/junit.xml \
  --cov=pdf_extractor \
  --cov-report=term-missing \
  --cov-report=html:reports/coverage
```

报告文件：

- `reports/junit.xml`
- `reports/coverage/index.html`
