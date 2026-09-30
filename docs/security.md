# Segurança

## Objetivos

- evitar armazenamento de senha em texto puro;
- usar sessões persistentes locais quando apropriado;
- manter dados de autenticação fora do Git;
- minimizar exposição de tokens e cookies em logs;
- utilizar somente acessos e downloads autorizados pela conta do usuário;
- não depender de técnicas para contornar controles de acesso ou licença dos provedores.

## Diretrizes futuras

- credenciais, cookies e sessões devem ser protegidos no ambiente local;
- dados sensíveis devem permanecer fora do repositório versionado;
- logs devem conter somente eventos, não payloads, cookies ou tokens;
- funções de autenticação e acesso devem ser desacopladas da UI;
- o sistema deve respeitar direitos de uso e licença de terceiros.

## Sessões locais (Etapa 06)

- O usuário digita senha e completa 2FA diretamente no site dentro do QtWebEngine.
- O aplicativo não oferece campos de senha, autofill, captura de credenciais, exportação de cookies ou acesso a tokens/storage.
- Cada provider possui profile próprio em `runtime/browser_profiles/`; nenhum profile de Chrome ou outro navegador é reutilizado.
- Os dados persistentes são necessários para manter a sessão local e são excluídos do Git pelo padrão `runtime/`.
- Limpar acesso rotaciona somente a geração do provider selecionado. Arquivos que o Windows ainda mantém bloqueados ficam inacessíveis pela nova geração e têm a remoção repetida após reiniciar.
- A presença de cookies ou storage nunca define estado `AUTHENTICATED`. O validator atual permanece `UNVERIFIED` até existir critério confiável por provider.
- Logs registram provider, estado e tempos, nunca URL completa, senha, cookies, tokens, headers ou HTML.

QtWebEngine exige que seus profiles, pages e views vivam na thread principal. Workers podem remover arquivos de uma geração já encerrada, mas não podem manipular objetos QtWebEngine.

## Limites desta etapa

Não há validação definitiva de login, extração de assets, scraping, automação, download ou transferência de sessão de outro navegador.

## Chrome gerenciado (Etapa 06B)

- Somente o executável encontrado localmente é iniciado; o aplicativo nunca conecta a uma instância Chrome preexistente.
- `--user-data-dir` aponta exclusivamente para `runtime/chrome_profiles/<provider>/`; nenhum profile pessoal é lido, copiado ou alterado.
- `DevToolsActivePort` anterior nunca é reutilizado. Antes de iniciar, o runtime reconcilia processos pelo profile dedicado exato e remove somente estado transitório quando não resta processo usando esse profile.
- A porta CDP é efêmera e o browser recebe `--remote-debugging-address=127.0.0.1`. Tanto o endpoint HTTP quanto os WebSockets validam loopback; a consulta HTTP não usa proxies.
- Logs contêm provider, PID próprio, status e tempos. Não registram cookies, tokens, URLs de páginas, HTML ou formulários.
- O encerramento normal usa o handle `Popen`. Após crash/reinício, a recuperação exige correspondência de PID, identidade de criação, executável e `--user-data-dir` exato; enumeração por nome isolado jamais autoriza encerramento. Chrome pessoal ou com outro profile não é tocado.
- A presença de cookies nunca significa `AUTHENTICATED`; o estado continua `UNVERIFIED`.
- QtWebEngine permanece no código como legado inativo para rollback; a importação ativa do app foi testada para não carregar os módulos QtWebEngine.

## Política interativa por provider (Etapa 06C)

- Shutterstock usa `webbrowser.open` para uma navegação iniciada pelo usuário no navegador padrão; a aplicação não controla nem lê o profile desse navegador.
- A navegação interativa não inicia CDP, não conecta a Chrome externo, não copia sessão/cookies e não lê storage.
- Assetway é `AUTOMATED`. Envato é `INTERACTIVE_REQUIRED` e, como Shutterstock, abre no navegador padrão sem CDP ou acesso ao profile pessoal.
- Não se tenta mascarar automação nem contornar controles anti-bot; login, 2FA e ações do site permanecem manuais.
- A URL do item é passada sem reconstrução, preservando query e fragmento; abrir a URL não altera o estado da fila.

## Download único Assetway (Etapa 07A)

- O downloader acessa somente o CDP associado a um processo Chrome vivo que o `ChromeProcessManager` iniciou; não conecta a browsers externos.
- O usuário faz login manualmente no profile persistente gerenciado. O app não lê campos de senha, cookies, tokens ou storage e não contorna sessão expirada.
- O diretório CDP é criado por item/tentativa sob `runtime/downloads/`, que é ignorado pelo Git; nenhum arquivo é enviado à pasta Downloads pessoal.
- Logs registram provider, `item_id`, estado, qualidade curta, erro seguro, bytes e tempos. Não registram URL completa, endpoint de download, HTML ou conteúdo de autenticação.
- Só controles oficiais visíveis são acionados. Não há Selenium, Playwright, OCR, coordenadas, endpoints privados, stealth ou bypass anti-bot.
- O processamento Assetway usa Chrome oficial headed minimizado, sem mascarar automação. Login e 2FA usam modo interativo; a troca fecha somente o processo próprio e reutiliza o mesmo profile persistente do provider.
- Shutterstock não usa CDP: somente a pasta Downloads escolhida pelo navegador normal é observada durante o item atual; arquivos preexistentes e temporários são ignorados, e o resultado é copiado para o runtime sem apagar ou renomear o original.
