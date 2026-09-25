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

A Etapa 06 implementa somente acesso manual aos sites em QtWebEngine, com profile persistente separado por provider e limpeza seletiva. Não há captura de credenciais, autenticação automática, scraping, downloads, seleção de qualidade, ZIP ou automação de sites.
