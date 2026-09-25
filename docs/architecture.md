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
- A UI não deve depender diretamente de provider específico.

### Navegador

- `SessionManager` possui profiles persistentes independentes para Assetway, Shutterstock e Envato Elements.
- `ProfileFactory` resolve diretórios determinísticos sob `runtime/browser_profiles/<provider>/`.
- `BrowserDialog` apresenta o site em `QWebEngineView` com o profile correspondente; o usuário realiza login e 2FA diretamente no site.
- A camada não captura senha, cookies ou tokens e não depende da fila nem da lógica de download.

### Downloads

- Recebe instruções da fila.
- Gerencia validação da qualidade, seleção da melhor URL e armazenamento temporário.
- Deve ser orientado para segurança e rastreabilidade.

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
