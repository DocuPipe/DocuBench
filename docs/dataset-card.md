# Dataset Card

## Dataset Summary

DocuBench is a 72-document benchmark for schema-guided structured extraction. Each example contains a source document, a JSON Schema, and a hand-verified JSON label. The task is to extract the labeled structured data from the document according to the schema.

## Composition

- 72 documents
- 72 schemas
- 72 labels
- 10 file types: PDF, JPEG, PNG, TIFF, XLSX, CSV, XML, TXT, DOCX, HTML
- 12 languages/scripts: English, Hebrew, Japanese, Chinese, Arabic, French, German, Portuguese, Dutch, Italian, Spanish, Hindi/Devanagari

Documents cover invoices, statements, utility bills, annual reports, payslips, purchase orders, waybills, healthcare forms, engineering drawings, insurance declarations, dictionaries, directories, auction catalogs, government registers, spreadsheets, XML, CSV, text, and HTML.

## Motivation

Many document extraction evaluations focus on single-page, flat, or QA-style tasks. DocuBench focuses on end-to-end structured extraction into realistic JSON shapes, including arrays, nested objects, multipage context, non-Latin scripts, and non-PDF inputs.

## Collection And Labeling

Documents were selected from public sources, vendor sample documents, government publications, open datasets, and benchmark-authored synthetic files. Each document has a source and license record in [`SOURCES.md`](../SOURCES.md) and [`sources.json`](../sources.json).

Labels were authored for the benchmark and manually checked field by field against the source document.

## Intended Uses

- Evaluating document extraction systems
- Testing schema-guided extraction robustness
- Comparing parser or extraction workflows on public artifacts
- Regression testing extraction systems across file types and languages

## Out-Of-Scope Uses

- Training models on the test labels
- Claiming broad document AI superiority from the headline aggregate alone
- Evaluating privacy handling, security, or compliance controls
- Treating these 72 documents as representative of all enterprise documents

## Licensing

- Code is MIT licensed.
- Labels, schemas, benchmark-authored metadata, and benchmark-authored results are CC BY 4.0 unless stated otherwise.
- Source documents retain their original licenses or publication basis.

## Maintenance

Scoring changes, label corrections, document removals, or additions should be recorded in a changelog and reflected in the benchmark version.
