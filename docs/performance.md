# Desempenho

## Objetivos

- minimizar o tempo total entre entrada dos arquivos ou links e ZIP pronto;
- garantir que a UI nunca congele;
- permitir processamento independente em concorrência;
- classificar URLs localmente sempre que possível;
- evitar chamadas de rede desnecessárias;
- reaproveitar sessões e conexões quando apropriado;
- permitir política futura de concorrência por provider;
- adaptar comportamento com base em rede e hardware;
- evitar uma quantidade fixa arbitrária de downloads simultâneos;
- instrumentar as etapas do processo;
- comparar futuramente o processo manual com o automatizado.

## Etapas a medir

A arquitetura futura deve registrar tempos para:

- leitura de entradas;
- extração de URLs;
- classificação;
- autenticação;
- preparação;
- downloads;
- compactação;
- tempo total.

## Regras de arquitetura

- a interface deve realizar apenas orchestration leve e feedback visual;
- o processamento pesado deve ocorrer em workers ou filas separadas;
- a classificação local deve preceder qualquer tentativa de rede;
- sessões autenticadas devem ser reaproveitadas quando tecnicamente apropriado;
- inicialização de navegador e processos caros deve ser evitada quando possível;
- o design futuro deve permitir escalonamento por provider e por disponibilidade de rede.

## Motor de fila (Etapa 04)

O motor mantém índices por `item_id` e URL normalizada para consulta e deduplicação próximas de O(1), e preserva ordem de inclusão com o dicionário ordenado por linguagem. Um `RLock` protege operações públicas para permitir acesso futuro por workers sem introduzir scheduler nesta etapa. O resumo varre os itens do lote em O(n), suficiente para os lotes pequenos esperados.

A métrica local desta etapa mede somente o tempo de construção da fila para lotes sintéticos de 2, 10, 20 e 100 itens. Não mede rede, browser, download ou ZIP.

## Interface e análise de entrada (Etapa 05)

Extração de PPTX/texto, classificação e atualização do `QueueManager` rodam em `QRunnable` via `QThreadPool`; os resultados chegam à UI por conexão Qt enfileirada. O pool limita a duas tarefas de entrada simultâneas. Isso não é concorrência de downloads nem scheduler.

O editor de texto usa debounce de 300 ms. A geração do lote e a geração do texto impedem commit de análise obsoleta; limpar invalida pedidos ativos. `AnalysisResult` registra tempo total de análise, extração, classificação, commit da fila e latência até o slot de atualização da UI. A latência de texto inclui o debounce; o tempo de análise não.

## Sessões QtWebEngine (Etapa 06, legado)

Profiles, pages e views são criados e manipulados na thread principal. O tempo local registra criação de profile e apresentação do diálogo até `showEvent`; não inclui latência de rede nem carregamento completo do site. A limpeza do profile antigo usa `QThreadPool` somente depois do encerramento dos objetos QtWebEngine. Memória é reportada apenas como aproximação do processo principal quando a medição nativa do Windows estiver disponível; processos renderer não são agregados.

Medição local em Windows, Python 3.14/PySide6 6.11.2, offscreen, sem navegar além da página vazia:

| Provider | Criação do profile | Diálogo apresentado |
| --- | ---: | ---: |
| Assetway | 6.357 ms (inicialização fria do Chromium) | 146 ms |
| Shutterstock | 79 ms | 85 ms |
| Envato Elements | 62 ms | 78 ms |

O working set do processo principal passou de 48,7 MiB para 123,2 MiB (+74,5 MiB) após abrir um profile e diálogo. Esse número não inclui processos renderer e é somente uma aproximação; não foi adicionada dependência para medir memória.

## Chrome gerenciado (Etapa 06B)

O runtime instrumenta localização/versão do Chrome, preparação do profile, criação de processo, espera por `DevToolsActivePort`, conexão CDP, abertura do target e encerramento. Startup e CDP rodam em workers para que a thread da UI não aguarde o processo. A medição funcional usa `about:blank`; o carregamento dos providers fica para validação manual.

## Download único Assetway (Etapa 07A)

Cada worker registra separadamente navegação/abertura do ativo, localização da ação, descoberta de qualidade, início do download, transferência, validação do arquivo e tempo total. Os workers são encadeados sequencialmente fora da thread UI; ainda não há paralelismo. O processamento Assetway usa Chrome `BACKGROUND_HEADED` minimizado e troca para `INTERACTIVE` somente para autenticação, fechando antes o processo próprio para reutilizar o mesmo profile com segurança. Detecção assistida Shutterstock e criação do ZIP também executam em workers.

## Limites da etapa atual

Esta etapa não implementa scheduler avançado, benchmark de rede, downloads reais, ou otimização de concorrência operacional.

## Métricas locais desta etapa

O motor de entrada registra métricas leves e locais para apoiar medição futura sem depender de redes ou benchmark externo. As métricas incluem:

- tempo de leitura do arquivo ou do texto;
- tempo de extração de URLs;
- número de ocorrências encontradas;
- número de URLs únicas após consolidação;
- contagem por tipo de origem quando relevante.

Essas informações são mantidas no módulo de entrada e não exigem infraestrutura extra. Elas servem de base para comparar mais tarde a etapa manual com o processo automatizado sem introduzir overhead de produção.
