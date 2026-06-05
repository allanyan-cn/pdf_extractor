# PDF Extractor

一个基于规则的 PDF 结构化内容提取工具。它从可提取文本的 PDF 中定位章节、标题、关键词和表格，根据 JSON 规则抽取文本、数值、日期、时间、整表或表格单元格，并返回来源页码与坐标。

适合处理财务报告、业务报告和其他带明确结构线索的文本型 PDF。

详细规则、字段说明和高级用法见 [User_Guide.md](User_Guide.md)。

## 功能概览

- 使用 PyMuPDF 提取 PDF 文本块、word 坐标和 TOC。
- 有 TOC 时按 TOC 划分章节，无 TOC 时用基础标题规则识别章节。
- 使用 SQLite FTS5 `trigram` tokenizer 建立段落级索引，支持中文关键词检索。
- 支持 `text`、`value`、`number`、`percentage`、`date`、`time` 和 `table`。
- 支持按 `scope -> within_heading -> keywords -> table/table_selector` 逐级定位内容。
- 支持有边框表格、无边框文本表格、跨页表格、表格单元格提取。
- 可选 OpenAI 多模态模型重建复杂表格。
- 每条规则返回 diagnostics，便于定位无结果原因。

当前不处理 OCR、纯扫描件 PDF、图片文字识别、Web UI 或复杂规则 DSL。

## 环境要求

- Python `>=3.11`
- SQLite 支持 FTS5 和 `trigram` tokenizer

## 安装

创建并启用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

也可以使用 `pyproject.toml` 按需安装：

```bash
# 核心依赖
python -m pip install -e .

# 开发与测试依赖
python -m pip install -e ".[dev]"

# 可选 OpenAI 多模态表格能力
python -m pip install -e ".[llm]"
```

## 快速开始

生成示例 PDF：

```bash
python examples/generate_sample_pdf.py
```

执行提取：

```bash
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

## 最小规则示例

规则文件必须是包含 `rules` 数组的 JSON 对象：

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
      "target": "Net interest income"
    }
  ]
}
```

从表格特定单元格提取：

```json
{
  "rules": [
    {
      "id": "net_interest_income",
      "name": "Extract net interest income",
      "scope": "Financial statements",
      "within_heading": "Consolidated income statement",
      "keywords": [],
      "extract_type": "number",
      "target": "Net interest income",
      "table_selector": {
        "row_header": "Net interest income",
        "column_header": "2025"
      }
    }
  ]
}
```

更多规则字段、定位逻辑、表格策略和输出格式见 [User_Guide.md](User_Guide.md)。

## 可选 LLM 表格提取

启用 OpenAI 多模态表格 fallback：

```bash
export OPENAI_API_KEY="your-api-key"

python examples/run_extract.py \
  --pdf /path/to/report.pdf \
  --rules /path/to/rules.json \
  --output /path/to/output.json \
  --llm-table-fallback \
  --table-llm-model gpt-4.1-mini
```

也可以使用 LM Studio 这类 OpenAI-compatible 本地服务。在项目根目录创建 `.env`：

```bash
OPENAI_API_KEY=lm-studio
OPENAI_BASE_URL=http://localhost:1234/v1
TABLE_LLM_MODEL=your-loaded-model
```

然后在 LM Studio 中启动本地 server，并加载支持视觉输入的模型。如果只想发送解析后的候选页文本，可以在规则中设置 `"llm_input": "text"`；如果使用默认 `"page_image"`，本地模型必须支持 image input。

规则中可以用 `table_strategy` 控制表格提取方式：

- `auto`：默认，先本地提取，失败后可调用 LLM。
- `local`：只用本地方法。
- `llm`：跳过本地表格解析，直接调用 LLM。

`table_selector` 单元格规则也支持 `table_strategy`。当使用 `"table_strategy": "llm"` 时，LLM 会先重建目标表格 rows，程序再把 rows 渲染成带清晰边框的临时 PDF，由 pdfplumber 重新抽取后复用现有行列选择逻辑：

```json
{
  "id": "net_interest_income_cell",
  "name": "Extract net interest income cell",
  "scope": "2025 FINANCIAL STATEMENTS",
  "within_heading": "Consolidated Income Statement",
  "keywords": [],
  "table_selector": {
    "table_title": "Consolidated Income Statement",
    "row_header": "Net interest income",
    "column_header": "2025"
  },
  "extract_type": "number",
  "target": "Net interest income",
  "table_strategy": "llm",
  "llm_input": "page_image"
}
```

## Python API

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
```

## 测试

执行完整测试并生成报告：

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

## 项目结构

```text
pdf_extractor/
  parser/       PDF 解析、章节识别和段落构建
  indexer/      SQLite FTS5 段落索引
  rules/        规则结构、加载和执行
  extractor/    文本、数值、表格和可选 LLM 提取器
  models/       文档与提取结果模型
  utils/        bbox、日志和 LLM 连接工具

examples/
tests/
```

完整设计约束见 [AGENTS.md](AGENTS.md)。
