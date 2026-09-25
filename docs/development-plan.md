# Plano de desenvolvimento

O desenvolvimento será realizado em fases separadas. Cada fase deve ser autorizada de forma explícita antes do início do trabalho correspondente.

## 1. Fundação

- estrutura do projeto;
- ambiente virtual;
- configuração de pacotes;
- janela mínima funcional;
- logs básicos.

## 2. Motor de entrada

- entrada de arquivos e URLs;
- armazenamento inicial de itens.

## 3. Reconhecimento de providers

- detecção dos provedores;
- identificação de origem.

## 4. Fila

- normalização e deduplicação;
- fila interna unificada.

## 5. Interface funcional

- fluxos reais de entrada e visualização;
- progress feedback.

## 6. Sessões e autenticação

- sessão autenticada do usuário;
- armazenamento seguro local.

## 7. Primeiro provider completo

- solução completa para um provedor.

## 8. Validação de qualidade

- filtros de preview, watermark, resíduo e qualidade.

## 9. ZIP e destino

- empacotamento e escolha de destino.

## 10. Segundo provider

- expansão para um segundo provedor.

## 11. Terceiro provider

- expansão para um terceiro provedor.

## 12. Performance adaptativa

- concorrência e ajuste dinâmico.

## 13. Benchmark

- comparação com processo manual.

## 14. Robustez e recuperação

- tolerância a falhas e recuperação.

## 15. Empacotamento Windows

- geração de distribuição para Windows.

## 16. Testes em outras máquinas

- validação em ambiente diverso.

## Observação

Nenhuma fase pode avançar automaticamente para a próxima sem autorização e validação explícitas.

Etapas 01 a 05 foram concluídas. A Etapa 06 está autorizada e se limita à infraestrutura de sessões persistentes com login manual em QtWebEngine. A Etapa 07 não está autorizada por esta tarefa.
