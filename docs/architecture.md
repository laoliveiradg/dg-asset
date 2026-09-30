# Arquitetura

## Visão geral

A arquitetura do projeto foi desenhada para separar responsabilidades claras e permitir evolução incremental. A aplicação será mantida em uma estrutura modular, com UI isolada de lógica de processamento, e com providers que possam ser ampliados sem acoplamento ao shell visual.

## Módulos principais

### UI

- Responsável por exibir estado da aplicação e receber interações do usuário.
- Não deve executar tarefas de rede, download, parsing de PPT ou extração de URLs em sua thread principal.
- A UI deve consumir eventos e estados, e não conhecer detalhes internos de provider.

### Entrada de arquivos

- Recebe arquivos de origem como PPTX e outras entradas futuras.
- Extrai somente sinais de leitura e prepara dados brutos para normalização.
- Deve ficar separada do processamento de download para evitar acoplamento.

### Extração e normalização de URLs

- Converte entradas em uma representação interna comum.
- Remove duplicidades conceituais e normaliza itens antes da fila.
- Permite que URLs coladas e URLs extraídas de arquivos compartilhem a mesma fila.

### Fila

- O `QueueManager` é a fonte de verdade em memória para estado operacional.
- Cada item representa uma URL normalizada, suas ocorrências e a classificação do provider.
- Os índices por `item_id` e URL normalizada permitem consulta e deduplicação incremental.
- A ordem de inclusão é preservada; novas origens são mescladas sem mover o item.
- A camada não depende da UI e não executa downloads.

### Providers

- Fornecem um contrato comum para reconhecimento, autenticação, preparação e download.
- Devem manter conectores independentes para Assetway, Shutterstock e Envato.
- `ProviderRegistry` identifica o provider; `ProviderExecutionPolicy` decide separadamente seu modo de execução.
- A UI não deve depender diretamente de provider específico.

### Navegador

- `ChromeRuntime` e `ChromeProcessManager` iniciam e controlam somente processos `Popen` criados pelo aplicativo quando a política permite o Chrome gerenciado.
- `ChromeLocator` procura App Paths do Windows, instalações por usuário/Program Files e PATH.
- `ChromeProfileFactory` mantém profiles persistentes independentes sob `runtime/chrome_profiles/<provider>/`.
- O controle CDP usa porta efêmera, `DevToolsActivePort` do profile e endpoints restritos a `127.0.0.1`.
- O usuário autentica manualmente na janela interativa do Chrome; a UI não captura credenciais.
- Não há conexão com Chrome externo, reutilização de profiles pessoais, scraping ou downloads.
- A antiga implementação `browser/` baseada em QtWebEngine está inativa e preservada temporariamente para rollback.

### Política de execução por provider (Etapa 06C)

`ProviderExecutionMode` descreve a estratégia/capacidade do provider sem alterar o `QueueState`. `ProviderRegistry` continua responsável somente por classificação; a política centralizada mapeia `ProviderId` para `AUTOMATED`, `INTERACTIVE_REQUIRED`, `UNVALIDATED` ou `UNAVAILABLE`.

Política atual: Assetway é `AUTOMATED`, Shutterstock é `INTERACTIVE_REQUIRED`, Envato é `UNVALIDATED` e `UNKNOWN` é `UNAVAILABLE`. Um provider interativo continua suportado e seus itens permanecem `READY`. A UI consulta a política para escolher a ação, e o futuro scheduler deverá fazer o mesmo.

Para navegação interativa, `open_interactive_provider(provider, url)` envia a URL original/normalizada ao navegador padrão do Windows via biblioteca padrão. O sistema não conecta via CDP, não inicia Chrome gerenciado para essa ação e não acessa o profile normal. Query e fragmento são preservados. O ChromeRuntime da Etapa 06B permanece disponível para providers que venham a ser validados para esse mecanismo.

Não são adotadas técnicas para mascarar automação ou contornar proteções anti-bot. A estratégia de acesso/download será validada provider por provider.

### Downloads

- Recebe instruções da fila.
- Gerencia validação da qualidade, seleção da melhor URL e armazenamento temporário.
- Deve ser orientado para segurança e rastreabilidade.
- A Etapa 07A mantém o downloader isolado por item Assetway e adiciona orquestração sequencial na UI para todos os itens elegíveis; ChromeRuntime genérico não contém lógica específica desse provider.

### Compactação

- Empacota arquivos obtidos corretamente em ZIP.
- Recebe somente itens validados e que passaram por verificação de qualidade.

### Configurações

- Guarda opções locais do usuário, como pasta padrão e destino manual.
- Deve manter dados de configuração fora do Git e sem armazenamento em texto puro de credenciais.

### Segurança

- Evita a persistência de credenciais em código-fonte e arquivos versionados.
- Controla dados sensíveis de sessão e tokens.
- Reduz exposição em logs.

### Desempenho

- Mede tempos entre entrada, processamento, download e compactação.
- Deve permitir execução concorrente de etapas independentes.
- Permite comparar processo manual versus automatizado em fases futuras.

## Contrato conceitual de provider

Um provider futuro deverá suportar, no mínimo, etapas conceptuais como:

- detecção de origem;
- normalização da URL;
- verificação de autenticação;
- preparação do acesso;
- download do arquivo correto;
- validação de qualidade.

Essa etapa não implementa a rede ou os acessos de produção; apenas prepara a estrutura para isso.

## Motor de entrada (Etapa 02)

A Etapa 02 implementa o motor de entrada isolado da UI, da fila e dos providers. Ele aceita textos genéricos e arquivos PPTX e produz um modelo interno de URLs com origem preservada.

- o texto é extraído como ocorrências de origem `pasted_text`;
- o PPTX é lido diretamente do pacote OOXML sem depender de PowerPoint;
- cada ocorrência registra URL original, URL normalizada, tipo de origem, nome do arquivo, slide quando determinável e localização relevante;
- a deduplicação acontece por URL normalizada, sem perder as fontes originais;
- o módulo trabalha somente com dados locais e não acessa internet.

A normalização é intencionalmente conservadora para evitar quebrar URLs que dependem de query strings ou fragmentos. A representação original da URL permanece disponível em paralelo à forma normalizada.

## Motor de fila (Etapa 04)

`QueueManager` aceita `UrlRecord` ou classificações associadas da Etapa 03. Sem classificação associada, consulta o `ProviderRegistry` existente, sem duplicar detectors. Um hash SHA-256 integral da URL normalizada funciona como identificador opaco e estável durante o ciclo de vida do item; cada manager cria um `batch_id` em memória.

Itens conhecidos entram por `PENDING` e avançam para `READY`. `UNKNOWN` permanece no lote em `BLOCKED`, com `blocked_reason=unsupported_provider`; isso não impede os itens conhecidos. As transições são validadas pela máquina explícita em `queue/state_machine.py`. O retry manual permite `FAILED -> READY`, limpa o erro ativo e mantém o erro anterior em `error_history`. Cada entrada em `PROCESSING` incrementa o contador de tentativas.

O manager mantém a primeira ordem de inclusão e mescla ocorrências distintas quando a mesma URL normalizada chega em outra chamada. O resumo fornece contagens por estado e provider e progresso pelo total em estado terminal (`COMPLETED`, `FAILED` ou `BLOCKED`). A fila usa um `RLock` em torno de operações públicas porque será consultada por workers futuros; não há scheduler nem concorrência de downloads nesta etapa. Logs de estado contêm somente item_id, provider e estados, nunca URLs ou detalhes técnicos de erro.

## Interface funcional (Etapa 05)

`MainWindow` contém os controles de apresentação e delega o fluxo ao `InputController`. A janela usa `DropZone` para arquivos `.pptx`, um editor multilinha para texto, `QueueTableModel` para apresentar snapshots do `QueueManager` e uma área de resumo preenchida por `QueueSummary`. A tabela não exibe URLs completas nem query strings; mostra estado legível, provider, referência do ativo, origem e quantidade de origens.

Fluxo assíncrono:

```text
UI Thread -> InputController -> QThreadPool / QRunnable
		   -> Input Engine -> ProviderRegistry -> QueueManager
		   -> queued signal -> UI Thread / QueueTableModel
```

A leitura de PPTX, extração e consolidação de texto, classificação e commit na fila ocorrem no `InputWorker`. Widgets só são atualizados por slots Qt na thread principal. O texto usa debounce single-shot de 300 ms; a adição de arquivos começa imediatamente. Falhas são agregadas em aviso inline por lote, e a falha de um PPTX não interrompe os demais arquivos do mesmo pedido.

Cada pedido recebe `request_id`, geração do lote e, para texto, geração do conteúdo. O controller valida as gerações sob lock antes de alterar a fila e novamente antes de publicar o snapshot. Limpar incrementa as gerações, interrompe o debounce, descarta os caminhos já submetidos e substitui o `QueueManager`; workers que terminarem depois não podem repovoar o lote. Um caminho canônico (`Path.resolve`) evita reprocessar o mesmo PPTX no lote.

O feedback mostra o estágio atual, avisos por nome de arquivo, contagens diretamente do `QueueSummary` e tempo local até a atualização do resultado. Não existe ação de download nesta etapa.

## Sessões locais (Etapa 06)

`SessionManager` e `ProfileFactory` pertencem à camada `browser/`. Cada provider usa um ID e diretório de profile separados; o caminho estável contém gerações numeradas para permitir rotação de sessão sem mudar a associação do provider. Cookies, storage e cache permanecem sob o runtime ignorado pelo Git.

Estados disponíveis: `UNINITIALIZED`, `UNVERIFIED`, `AUTHENTICATED`, `LOGIN_REQUIRED`, `CHECKING` e `ERROR`. O validator padrão retorna `UNVERIFIED`; presença de cookies ou storage não é prova de login válido. A confirmação autenticada fica para um validator específico sustentado por evidência confiável.

As operações que criam ou manipulam `QWebEngineProfile`, `QWebEnginePage` e `QWebEngineView` executam na thread principal, conforme as restrições do QtWebEngine. Em contraste, parsing de PPTX continua nos workers da Etapa 05. A limpeza fecha os diálogos, aposenta a geração do provider, cria uma geração vazia e tenta remover a antiga em worker sem acessar objetos QtWebEngine. Se o Windows mantiver arquivos Chromium bloqueados, a exclusão é repetida ao iniciar a aplicação seguinte e a UI informa a pendência.

Senhas não são recebidas nem armazenadas pelo aplicativo; login e 2FA ocorrem diretamente na página web. Logs contêm provider, estado e tempos, nunca URLs de navegação, cookies, tokens, headers ou conteúdo de páginas. Nenhum comportamento de download é conectado ao navegador nesta etapa.

## Chrome gerenciado (Etapa 06B)

`ChromeSessionController` agenda abertura e limpeza em workers Qt; `ChromeRuntime` mantém status conservador e delega o processo a `ChromeProcessManager`. O executável é localizado sem instalação automática. Cada provider tem um diretório próprio dentro de `runtime/chrome_profiles/`. O runtime persiste em `managed_process.json` somente PID, executável, provider, profile, modo e identidade temporal do processo. Após reinício, um órfão só é recuperado quando PID, criação, executável e argumento exato `--user-data-dir` comprovam o uso do profile dedicado. PID reutilizado e Chrome com qualquer outro profile não são encerrados.

Argumentos comuns: `--user-data-dir`, `--remote-debugging-port=0`, `--remote-debugging-address=127.0.0.1`, `--no-first-run` e `--no-default-browser-check`. Assetway usa `ChromeMode.BACKGROUND_HEADED` com `--start-minimized`, pois o DOM real divergiu no headless; `INTERACTIVE` permanece visível para login. Os modos reutilizam o mesmo profile e nunca executam simultaneamente. Não são usados stealth, certificado ignorado ou user-agent falso.

O fechamento normal tenta `Browser.close`, aguarda o handle `Popen` e somente em timeout chama `terminate`/`kill` nesse handle específico. Na recuperação após crash, a árvore só é encerrada depois da validação conservadora do processo e do profile exato. Depois da saída confirmada, são removidos metadata e artefatos transitórios de instância; o profile persistente e os dados de sessão permanecem. A limpeza explícita de acesso continua sendo a única operação que apaga a pasta do provider selecionado.

Os módulos `browser/` QtWebEngine permanecem como legado de rollback, mas o import/fluxo ativo da aplicação não os carrega ou instancia. A aprovação funcional de compatibilidade do site depende do teste manual no Chrome real; o smoke automatizado usa apenas profile temporário e `about:blank`.

## Estratégia por provider (Etapa 06C)

O modo de execução não é estado da fila e não é duplicado em `QueueItem`; os consumidores consultam a política pelo provider. Providers conhecidos permanecem `READY`; somente `UNKNOWN` continua `BLOCKED` pela regra preexistente de provider não suportado.

Shutterstock exige interação legítima do usuário. A UI abre o item selecionado no navegador padrão usando `QueueItem.normalized_url`, sem modificar o item ou marcar conclusão. A ação de Acessos abre a página inicial da mesma forma. “Limpar acesso” não opera sobre o profile pessoal.

Assetway usa a navegação Chrome gerenciada com estratégia `AUTOMATED`; Envato permanece na infraestrutura gerenciada com estratégia `UNVALIDATED`. Shutterstock continua no navegador padrão, sem CDP. Os conectores permanecem separados e não usam scraping privado, extensão, Native Messaging, stealth ou bypass.

## Primeiro download Assetway (Etapa 07A)

`providers/assetway/` implementa a leitura semântica da página, descoberta da ação e opções de qualidade para um `QueueItem` por vez. A UI coleta automaticamente todos os Assetway `READY` e encadeia workers Qt sequencialmente; falhas isoladas não interrompem os itens seguintes. O ChromeRuntime continua genérico e o acesso CDP só é retornado quando `ChromeProcessManager` confirma processo próprio ativo.

O downloader abre `QueueItem.normalized_url` sem reconstrução, confirma host/referência do ativo e detecta login sem ler campos ou credenciais. Controles são encontrados por DOM visível, texto curto, role e atributos acessíveis. Não registra HTML nem URL completa.

Cada execução Assetway mantém uma sessão de página vinculada ao `target_id` criado pelo runtime e ao `QueueItem`; nenhum target é escolhido novamente durante o item. Após `Page.navigate`, o worker confirma rota, `document.readyState`, corpo útil e estabilidade da SPA. A descoberta progressiva aguarda controles dinâmicos e inspeciona o documento principal, frames acessíveis via contextos CDP e shadow roots abertos. Somente metadados sanitizados de estrutura e controles são registrados.

Antes do clique final, `Browser.setDownloadBehavior` aponta para `runtime/downloads/<batch_id>/<item_id>/attempt-<n>/`; eventos `Browser.downloadWillBegin` e `Browser.downloadProgress` confirmam a transferência. A fila só recebe `COMPLETED` depois de validar arquivo final estável, extensão/formato e bytes recebidos. `.crdownload`, vazio, HTML, formato inconsistente e ausência de evidência de qualidade são rejeitados. Preview, thumbnail, watermark e opções não reconhecidas não são selecionados.

Se os controles oficiais não demonstrarem uma qualidade original, vetorial ou alta explicitamente reconhecida, o item falha com `quality_unverified`. Essa etapa não converte, faz upscale, aceita screenshot, usa a pasta Downloads pessoal nem inicia automação de outros providers. O diagnóstico DOM fica oculto atrás de `IMAGE_DOWNLOADER_DEV_TOOLS=1`. O fluxo real foi validado com os assets 65507 (EPS original) e 81654 (JPEG original), incluindo processamento sequencial, verificação física e ZIP íntegro; por isso Assetway foi promovido para `AUTOMATED`. A suíte pytest continua isolada por fakes.

A experiência principal não usa a tabela como controle operacional: o usuário adiciona entradas, aciona “Baixar imagens” uma vez e escolhe o destino do ZIP ao final. A tabela fica oculta por padrão em “Ver detalhes”; acessos ficam em diálogo secundário. Seleção de linha permanece apenas para consulta e diagnóstico.

Shutterstock usa fluxo assistido sem CDP: a aplicação fotografa o estado da pasta Downloads, abre o item no navegador padrão e um worker detecta exatamente um arquivo novo, final e estável. O arquivo é validado e copiado para o diretório do item no runtime antes da transição para `COMPLETED`.

Ao final, `BatchArchiveService` compacta somente caminhos validados em `runtime/archives/<batch_id>/`. A criação roda fora da thread UI; depois dela o usuário escolhe a pasta e uma cópia com nome não conflitante é salva. Falhas individuais não impedem ZIP dos sucessos.
