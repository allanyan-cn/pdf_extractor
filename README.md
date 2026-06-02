# PDF Extractor

一个基于规则的 PDF 结构化内容提取工具，用于从可提取文本的 PDF 中定位并提取文本、数值和简单表格，同时保留来源页码与坐标信息。

当前项目处于 V1 骨架阶段。目录、依赖、示例规则和基础环境测试已经准备完成，具体提取逻辑仍在开发中。

## V1 目标

- 使用 PyMuPDF 提取 PDF 文本块、坐标和 TOC。
- 有 TOC 时按 TOC 划分章节，无 TOC 时使用简单规则识别标题。
- 使用 SQLite FTS5 `trigram` tokenizer 建立段落级索引。
- 根据关键词和章节范围定位段落。
- 根据规则提取文本、简单数值和单页简单表格。
- 使用 pdfplumber 提取表格。
- 将 OpenAI SDK 封装为可选 LLM fallback，不作为主流程依赖。
- 输出结构化结果及来源页码、bbox 坐标。

V1 不处理 OCR、纯扫描件 PDF、图片文字识别、复杂跨页表格、Web UI 或复杂规则 DSL。

## 环境要求

- Python `>=3.11`
- SQLite 需要支持 FTS5 和 `trigram` tokenizer

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
# 核心运行依赖，包含 PyMuPDF 和 pdfplumber
python -m pip install -e .

# 开发和测试依赖
python -m pip install -e ".[dev]"

# 可选 LLM fallback
python -m pip install -e ".[llm]"
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
  example_rule.json
  run_extract.py

tests/
```

完整设计约束见 [`AGENTS.md`](AGENTS.md)。

## CLI

计划提供以下入口：

```bash
python examples/run_extract.py \
  --pdf examples/sample.pdf \
  --rules examples/example_rule.json \
  --output examples/output.json
```

CLI 提取流程尚未实现。当前执行入口会明确提示该状态。

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
