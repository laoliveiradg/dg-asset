# AGENTS.md

## Regras permanentes

1. Trabalhar somente dentro deste repositório.
2. Ler este arquivo e a documentação relevante em /docs antes de alterar código.
3. Não implementar funcionalidades além da tarefa solicitada.
4. Não remover funcionalidades existentes sem justificativa e autorização explícita.
5. Não realizar grandes mudanças arquiteturais silenciosamente.
6. Antes de uma mudança estrutural importante, explicar:
   - problema;
   - solução;
   - arquivos afetados;
   - riscos;
   - impacto.
7. Não armazenar em código-fonte ou texto puro versionado:
   - senhas;
   - tokens;
   - cookies;
   - sessões;
   - credenciais.
8. A interface não pode executar operações demoradas na thread principal.
9. Leitura de PPT, rede, download, compactação e automação futura não podem congelar a interface.
10. Providers devem permanecer desacoplados da UI.
11. Entrada de PPT não pode depender dos módulos de download.
12. Assetway, Shutterstock e Envato devem possuir conectores independentes.
13. Todo provider deverá futuramente seguir um contrato comum.
14. URLs de arquivos e URLs coladas deverão futuramente alimentar a mesma fila interna.
15. Downloads duplicados devem ser evitados.
16. Qualidade máxima disponível é obrigatória.
17. Não aceitar como download válido:
   - preview;
   - thumbnail;
   - screenshot;
   - imagem com watermark;
   - versão reduzida quando houver versão superior.
18. Se a maior qualidade esperada não puder ser obtida, o item deve futuramente ser tratado como falha ou pendência, nunca substituído silenciosamente por baixa qualidade.
19. Desempenho deve ser mensurável.
20. Otimizações não devem ser consideradas válidas somente por percepção subjetiva.
21. Reutilizar sessões e conexões quando tecnicamente apropriado.
22. Evitar inicializações repetidas e desnecessárias de navegador.
23. Runtime, cache, downloads temporários, logs sensíveis, perfis de navegador e credenciais jamais devem ser versionados.
24. Toda alteração deve preservar os testes existentes.
25. Antes de concluir uma tarefa:
   - executar testes;
   - executar lint;
   - verificar imports;
   - verificar inicialização quando aplicável.
26. Não avançar automaticamente para a próxima etapa do desenvolvimento.

## Regras de etapa atual

- A Etapa 06B mantém Chrome próprio gerenciado por CDP local disponível para providers cuja política permita esse fluxo.
- A política centralizada por provider define Assetway como `AUTOMATED` e Shutterstock/Envato como `INTERACTIVE_REQUIRED`.
- A prioridade atual é entregar o MVP ponta a ponta: orquestração sequencial por provider, downloads validados, ZIP com sucessos e escolha de destino ao final.
- Assetway usa Chrome gerenciado `BACKGROUND_HEADED` por padrão; Shutterstock e Envato permanecem assistidos no navegador padrão. Não implementar paralelismo, stealth, bypass ou retry automático.
- Nunca usar profile pessoal no Chrome gerenciado, conectar a Chrome externo via CDP, copiar cookies ou encerrar processos que não sejam próprios.
- A navegação interativa pelo navegador padrão não autoriza leitura ou alteração do profile normal do usuário.
- Profiles gerenciados ficam separados por provider em runtime/chrome_profiles e nunca são versionados.
- O Chrome gerenciado usa porta DevTools efêmera vinculada a 127.0.0.1; Assetway usa `BACKGROUND_HEADED` minimizado por padrão e `INTERACTIVE` visível somente para login ou intervenção legítima, compartilhando o mesmo profile persistente.
- A implementação QtWebEngine anterior permanece somente como legado inativo temporário para rollback.
- Usar somente controles oficiais e visíveis do Assetway; se não for possível provar a maior qualidade, falhar sem fallback.
- Não automatizar login, acessar endpoints privados, fazer scraping fora da interação oficial ou usar técnicas stealth/anti-bot. Não insistir em headless quando ele altera o DOM real do provider.
- A aplicação não deve marcar sessões AUTHENTICATED sem validação específica confiável.

## Revisão e responsabilidade

- Qualquer alteração deve ser pequena, justificável e segura.
- Mudanças estruturais importantes requerem explicação clara antes da implementação.
- Nenhum agente deve introduzir dependências ou arquivos sensíveis sem necessidade.
