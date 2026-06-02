# PDF Extractor

一个基于规则的 PDF 结构化内容提取工具。它从可提取文本的 PDF 中定位章节和段落，根据 JSON 规则抽取文本、数值或表格，并返回来源页码与坐标。

适合处理财务报告、业务报告和其他带明确关键词的文本型 PDF。

## 功能

- 使用 PyMuPDF 提取 PDF 文本块、word 坐标和 TOC。
- 有 TOC 时按 TOC 划分章节，无 TOC 时使用标题规则、字号和位置识别章节。
- 章节保存 `parent_id` 和完整 `path`，重复标题可使用完整路径精确定位。
- 使用 SQLite FTS5 `trigram` tokenizer 建立段落级索引，支持中文关键词检索。
- 支持 `text`、`value` 和 `table` 三类提取规则。
- 数值支持币种、货币符号、括号负数、中英文单位、百分比、基点、数量单位和科学计数法。
- 表格支持有边框、无边框、跨页拼接和常见合并单元格修复。
- 本地表格提取失败时，可以选择启用 OpenAI 多模态模型辅助重建表格。
- 每条规则都返回 diagnostics，便于定位无结果原因。

当前不处理 OCR、纯扫描件 PDF、图片文字识别、Web UI 或复杂规则 DSL。

## 环境要求

- Python `>=3.11`
- SQLite 支持 FTS5 和 `trigram` tokenizer

当前开发环境已经验证 SQLite FTS5 `trigram` 可以用于中文关键词子串检索。

## 安装

创建并启用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
```

安装完整开发环境：

```bash
python -m pip install -r requirements.txt
```

也可以使用 `pyproject.toml` 按需安装：

```bash
# 核心依赖：PyMuPDF 和 pdfplumber
python -m pip install -e .

# 开发与测试依赖
python -m pip install -e ".[dev]"

# 可选 OpenAI 多模态表格 fallback
python -m pip install -e ".[llm]"
```

## 快速开始

仓库中提供了一个可直接运行的示例。先生成包含 TOC、正文和表格的 PDF：

```bash
python examples/generate_sample_pdf.py
```

执行规则提取：

```bash
python examples/run_extract.py \
  --pdf examples/sample.pdf \
  --rules examples/sample_rule.json \
  --output examples/output.json
```

结果写入 `examples/output.json`。

## CLI 参数

```text
--pdf PDF                     输入 PDF 路径
--rules RULES                 JSON 规则文件路径
--output OUTPUT               输出 JSON 路径
--llm-table-fallback          本地表格提取失败时启用 OpenAI 多模态 fallback
--table-llm-model MODEL       多模态表格 fallback 使用的模型
```

处理自己的 PDF：

```bash
python examples/run_extract.py \
  --pdf /path/to/report.pdf \
  --rules /path/to/rules.json \
  --output /path/to/output.json
```

## 规则格式

规则文件必须是一个包含 `rules` 数组的 JSON 对象：

```json
{
  "rules": [
    {
      "id": "net_income_value",
      "name": "提取净收入金额",
      "scope": "第一章 > 第三节 财务表现",
      "keywords": ["净收入"],
      "extract_type": "value",
      "target": "净收入金额",
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
| `scope` | 否 | 限制章节范围。省略或设为 `null` 时搜索整个 PDF |
| `keywords` | 是 | 用于定位候选段落的关键词列表 |
| `extract_type` | 是 | `text`、`value` 或 `table` |
| `target` | 是 | 需要提取的业务字段，用于数值候选类型判断 |
| `priority` | 否 | 执行优先级，数值越大越先执行，默认为 `0` |

### Scope

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

提取结果会同时返回 `section_title` 和 `section_path`，便于核对实际命中的章节层级。

## 规则示例

### 提取文本

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

### 提取数值

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

数值结果优先返回 word 级 span bbox。无法精确定位时回退到段落 bbox。

### 提取表格

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

## 多模态表格 Fallback

多模态 fallback 默认关闭。它只在本地表格提取失败时调用，并且只发送关键词命中页和相邻续页的 PNG 图片。

LLM 只返回结构化 `rows`，不负责生成坐标。结果 bbox 使用本地关键词段落坐标。

先设置 OpenAI API Key：

```bash
export OPENAI_API_KEY="your-api-key"
```

然后执行：

```bash
python examples/run_extract.py \
  --pdf /path/to/report.pdf \
  --rules /path/to/rules.json \
  --output /path/to/output.json \
  --llm-table-fallback \
  --table-llm-model gpt-4.1-mini
```

## 输出格式

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
      "value": "RMB 3,200 million",
      "source_text": "Net income reached RMB 3,200 million.",
      "page_number": 1,
      "bbox": {
        "x0": 173.49,
        "y0": 193.18,
        "x1": 265.79,
        "y1": 208.29
      },
      "confidence": 0.9,
      "section_title": "Section 2 Results",
      "section_path": ["Chapter 1 Overview", "Section 2 Results"],
      "bbox_source": "span"
    },
    {
      "rule_id": "net_income_table",
      "value": [
        ["Item", "Amount"],
        ["Net income", "RMB 3,200 million"]
      ],
      "page_number": 1,
      "page_numbers": [1],
      "bboxes": [
        {
          "x0": 72.0,
          "y0": 250.0,
          "x1": 360.0,
          "y1": 320.0
        }
      ],
      "bbox_source": "table"
    }
  ],
  "diagnostics": [
    {
      "rule_id": "net_income_value",
      "status": "success",
      "message": "Extraction completed successfully.",
      "candidate_count": 2,
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
| `table_llm` | LLM 重建表格，坐标来自本地关键词段落 |

diagnostics 状态：

| 状态 | 说明 |
| --- | --- |
| `success` | 规则成功提取结果 |
| `scope_not_found` | scope 未匹配到章节 |
| `scope_ambiguous` | scope 匹配到多个章节，需要使用完整路径 |
| `keywords_not_found` | 没有段落命中关键词 |
| `value_not_found` | 命中段落，但没有提取到数值 |
| `table_not_found` | 命中段落，但没有提取到表格 |
| `text_not_found` | 命中段落，但没有提取到文本 |

CLI 退出码：

| 退出码 | 说明 |
| --- | --- |
| `0` | 至少提取到一项结果 |
| `1` | 文件、规则格式或 PDF 解析失败 |
| `2` | 执行完成，但所有规则均无结果。仍会写出 diagnostics JSON |

## Python API

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

## 目录结构

```text
pdf_extractor/
  parser/       PDF 解析、章节识别和段落构建
  indexer/      SQLite FTS5 段落索引
  rules/        规则结构、加载和执行
  extractor/    文本、数值、表格和可选 LLM 提取器
  models/       文档与提取结果模型
  utils/        bbox、日志和 LLM 连接工具

examples/
  generate_sample_pdf.py
  sample_rule.json
  run_extract.py

tests/
```

完整设计约束见 [`AGENTS.md`](AGENTS.md)。

## 测试

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

测试报告属于本地产物，不提交到版本控制。
