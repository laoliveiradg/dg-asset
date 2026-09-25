# ImageDownloaderLocal

Aplicação desktop local para Windows para automatizar o download de imagens usadas em projetos.

## Etapa atual

Etapa 06: preparação da fila e sessões locais com login manual. A aplicação permite:

- arrastar ou selecionar apresentações `.pptx`;
- colar texto com uma ou várias URLs;
- analisar entradas automaticamente em worker Qt;
- visualizar providers, itens não suportados e origens consolidadas;
- limpar o lote e começar outro;
- abrir Assetway, Shutterstock e Envato em áreas QtWebEngine com profiles separados e persistentes.

O parsing e a classificação são locais. O navegador só acessa o site quando o usuário abre um provider. Senhas não são capturadas ou armazenadas; não há downloads, scraping, ZIP ou automação de sites.

## Organização

- src/image_downloader: código principal do pacote.
- docs: documentação de arquitetura, regras, segurança e performance.
- tests: testes automatizados das etapas e da integração da UI.
- runtime: diretório para dados locais e temporários, ignorado pelo Git.

## Como executar

```bash
python -m venv .venv
. .venv/bin/activate  # Linux/macOS
# ou .venv\Scripts\activate  # Windows PowerShell
pip install -e .[dev]
python -m image_downloader
```

O texto colado é analisado após um debounce de 300 ms. Profiles vivem em `runtime/browser_profiles/` e não são versionados. O status permanece “Não verificada” até existir validação segura específica do provider.
