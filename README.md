# ImageDownloaderLocal

Aplicação desktop local para Windows para automatizar o download de imagens usadas em projetos.

## Etapa atual

Etapa 06C: política de execução por provider, mantendo a infraestrutura Chrome/CDP da 06B. A aplicação permite:

- arrastar ou selecionar apresentações `.pptx`;
- colar texto com uma ou várias URLs;
- analisar entradas automaticamente em worker Qt;
- visualizar providers, itens não suportados e origens consolidadas;
- limpar o lote e começar outro;
- abrir Assetway e Envato no Chrome gerenciado, com profiles separados e persistentes;
- abrir o acesso e itens Shutterstock no navegador padrão, sem CDP ou controle do profile pessoal;
- conectar o Chrome gerenciado somente à instância própria pelo Chrome DevTools Protocol em localhost.

O parsing e a classificação são locais. Shutterstock é `INTERACTIVE_REQUIRED`; Assetway e Envato são `UNVALIDATED`. Essa política não altera o estado `READY` da fila para providers conhecidos. Login e interação permanecem manuais. Não há downloads, scraping, ZIP, stealth ou automação de sites.

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

O texto colado é analisado após um debounce de 300 ms. Profiles Chrome gerenciados vivem em `runtime/chrome_profiles/` e não são versionados. Estratégias serão validadas provider por provider; o estado de sessão permanece conservador. A antiga implementação QtWebEngine está mantida apenas como legado inativo.
