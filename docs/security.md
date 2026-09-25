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
