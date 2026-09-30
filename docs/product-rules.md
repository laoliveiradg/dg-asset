# Regras de produto

- A aplicação é local e destinada a uso no Windows.
- O foco principal é lidar com lotes pequenos, normalmente entre aproximadamente 2 e 20 imagens.
- O processo deve exigir poucos cliques.
- A análise automática deve ocorrer sempre que possível.
- PPTs e links colados devem convergir para a mesma fila interna.
- Downloads duplicados devem ser evitados.
- Feedback de progresso deve ser compreensível.
- Falhas não devem impedir que itens independentes concluam.
- O ZIP deve ser produzido a partir dos arquivos obtidos corretamente.
- Pendências devem ser apresentadas claramente ao usuário.
- A arquitetura futura deve permitir pasta padrão e escolha manual de destino.

## Política de execução (Etapa 06C)

A política de execução por provider é: Assetway = `AUTOMATED`, Shutterstock = `INTERACTIVE_REQUIRED` e Envato = `UNVALIDATED`. `INTERACTIVE_REQUIRED` significa que o provider é suportado, mas exige interação legítima do usuário; não significa bloqueio ou item não suportado. Itens conhecidos permanecem `READY`.

Shutterstock abre a URL do item no navegador padrão, sem CDP, controle do profile pessoal ou automação. A infraestrutura ChromeRuntime/CDP da Etapa 06B permanece disponível para providers que futuramente sejam validados para esse mecanismo. As estratégias de acesso e download serão avaliadas provider por provider.

Não há captura de credenciais, automação de login/download, scraping, downloads, seleção de qualidade, ZIP, stealth, técnicas para mascarar automação ou contorno de proteções anti-bot.

## Primeiro download Assetway (Etapa 07A)

Uma única ação global coleta todos os itens Assetway em `READY` e os processa sequencialmente. O fluxo usa a URL real de cada item, o Chrome gerenciado e os controles oficiais disponíveis à conta autenticada. Login e 2FA permanecem manuais. `QueueManager` controla `READY -> PROCESSING -> COMPLETED` ou `FAILED`; a falha de um item não interrompe os demais.

A tela principal mostra somente entrada, resumo compacto, ação global e progresso. A tabela fica recolhida em “Ver detalhes”; gerenciamento de login fica em “Acessos”. Diagnóstico DOM aparece apenas com `IMAGE_DOWNLOADER_DEV_TOOLS=1`. Assetway usa Chrome real minimizado `BACKGROUND_HEADED`; quando a sessão expira, somente esse provider é interrompido e a UI oferece login `INTERACTIVE` no mesmo profile. Shutterstock usa o navegador padrão e monitoramento assistido da pasta Downloads, sem CDP.

A opção oficial de maior qualidade deve ser comprovada antes de iniciar a transferência. Original/fonte vetorial (EPS, AI, SVG) tem precedência sobre raster reduzido; opções preview, thumbnail, watermark, arquivo incompleto, vazio, HTML ou formato incoerente não são válidas. Sem prova da melhor qualidade, o item falha como `quality_unverified`, sem fallback.

Arquivos validados ficam em `runtime/downloads/<batch_id>/<item_id>/attempt-<n>/`; ZIP temporário fica em `runtime/archives/<batch_id>/`. Após o processamento, o usuário escolhe a pasta final. Não há conversão, upscale, paralelismo, retry automático ou automação Envato. O fluxo Assetway foi validado em execução real com um EPS original e um JPEG original e sua política central é `AUTOMATED`. Envato permanece `UNVALIDATED`.
