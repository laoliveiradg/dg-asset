# ImageDownloaderLocal

Aplicação desktop local para Windows para automatizar o download de imagens usadas em projetos.

## Etapa atual

Etapa 06B: preparação da fila e sessões locais por Google Chrome gerenciado. A aplicação permite:

- arrastar ou selecionar apresentações `.pptx`;
- colar texto com uma ou várias URLs;
- analisar entradas automaticamente em worker Qt;
- visualizar providers, itens não suportados e origens consolidadas;
- limpar o lote e começar outro;
- abrir Assetway, Shutterstock e Envato no Chrome real com profiles separados e persistentes;
- conectar somente à instância própria pelo Chrome DevTools Protocol em localhost.

O parsing e a classificação são locais. Sites só são acessados quando o usuário abre um provider. O login é manual; senhas não são capturadas ou armazenadas. Não há downloads, scraping, ZIP ou automação de sites.

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

O texto colado é analisado após um debounce de 300 ms. Profiles Chrome vivem em `runtime/chrome_profiles/` e não são versionados. A sessão permanece não verificada até existir validação segura específica do provider. A antiga implementação QtWebEngine está mantida apenas como legado inativo.
