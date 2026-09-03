# PaperTrans

PaperTrans is a local-first workspace for translating academic papers into
Japanese while preserving document structure. Official arXiv HTML is the
supported input path; digital-PDF import through Docling remains experimental.
Translation jobs are completed by a connected MCP client.

PaperTrans is currently distributed and operated as a **source checkout**. The
Python wheel and sdist contain only the core Python package and command-line
entry points; they are not a standalone distribution of the Next.js
application, repository Skills, or evaluation workers. Follow the
[repository setup instructions](https://github.com/tenxnet/PaperTrans#readme)
for the complete application.

- [English README](https://github.com/tenxnet/PaperTrans/blob/main/README.en.md)
- [Documentation](https://github.com/tenxnet/PaperTrans/tree/main/docs)
- [Security policy](https://github.com/tenxnet/PaperTrans/security/policy)
- [Changelog](https://github.com/tenxnet/PaperTrans/blob/main/CHANGELOG.md)
- [Issue tracker](https://github.com/tenxnet/PaperTrans/issues)

Project-owned package code is licensed under Apache-2.0. Dependencies, model
files, papers, generated translations, and separately distributed evaluation
workers retain their own licenses and obligations; see the
[dependency and distribution audit](https://github.com/tenxnet/PaperTrans/blob/main/docs/dependency-licenses.md).
