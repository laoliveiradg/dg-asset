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

## Escopo desta etapa

A Etapa 06C estabelece uma política de execução por provider: Shutterstock = `INTERACTIVE_REQUIRED`, Assetway = `UNVALIDATED` e Envato = `UNVALIDATED`. `INTERACTIVE_REQUIRED` significa que o provider é suportado, mas exige interação legítima do usuário; não significa bloqueio ou item não suportado. Itens conhecidos permanecem `READY`.

Shutterstock abre a URL do item no navegador padrão, sem CDP, controle do profile pessoal ou automação. A infraestrutura ChromeRuntime/CDP da Etapa 06B permanece disponível para providers que futuramente sejam validados para esse mecanismo. As estratégias de acesso e download serão avaliadas provider por provider.

Não há captura de credenciais, automação de login/download, scraping, downloads, seleção de qualidade, ZIP, stealth, técnicas para mascarar automação ou contorno de proteções anti-bot.
