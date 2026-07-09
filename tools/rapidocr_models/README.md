本目录用于放置自定义 RapidOCR 离线模型配置。

当前默认方案使用 `rapidocr` Python 包内置的默认中英文模型，并由
`InvoiceCompiler.spec` 在打包时收集进 exe。若以后需要替换模型，可在本目录放置
`rapidocr.yaml` / `rapidocr.yml` / `config.yaml` / `config.yml`，程序会优先使用该配置。
