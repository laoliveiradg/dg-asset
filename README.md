# ImageDownloaderLocal

Aplicação desktop local para Windows para automatizar o download de imagens usadas em projetos.

## Etapa atual

MVP em integração: entrada unificada, processamento sequencial por provider, validação e ZIP final. A aplicação permite:

- arrastar ou selecionar apresentações `.pptx`;
- colar texto com uma ou várias URLs;
- analisar entradas automaticamente em worker Qt;
- visualizar providers, itens não suportados e origens consolidadas;
- limpar o lote e começar outro;
- abrir Assetway e Envato no Chrome gerenciado, com profiles separados e persistentes;
- abrir o acesso e itens Shutterstock no navegador padrão, sem CDP ou controle do profile pessoal;
- conectar o Chrome gerenciado somente à instância própria pelo Chrome DevTools Protocol em localhost;
- processar sequencialmente todos os itens Assetway prontos por uma única ação global.
- executar Assetway em Chrome real gerenciado e minimizado, abrindo o modo interativo somente quando login for necessário;
- assistir downloads Shutterstock no navegador padrão e reunir resultados válidos em ZIP;
- manter detalhes da fila e gerenciamento de acessos fora do fluxo principal.

O parsing e a classificação são locais. Assetway é `AUTOMATED`; Shutterstock e Envato são `INTERACTIVE_REQUIRED`. Os três providers e o ZIP misto foram validados com ativos reais; qualidade sem comprovação resulta em falha. Login e downloads exigidos pelos providers interativos continuam manuais. Não há paralelismo, retry automático, scraping ou stealth.

## Organização

- src/image_downloader: código principal do pacote.
- docs: documentação de arquitetura, regras, segurança e performance.
- tests: testes automatizados das etapas e da integração da UI.
- `%LOCALAPPDATA%\Asset\runtime`: dados locais e temporários, fora do executável.

## Como executar

```bash
python -m venv .venv
. .venv/bin/activate  # Linux/macOS
# ou .venv\Scripts\activate  # Windows PowerShell
pip install -e .[dev]
python -m image_downloader
```

O texto colado é analisado após um debounce de 300 ms. Profiles Chrome e downloads temporários vivem sob `%LOCALAPPDATA%\Asset\runtime` e não são versionados. Estratégias serão validadas provider por provider; o estado de sessão permanece conservador. A antiga implementação QtWebEngine está mantida apenas como legado inativo.

## Build portátil para Windows

```powershell
pip install -e ".[build]"
.\scripts\build_windows.ps1
```

O artefato é criado em `dist\Asset.exe`, sem instalador e sem console.
